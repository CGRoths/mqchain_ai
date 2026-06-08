from __future__ import annotations

import asyncio
import os
import shutil
from io import BytesIO
from pathlib import Path

os.environ["MQCHAIN_AI_DATABASE_URL"] = "sqlite:///./data/test_mqchain_ai.db"
os.environ["MQCHAIN_AI_STAGED_ARTIFACT_DIR"] = "./data/test_staged_artifacts"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.database import Base, SessionLocal, engine, init_db
from app.ingestion.github_source_resolver import github_blob_to_raw_url
from app.ingestion.intake_orchestrator import IntakeOrchestrator
from app.ingestion.source_adapters import _layout_wallet_rows_from_page
from app.ingestion.solidity_address_extractor import extract_solidity_deployment_table
from app.models.intake import AddressCandidate, AddressEvidence, SourceJob
from app.services.registry_service import RegistryPromotionService
from app.main import app


@pytest.fixture(autouse=True)
def reset_db() -> None:
    test_stage = Path("./data/test_staged_artifacts")
    if test_stage.exists():
        shutil.rmtree(test_stage)
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)
    if test_stage.exists():
        shutil.rmtree(test_stage)


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _xlsx_bytes(sheets: dict[str, list[list[str]]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    default = workbook.active
    workbook.remove(default)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _upload_preview(client: TestClient, filename: str, content: bytes, requested_source_type: str | None = None) -> dict:
    data = {}
    if requested_source_type:
        data["requested_source_type"] = requested_source_type
    response = client.post(
        "/api/intake/upload/preview",
        files={"file": (filename, content, "application/octet-stream")},
        data=data,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _url_preview(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_url: str,
    content: str,
    content_type: str = "text/html",
    input_method: str = "url",
) -> dict:
    async def fake_fetch(url: str):
        return content.encode("utf-8"), url, content_type

    monkeypatch.setattr("app.ingestion.intake_orchestrator.fetch_url_bytes", fake_fetch)
    response = client.post("/api/intake/preview", json={"input_method": input_method, "source_url": source_url})
    assert response.status_code == 200, response.text
    return response.json()


def test_health_routes_exist(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/health/db").json()["database"] == "ok"


def test_preview_has_required_fields_and_separate_warning_channels(client: TestClient) -> None:
    response = client.post(
        "/api/intake/preview",
        json={"input_method": "paste", "pasted_text": "Ethereum 0x1111111111111111111111111111111111111111"},
    )
    assert response.status_code == 200
    body = response.json()
    for field in (
        "preview_id",
        "requested_source_type",
        "final_source_type",
        "adapter_name",
        "fingerprint_confidence",
        "override_reason",
        "warnings",
        "fatal_errors",
    ):
        assert field in body
    assert isinstance(body["warnings"], list)
    assert isinstance(body["fatal_errors"], list)


def test_upload_preview_returns_preview_id_and_staged_artifact_id(client: TestClient) -> None:
    preview = _upload_preview(
        client,
        "wallets.csv",
        b"Entity,Network,Address\nBybit,Ethereum,0x1111111111111111111111111111111111111111\n",
    )
    assert preview["preview_id"]
    assert preview["staged_artifact_id"]
    assert preview["can_save_job"] is True


def test_save_from_preview_reuses_staged_artifact_and_snapshot(client: TestClient) -> None:
    preview = _upload_preview(
        client,
        "wallets.csv",
        b"Entity,Network,Address\nBybit,Ethereum,0x1111111111111111111111111111111111111111\n",
        requested_source_type="por_pdf",
    )
    response = client.post(
        "/api/intake/jobs",
        json={"preview_id": preview["preview_id"], "staged_artifact_id": preview["staged_artifact_id"]},
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["preview_id"] == preview["preview_id"]
    assert job["staged_artifact_id"] == preview["staged_artifact_id"]
    assert job["final_source_type"] == preview["final_source_type"]
    assert job["adapter_name"] == preview["adapter_name"]

    with SessionLocal() as db:
        stored = db.get(SourceJob, job["id"])
        assert stored is not None
        assert stored.fingerprint_json == preview["fingerprint"]
        assert stored.final_source_type == "csv_upload"
        assert stored.adapter_name == "excel_csv_adapter"


def test_run_uses_saved_fingerprint_and_adapter_not_recomputed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    preview = _upload_preview(
        client,
        "wallets.csv",
        b"Entity,Network,Address\nBybit,Ethereum,0x1111111111111111111111111111111111111111\n",
    )
    job = client.post("/api/intake/jobs", json={"preview_id": preview["preview_id"]}).json()

    def boom(*args, **kwargs):
        raise AssertionError("run must not refingerprint")

    monkeypatch.setattr("app.ingestion.source_fingerprint.SourceFingerprintService.fingerprint", boom)
    response = client.post(f"/api/intake/jobs/{job['id']}/run")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["adapter_name"] == preview["adapter_name"]
    assert body["final_source_type"] == preview["final_source_type"]
    assert body["extracted_candidates"] == 1
    assert body["reused_existing"] is False


def test_run_extraction_is_idempotent_and_evidence_counts_match(client: TestClient) -> None:
    preview = _upload_preview(
        client,
        "wallets.csv",
        (
            "Entity,Network,Address\n"
            "Bybit,Ethereum,0x1111111111111111111111111111111111111111\n"
            "Bybit,BSC,0x2222222222222222222222222222222222222222\n"
        ).encode(),
    )
    job = client.post("/api/intake/jobs", json={"preview_id": preview["preview_id"]}).json()

    first = client.post(f"/api/intake/jobs/{job['id']}/run")
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["extracted_candidates"] == 2
    assert first_body["reused_existing"] is False

    candidates = client.get(f"/api/intake/jobs/{job['id']}/candidates").json()
    evidence = client.get(f"/api/intake/jobs/{job['id']}/evidence").json()
    assert len(candidates) == 2
    assert len(evidence) == 2

    second = client.post(f"/api/intake/jobs/{job['id']}/run")
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["extracted_candidates"] == 2
    assert second_body["reused_existing"] is True

    candidates_after = client.get(f"/api/intake/jobs/{job['id']}/candidates").json()
    evidence_after = client.get(f"/api/intake/jobs/{job['id']}/evidence").json()
    assert len(candidates_after) == 2
    assert len(evidence_after) == 2
    assert len(evidence_after) == len(candidates_after)


def test_xlsx_requested_as_por_pdf_routes_to_excel_adapter(client: TestClient) -> None:
    content = _xlsx_bytes(
        {
            "Summary": [["skip"]],
            "MEXC": [
                ["Entity", "Network", "Address", "Wallet Label / Role", "Evidence Type"],
                ["MEXC", "Ethereum", "0x1111111111111111111111111111111111111111", "Reserve Wallet", "official_registry"],
            ],
        }
    )
    preview = _upload_preview(client, "cex_por_wallet_registry_indodax_full_partial_standardized.xlsx", content, "por_pdf")
    assert preview["requested_source_type"] == "por_pdf"
    assert preview["final_source_type"] == "excel_upload"
    assert preview["adapter_name"] == "excel_csv_adapter"
    assert preview["override_reason"] == "source_type_overridden_by_artifact_fingerprint"
    assert "source_type_overridden_by_artifact_fingerprint" in preview["warnings"]
    assert preview["profile"]["parsed_sheet_names"] == ["MEXC"]
    assert "Summary" in preview["profile"]["skipped_sheet_names"]
    assert preview["candidates_preview"][0]["status"] == "needs_review"


def test_csv_requested_as_por_pdf_routes_to_excel_adapter(client: TestClient) -> None:
    preview = _upload_preview(
        client,
        "wallets.csv",
        b"Entity,Network,Address\nBybit,Ethereum,0x1111111111111111111111111111111111111111\n",
        "por_pdf",
    )
    assert preview["final_source_type"] == "csv_upload"
    assert preview["adapter_name"] == "excel_csv_adapter"


def test_uniswap_docs_deployment_table_preview_save_run_evidence(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <html><body>
      <h1>Uniswap v2 deployments</h1>
      <table>
        <tr><th>Network</th><th>Factory Contract Address</th><th>V2Router02 Contract Address</th></tr>
        <tr><td>Mainnet</td><td>0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f</td><td>0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D</td></tr>
        <tr><td>Arbitrum</td><td>0xf1D7CC64Fb4452F05c498126312eBE29f30Fbcf9</td><td>0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24</td></tr>
      </table>
    </body></html>
    """
    preview = _url_preview(
        client,
        monkeypatch,
        source_url="https://developers.uniswap.org/docs/protocols/v2/deployments",
        content=html,
    )
    assert preview["final_source_type"] == "official_docs"
    assert preview["adapter_name"] == "web_docs_adapter"
    assert preview["profile"]["entity_name"] == "Uniswap"
    assert preview["profile"]["category"] == "dex"
    assert preview["profile"]["metadata"]["source_input_type"] == "docs_html_deployment_table"
    candidates = preview["candidates_preview"]
    assert len(candidates) == 4
    assert {candidate["suggested_role"] for candidate in candidates} == {"factory_contract", "router_contract"}
    assert {candidate["source_network"] for candidate in candidates} == {"Ethereum", "Arbitrum"}
    assert all(candidate["evidence_type"] == "official_docs_deployment" for candidate in candidates)
    assert all(candidate["status"] == "needs_review" for candidate in candidates)

    job = client.post("/api/intake/jobs", json={"preview_id": preview["preview_id"]}).json()
    run = client.post(f"/api/intake/jobs/{job['id']}/run")
    assert run.status_code == 200, run.text
    assert run.json()["extracted_candidates"] == 4
    saved_candidates = client.get(f"/api/intake/jobs/{job['id']}/candidates").json()
    evidence = client.get(f"/api/intake/jobs/{job['id']}/evidence").json()
    assert len(saved_candidates) == 4
    assert len(evidence) == 4
    assert evidence[0]["payload"]["raw_reference"]["column_name"] in {"Factory Contract Address", "V2Router02 Contract Address"}


def test_sablier_sectioned_docs_table_uses_heading_as_network(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <html><body>
      <h2>Ethereum</h2>
      <table>
        <tr><th>Contract</th><th>Address</th><th>Deployment</th></tr>
        <tr><td>SablierFlow</td><td>0x8441111111111111111111111111111111111111</td><td>v1</td></tr>
        <tr><td>FlowNFTDescriptor</td><td>0x24b2222222222222222222222222222222222222</td><td>v1</td></tr>
      </table>
      <h2>Abstract</h2>
      <table>
        <tr><th>Contract</th><th>Address</th><th>Deployment</th></tr>
        <tr><td>SablierFlow</td><td>0x2fac333333333333333333333333333333333333</td><td>v1</td></tr>
      </table>
    </body></html>
    """
    preview = _url_preview(
        client,
        monkeypatch,
        source_url="https://docs.sablier.com/guides/flow/deployments",
        content=html,
    )
    candidates = preview["candidates_preview"]
    assert len(candidates) == 3
    assert preview["profile"]["entity_name"] == "Sablier"
    assert preview["profile"]["category"] == "yield"
    assert preview["profile"]["sub_category"] == "streaming_payments"
    assert [candidate["source_network"] for candidate in candidates] == ["Ethereum", "Ethereum", "Abstract"]
    assert {candidate["suggested_role"] for candidate in candidates} == {"protocol_contract", "nft_descriptor"}


def test_aave_github_solidity_constants_preview_save_run_evidence(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    source = """
    pragma solidity ^0.8.0;
    // Main Aave V3 pool addresses provider
    IPoolAddressesProvider internal constant POOL_ADDRESSES_PROVIDER =
      IPoolAddressesProvider(0x1111111111111111111111111111111111111111);
    address internal constant AAVE_ORACLE = 0x2222222222222222222222222222222222222222;
    """
    preview = _url_preview(
        client,
        monkeypatch,
        source_url="https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Ethereum.sol",
        content=source,
        content_type="text/plain",
        input_method="github",
    )
    assert preview["final_source_type"] == "github_blob"
    assert preview["adapter_name"] == "github_adapter"
    assert preview["profile"]["entity_name"] == "Aave"
    assert preview["profile"]["category"] == "lending"
    candidates = preview["candidates_preview"]
    assert len(candidates) == 2
    assert {candidate["suggested_role"] for candidate in candidates} == {"address_provider", "oracle"}
    assert {candidate["source_network"] for candidate in candidates} == {"Ethereum"}
    assert all(candidate["source_input_type"] == "github_solidity_address_book" for candidate in candidates)
    assert all(candidate["evidence_type"] == "official_github_deployment" for candidate in candidates)
    assert all(candidate["source_url"] == "https://raw.githubusercontent.com/aave-dao/aave-address-book/main/src/AaveV3Ethereum.sol" for candidate in candidates)

    job = client.post("/api/intake/jobs", json={"preview_id": preview["preview_id"]}).json()
    run = client.post(f"/api/intake/jobs/{job['id']}/run")
    assert run.status_code == 200, run.text
    assert run.json()["extracted_candidates"] == 2
    assert len(client.get(f"/api/intake/jobs/{job['id']}/candidates").json()) == 2
    evidence = client.get(f"/api/intake/jobs/{job['id']}/evidence").json()
    assert len(evidence) == 2
    assert {item["payload"]["raw_reference"]["contract_name"] for item in evidence} == {"POOL_ADDRESSES_PROVIDER", "AAVE_ORACLE"}


def test_generic_solidity_router_role_inference() -> None:
    tables = extract_solidity_deployment_table(
        "address public constant ROUTER = 0x3333333333333333333333333333333333333333;",
        source_url="https://github.com/example/protocol/blob/main/src/Deployments.sol",
    )
    assert tables[0]["rows"][0]["Role"] == "router_contract"


def test_github_blob_url_converts_to_raw_source() -> None:
    assert github_blob_to_raw_url("https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Ethereum.sol") == (
        "https://raw.githubusercontent.com/aave-dao/aave-address-book/main/src/AaveV3Ethereum.sol"
    )


def test_pdf_requested_as_excel_upload_routes_to_pdf_adapter(client: TestClient) -> None:
    preview = _upload_preview(client, "reserve.pdf", b"%PDF-1.7\nfake", "excel_upload")
    assert preview["final_source_type"] == "pdf_upload"
    assert preview["adapter_name"] == "pdf_adapter"
    assert preview["override_reason"] == "source_type_overridden_by_artifact_fingerprint"


def test_pdf_front_matter_words_do_not_create_fake_candidates(client: TestClient) -> None:
    sample = """
    Proof of Reserves Audit Report BYBIT
    cryptocurrencies Preliminary
    By implementing Hacken Proof of Reserves, reserves are verifiable.
    The primary objective of this audit is to provide assurance.
    """
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    assert preview["final_source_type"] == "pdf_upload"
    assert preview["adapter_name"] == "pdf_adapter"
    assert preview["candidates_preview"] == []


def test_pdf_audited_wallet_section_extracts_only_real_rows(client: TestClient) -> None:
    sample = """
    Proof of Reserves Audit Report BYBIT
    Report date Fri Apr 24 2026
    Audit date Wed Apr 22 2026
    Audited wallets
    Network Address
    Aptos 0x118db0fecb576630cb1c977efb0de29d3692cafbe8dc88f5289f712e3
    5d9a1e8
    Arbitrum 0x18673311fec54ac2244a602e6d91845553d24e62
    Collateral ratios
    ryptocurrenciesPreliminary
    """
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    candidates = preview["candidates_preview"]
    assert len(candidates) == 2
    assert candidates[0]["source_network"] == "Aptos"
    assert candidates[0]["address"] == "0x118db0fecb576630cb1c977efb0de29d3692cafbe8dc88f5289f712e35d9a1e8"
    assert candidates[0]["chain_guess"] == "aptos"
    assert candidates[0]["suggested_role"] == "cex_por_wallet"
    assert candidates[0]["source_input_type"] == "pdf_audited_wallet_table"
    assert candidates[0]["evidence_type"] == "audited_wallet"
    assert candidates[0]["confidence_initial"] == 85
    assert candidates[1]["source_network"] == "Arbitrum"
    assert candidates[1]["address"] == "0x18673311fec54ac2244a602e6d91845553d24e62"
    assert candidates[1]["chain_id"] == 42161


def test_pdf_xrp_false_positive_is_rejected(client: TestClient) -> None:
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", b"ryptocurrenciesPreliminary")
    assert preview["candidates_preview"] == []


def test_pdf_ripple_row_requires_network_and_validates(client: TestClient) -> None:
    sample = """
    BYBIT
    Audited wallets
    Network Address
    Ripple raBWjPDjohBGc9dR6ti3DsP9Sn47jirTi3
    Conclusion
    """
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    candidates = preview["candidates_preview"]
    assert len(candidates) == 1
    assert candidates[0]["source_network"] == "Ripple"
    assert candidates[0]["address"] == "raBWjPDjohBGc9dR6ti3DsP9Sn47jirTi3"
    assert candidates[0]["chain_guess"] == "xrp"


def test_pdf_preview_bybit_profile_uses_audited_wallet_metadata(client: TestClient) -> None:
    sample = """
    Proof of Reserves Audit Report BYBIT
    Auditee Bybit
    Report date Fri Apr 24 2026
    Audit date Wed Apr 22 2026
    cryptocurrencies Preliminary
    Audited wallets
    Network Address
    Aptos 0x118db0fecb576630cb1c977efb0de29d3692cafbe8dc88f5289f712e3
    5d9a1e8
    Team Composition
    Reserves By implementing the Hacken approach
    """
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    assert preview["profile"]["entity_name"] == "Bybit"
    assert preview["profile"]["category"] == "cex"
    assert preview["profile"]["sub_category"] == "reserve_boundary"
    assert "cex_por_wallet" in preview["profile"]["expected_roles"]
    assert preview["candidates_preview"][0]["source_input_type"] == "pdf_audited_wallet_table"
    assert all("Preliminary" not in candidate["address"] for candidate in preview["candidates_preview"])


def test_pdf_network_and_address_headers_on_separate_lines_parse(client: TestClient) -> None:
    sample = """
    BYBIT Proof of Reserves Audit Report
    Audited wallets
    Network
    Address
    Arbitrum 0x18673311fec54ac2244a602e6d91845553d24e62
    Conclusion
    """
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    assert preview["profile"]["metadata"]["network_address_header_found"] is True
    assert preview["profile"]["metadata"]["audited_wallet_rows_detected"] == 1
    assert preview["profile"]["metadata"]["pdf_parser_mode"] == "hacken_audited_wallet_line_table"
    assert preview["candidates_preview"][0]["address"] == "0x18673311fec54ac2244a602e6d91845553d24e62"


def test_pdf_network_and_address_on_separate_lines_parse(client: TestClient) -> None:
    sample = """
    BYBIT Proof of Reserves
    Audited wallets
    Network Address
    Aptos
    0x118db0fecb576630cb1c977efb0de29d3692cafbe8dc88f5289f712e3
    5d9a1e8
    Conclusion
    """
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    assert len(preview["candidates_preview"]) == 1
    assert preview["candidates_preview"][0]["source_network"] == "Aptos"
    assert preview["candidates_preview"][0]["address"] == "0x118db0fecb576630cb1c977efb0de29d3692cafbe8dc88f5289f712e35d9a1e8"


def test_pdf_split_multiword_network_parses_arbitrum_nova(client: TestClient) -> None:
    sample = """
    BYBIT Proof of Reserves
    Audited wallets
    Network Address
    Arbitrum
    Nova
    0xd4d1111111111111111111111111111111111111
    Conclusion
    """
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    assert len(preview["candidates_preview"]) == 1
    assert preview["candidates_preview"][0]["source_network"] == "Arbitrum Nova"
    assert preview["candidates_preview"][0]["chain_id"] == 42170
    assert preview["candidates_preview"][0]["address"] == "0xd4d1111111111111111111111111111111111111"


def test_pdf_footer_line_is_not_appended_to_wrapped_address(client: TestClient) -> None:
    address = "0x118db0fecb576630cb1c977efb0de29d3692cafbe8dc88f5289f712e35d9a1e8"
    sample = f"""
    BYBIT Proof of Reserves
    Audited wallets
    Network Address
    Aptos {address}
    Hacken's BYBIT Proof of Reserve
    Page 12
    """
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    assert len(preview["candidates_preview"]) == 1
    assert preview["candidates_preview"][0]["address"] == address


def test_pdf_bybit_por_fallback_profile_keeps_cex_context(client: TestClient) -> None:
    sample = """
    Proof of Reserves Audit Report BYBIT
    Auditee Bybit
    No audited wallet rows on this excerpt.
    """
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    assert preview["candidates_preview"] == []
    assert preview["profile"]["category"] == "cex"
    assert preview["profile"]["sub_category"] == "reserve_boundary"
    assert "cex_por_wallet" in preview["profile"]["expected_roles"]
    assert preview["profile"]["metadata"]["pdf_parser_mode"] == "pdf_text_fallback"


def test_pdf_audited_wallet_heading_without_rows_has_specific_warning(client: TestClient) -> None:
    sample = """
    BYBIT Proof of Reserves Audit Report
    Audited wallets
    Network
    Address
    No wallet rows available in this fixture.
    Conclusion
    """
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    assert preview["profile"]["metadata"]["audited_wallet_heading_found"] is True
    assert preview["profile"]["metadata"]["audited_wallet_rows_detected"] == 0
    assert "pdf_audited_wallet_section_found_but_no_rows" in preview["warnings"]


def test_pdf_compact_hacken_audited_wallet_rows_parse(client: TestClient) -> None:
    sample = (
        "Proof of Reserves Audit Report BYBIT"
        "Audited walletsNetworkAddress"
        "Aptos0x118db0fecb576630cb1c977efb0de29d3692cafbe8dc88f5289f712e35d9a1e8"
        "Aptos0x4cead285873b1bbbbd7fecc3c103e539f5b2ab7563b4c3d9e98f9f013a014e5a"
        "Arbitrum0x18673311fec54ac2244a602e6d91845553d24e62"
        "Collateral ratios"
    )
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    candidates = preview["candidates_preview"]
    assert len(candidates) == 3
    assert [candidate["source_network"] for candidate in candidates] == ["Aptos", "Aptos", "Arbitrum"]
    assert candidates[0]["address"] == "0x118db0fecb576630cb1c977efb0de29d3692cafbe8dc88f5289f712e35d9a1e8"
    assert candidates[1]["address"] == "0x4cead285873b1bbbbd7fecc3c103e539f5b2ab7563b4c3d9e98f9f013a014e5a"
    assert candidates[2]["address"] == "0x18673311fec54ac2244a602e6d91845553d24e62"
    assert preview["profile"]["metadata"]["pdf_parser_mode"] == "hacken_audited_wallet_compact_table"
    assert preview["profile"]["metadata"]["audited_wallet_rows_detected"] == 3
    assert preview["profile"]["metadata"]["source_input_type"] == "pdf_audited_wallet_table"
    assert preview["profile"]["table_count"] == 1
    assert "pdf_loose_text_fallback_used" not in preview["warnings"]


def test_pdf_compact_hacken_parser_normalizes_ligatures(client: TestClient) -> None:
    sample = (
        "Proof of Reserves Audit Report BYBIT"
        "Audited walletsNetworkAddress"
        "Arbitrum0x83d8b993ﬀ9795aee4abe8597c1925b50b30d5be"
        "Conclusion"
    )
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode("utf-8"))
    assert len(preview["candidates_preview"]) == 1
    assert preview["candidates_preview"][0]["source_network"] == "Arbitrum"
    assert preview["candidates_preview"][0]["address"] == "0x83d8b993ff9795aee4abe8597c1925b50b30d5be"
    assert preview["profile"]["metadata"]["pdf_text_normalized"] is True
    assert preview["profile"]["metadata"]["pdf_parser_mode"] == "hacken_audited_wallet_compact_table"


def test_pdf_compact_hacken_parser_does_not_parse_footer(client: TestClient) -> None:
    sample = (
        "Proof of Reserves Audit Report BYBIT"
        "Audited walletsNetworkAddress"
        "Hacken's BYBIT Proof of Reserve Page8"
    )
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    assert preview["candidates_preview"] == []
    assert preview["profile"]["metadata"]["audited_wallet_heading_found"] is True
    assert preview["profile"]["metadata"]["network_address_header_found"] is True
    assert preview["profile"]["metadata"]["audited_wallet_rows_detected"] == 0
    assert "pdf_structured_hacken_parser_failed" in preview["warnings"]


def test_pdf_compact_hacken_parser_ignores_page_noise_until_collateral_ratios(client: TestClient) -> None:
    sample = (
        "Proof of Reserves Audit Report BYBIT"
        "Audited walletsNetworkAddress"
        "Aptos0x118db0fecb576630cb1c977efb0de29d3692cafbe8dc88f5289f712e35d9a1e8"
        "Hacken's BYBIT Proof of Reserve 2026/04/22 Confidential Page8"
        "Hacken OU Parda 4, Kesklinn Tallinn 10151 Harju Maakond Eesti Kesklinna, Estonia"
        "Arbitrum0xf440139a62b2b939699c5b3e09f88e40464ab9bc"
        "Bitcoin12rFmDggwCNrRL6vuPEjzCDSskTRPjDajP"
        "BSC0x1c3944173abee256456b1498299fc501ad5bbd6f"
        "Collateral ratios"
        "Ethereum0x9999999999999999999999999999999999999999"
    )
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    rows = preview["table_preview"][0]["rows"]
    networks = [row["Network"] for row in rows]
    assert networks == ["Aptos", "Arbitrum", "Bitcoin", "BSC"]
    assert preview["profile"]["metadata"]["parser_stop_marker"] == "Collateral ratios"
    assert preview["profile"]["metadata"]["raw_wallet_rows_detected"] == 4
    assert preview["profile"]["metadata"]["total_wallet_rows_detected"] == 4
    assert preview["profile"]["metadata"]["network_counts"] == {"Aptos": 1, "Arbitrum": 1, "Bitcoin": 1, "BSC": 1}
    assert preview["profile"]["metadata"]["candidate_rows_created"] == 4
    assert "Ethereum" not in networks
    assert {candidate["source_network"] for candidate in preview["candidates_preview"]} == {"Aptos", "Arbitrum", "Bitcoin", "BSC"}


def test_pdf_compact_hacken_real_bybit_excerpt_continues_after_page_footers(client: TestClient) -> None:
    sample = (
        "Proof of Reserves Audit Report BYBIT"
        "Audited walletsNetworkAddress"
        "Aptos0x118db0fecb576630cb1c977efb0de29d3692cafbe8dc88f5289f712e35d9a1e8"
        "Hacken's BYBIT Proof of Reserve 2026/04/22 Parda 4 Page8"
        "Arbitrum0xf440139a62b2b939699c5b3e09f88e40464ab9bc"
        "ArbitrumNova0xd4d1111111111111111111111111111111111111"
        "Hacken OU Parda 4, Kesklinn Tallinn 10151 Harju Maakond Eesti Kesklinna, Estonia Page9"
        "Avalanche-C0x2222222222222222222222222222222222222222"
        "Bitcoin12rFmDggwCNrRL6vuPEjzCDSskTRPjDajP"
        "BSC0x1c3944173abee256456b1498299fc501ad5bbd6f"
        "Hacken's BYBIT Proof of Reserve 2026/04/22 Page10"
        "Ethereum0x3333333333333333333333333333333333333333"
        "Hacken's BYBIT Proof of Reserve 2026/04/22 Page11"
        "TronTQY8hQGQ2Z1P2rJjZfY5rS7qk9hF5pQy7x"
        "Collateral ratios"
    )
    preview = _upload_preview(client, "Bybit_PoR_Audit_2026_Apr_22.pdf", sample.encode())
    metadata = preview["profile"]["metadata"]
    candidate_networks = {candidate["source_network"] for candidate in preview["candidates_preview"]}
    assert metadata["pdf_parser_mode"] == "hacken_audited_wallet_compact_table"
    assert metadata["audited_wallet_rows_detected"] >= 8
    assert metadata["network_counts"]["Bitcoin"] == 1
    assert metadata["network_counts"]["BSC"] == 1
    assert "Bitcoin" in candidate_networks
    assert "BSC" in candidate_networks
    assert metadata["parser_stop_marker"] == "Collateral ratios"


def test_pdf_mexc_hacken_parser_skips_toc_and_selects_real_audited_wallet_section(client: TestClient) -> None:
    algorand = "A" * 58
    aptos = "0xe8ca" + "1" * 56 + "cbdc"
    arbitrum = "0xb86f" + "1" * 36
    bitcoin = "13uZ" + "A" * 30
    bsc = "0x2e8" + "2" * 37
    sample = f"""
    Proof of Reserves Audit Report MEXC
    Executive Summary
    Building Trust
    Methodology
    Proof of Reserves Scope & Findings
    Audited wallets
    Collateral ratios
    Team Composition
    Conclusion
    Disclaimers
    References
    Auditee MEXC
    Hacken MEXC Proof of Reserves Page 12
    Audited wallets
    Network Address
    Algorand
    {algorand[:52]}
    {algorand[52:]}
    Aptos
    {aptos[:62]}
    {aptos[62:]}
    Arbitrum {arbitrum}
    Bitcoin {bitcoin}
    BSC {bsc}
    Collateral ratios
    """
    preview = _upload_preview(client, "MEXC_PoR_Audit_20260510.pdf", sample.encode())
    metadata = preview["profile"]["metadata"]
    rows = preview["table_preview"][0]["rows"]
    assert preview["profile"]["entity_name"] == "MEXC"
    assert preview["profile"]["category"] == "cex"
    assert metadata["pdf_parser_mode"] == "hacken_audited_wallet_line_table"
    assert metadata["parser_stop_marker"] == "Collateral ratios"
    assert metadata["rejected_audited_wallet_heading_count"] >= 1
    assert [row["Network"] for row in rows] == ["Algorand", "Aptos", "Arbitrum", "Bitcoin", "BSC"]
    assert "pdf_loose_text_fallback_used" not in preview["warnings"]
    assert all(candidate["source_network"] for candidate in preview["candidates_preview"])


def test_pdf_kucoin_hacken_line_table_parses_split_networks_and_wrapped_addresses(client: TestClient) -> None:
    aptos = "0x7cab" + "2" * 56 + "0f22"
    avalanche = "0x17a303" + "3" * 34
    bitcoin = "bc1qjxk5" + "a" * 30 + "as8a3dkw"
    kcc = "0x4c" + "4" * 38
    noble = "noble1" + "q" * 38
    ton = "EQ" + "A" * 46
    xdc = "0x5d" + "5" * 38
    zklink = "0x6e" + "6" * 38
    zksync = "0x7f" + "7" * 38
    sample = f"""
    Hacken's KuCoin Proof of Reserve
    Auditee KuCoin
    Audited Wallets
    Network
    Address
    Aptos
    {aptos[:62]}
    {aptos[62:]}
    Avalanche
    C-Chain
    {avalanche}
    Bitcoin
    {bitcoin[:30]}
    {bitcoin[30:]}
    KCC {kcc}
    NEAR kucoinc.near
    Noble {noble}
    TON {ton}
    XDC {xdc}
    zkLink Nova {zklink}
    zkSync Era {zksync}
    Collateral Ratios
    """
    preview = _upload_preview(client, "KuCoin_PoR_Audit.pdf", sample.encode())
    metadata = preview["profile"]["metadata"]
    rows = preview["table_preview"][0]["rows"]
    by_network = {row["Network"]: row["Address"] for row in rows}
    assert preview["profile"]["entity_name"] == "KuCoin"
    assert preview["profile"]["category"] == "cex"
    assert metadata["pdf_parser_mode"] == "hacken_audited_wallet_line_table"
    assert metadata["parser_stop_marker"] == "Collateral Ratios"
    assert by_network["Aptos"] == aptos
    assert by_network["Avalanche-C"] == avalanche
    assert by_network["Bitcoin"] == bitcoin
    assert by_network["KCC"] == kcc
    assert by_network["Near"] == "kucoinc.near"
    assert by_network["Noble"] == noble
    assert by_network["Ton"] == ton
    assert by_network["XDC"] == xdc
    assert by_network["zkLink Nova"] == zklink
    assert by_network["zkSync Era"] == zksync
    assert metadata["candidate_rows_created"] == metadata["raw_wallet_rows_detected"]
    assert "pdf_loose_text_fallback_used" not in preview["warnings"]


def test_layout_wallet_rows_reconstruct_column_major_mexc_sample() -> None:
    def word(text: str, x0: float, top: float) -> dict:
        return {"text": text, "x0": x0, "x1": x0 + len(text) * 6, "top": top, "bottom": top + 10}

    algorand = "M3IAMWFYEIJWLWFIIOEDFOLGIVMEOB3F4I3CA4BIAHJENHUUSX63APOXXM"
    aptos = "0xe8ca094fec460329aaccc2a644dc73c5e39f1a2ad6e97f82b6cbdc1a5949b9ea"
    words = [
        word("Network", 62, 160),
        word("Address", 158, 160),
        word(algorand[:58], 158, 188),
        word("Algorand", 62, 197),
        word(algorand[58:], 158, 206),
        word(aptos[:62], 158, 228),
        word("Aptos", 62, 237),
        word(aptos[62:], 158, 246),
        word("Bitcoin", 62, 270),
        word("13uZyaPbt4rTwYQ8xWFySVUzWH3pk2P5c7", 158, 270),
        word("BSC", 62, 294),
        word("0x2e8f79ad740de90dc5f5a9f0d8d9661a60725e64", 158, 294),
    ]
    rows = _layout_wallet_rows_from_page(words, "MEXC", 9)
    by_network = {row["Network"]: row["Address"] for row in rows}
    assert by_network["Algorand"] == algorand
    assert by_network["Aptos"] == aptos
    assert by_network["Bitcoin"] == "13uZyaPbt4rTwYQ8xWFySVUzWH3pk2P5c7"
    assert by_network["BSC"] == "0x2e8f79ad740de90dc5f5a9f0d8d9661a60725e64"


def test_xdc_prefixed_and_0x_addresses_normalize_to_xdc(client: TestClient) -> None:
    preview = _upload_preview(
        client,
        "xdc.csv",
        (
            "Entity,Network,Address\n"
            "KuCoin,XDC,xdcF29f049144467b3dc55e19205c30C1737942F23a\n"
            "KuCoin,XDC,0x2933782b5a8d72f2754103d1489614f29bfa4625\n"
        ).encode(),
    )
    by_address = {candidate["address"]: candidate for candidate in preview["candidates_preview"]}
    assert by_address["xdcF29f049144467b3dc55e19205c30C1737942F23a"]["normalized_address"] == "xdcf29f049144467b3dc55e19205c30c1737942f23a"
    assert by_address["0x2933782b5a8d72f2754103d1489614f29bfa4625"]["normalized_address"] == "xdc2933782b5a8d72f2754103d1489614f29bfa4625"
    assert all(candidate["chain_slug"] == "xdc" for candidate in preview["candidates_preview"])


def test_real_mexc_pdf_layout_parser_extracts_full_wallet_section(client: TestClient) -> None:
    path = Path(r"C:\Users\User\Downloads\MEXC_PoR_Audit_20260510.pdf")
    if not path.exists():
        pytest.skip("real MEXC PDF fixture is not available")
    preview = _upload_preview(client, path.name, path.read_bytes())
    metadata = preview["profile"]["metadata"]
    required_networks = {
        "Algorand",
        "Aptos",
        "Arbitrum",
        "Avalanche-C",
        "Base",
        "Bitcoin",
        "BSC",
        "Celo",
        "Ethereum",
        "Kaia",
        "Linea",
        "Morph",
        "Near",
        "Optimism",
        "Plasma",
        "Polkadot AH",
        "Polygon",
        "SEI",
        "Solana",
        "Sonic",
        "Starknet",
        "Sui",
        "Ton",
        "Tron",
        "Unichain",
        "XDC",
        "zkSync Lite",
    }
    assert preview["profile"]["entity_name"] == "MEXC"
    assert preview["profile"]["category"] == "cex"
    assert preview["profile"]["sub_category"] == "reserve_boundary"
    assert metadata["pdf_parser_mode"] == "hacken_audited_wallet_layout_table"
    assert metadata["parser_stop_marker"] == "Collateral ratios"
    assert metadata["raw_wallet_rows_detected"] > 23
    assert metadata["candidate_rows_created"] == metadata["raw_wallet_rows_detected"]
    assert required_networks <= set(metadata["network_counts"])
    assert preview["warnings"] == []
    assert all(candidate["source_network"] for candidate in preview["candidates_preview"])
    assert all(candidate["confidence_initial"] != 45 for candidate in preview["candidates_preview"])


def test_real_kucoin_pdf_layout_parser_extracts_full_wallet_section(client: TestClient) -> None:
    path = Path(r"C:\Users\User\Downloads\kucoin_report.pdf")
    if not path.exists():
        pytest.skip("real KuCoin PDF fixture is not available")
    preview = _upload_preview(client, path.name, path.read_bytes())
    metadata = preview["profile"]["metadata"]
    required_networks = {
        "Aptos",
        "Arbitrum",
        "Aurora",
        "Avalanche-C",
        "Base",
        "Blast",
        "Bitcoin",
        "BSC",
        "Ethereum",
        "Hyperliquid",
        "Kava EVM",
        "KCC",
        "Linea",
        "Manta",
        "Merlin",
        "Monad",
        "Near",
        "Noble",
        "Optimism",
        "Plasma",
        "Polygon",
        "Scroll",
        "Sonic",
        "Solana",
        "Statemint",
        "Starknet",
        "Sui",
        "Taiko",
        "Tezos",
        "Ton",
        "Tron",
        "XDC",
        "Zircuit",
        "zkLink Nova",
        "zkSync Era",
    }
    assert preview["profile"]["entity_name"] == "KuCoin"
    assert preview["profile"]["category"] == "cex"
    assert preview["profile"]["sub_category"] == "reserve_boundary"
    assert metadata["pdf_parser_mode"] == "hacken_audited_wallet_layout_table"
    assert metadata["parser_stop_marker"] == "Collateral Ratios"
    assert metadata["raw_wallet_rows_detected"] > 67
    assert metadata["candidate_rows_created"] == metadata["raw_wallet_rows_detected"]
    assert required_networks <= set(metadata["network_counts"])
    assert preview["warnings"] == []
    assert all(candidate["source_network"] for candidate in preview["candidates_preview"])
    assert all(candidate["confidence_initial"] != 45 for candidate in preview["candidates_preview"])


def test_candidate_save_rolls_back_without_context_or_evidence(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    preview = _upload_preview(
        client,
        "wallets.csv",
        b"Entity,Network,Address\nBybit,Ethereum,0x1111111111111111111111111111111111111111\n",
    )
    job = client.post("/api/intake/jobs", json={"preview_id": preview["preview_id"]}).json()

    def fail_evidence(self, candidate, source_document, item, job):
        raise RuntimeError("evidence failed")

    monkeypatch.setattr(IntakeOrchestrator, "_save_candidate_evidence", fail_evidence)
    with SessionLocal() as db:
        with pytest.raises(RuntimeError):
            asyncio.run(IntakeOrchestrator(db).run_extraction(job["id"]))
        assert db.scalars(select(AddressCandidate).where(AddressCandidate.source_job_id == job["id"])).all() == []
        assert db.scalars(select(AddressEvidence)).all() == []


def test_registry_promotion_is_disabled() -> None:
    with pytest.raises(NotImplementedError):
        RegistryPromotionService().promote_candidate(1)


def test_xlsx_multi_sheet_registry_skips_controls_and_parses_real_sheets(client: TestClient) -> None:
    content = _xlsx_bytes(
        {
            "Summary": [["control"]],
            "Schema": [["control"]],
            "Source_Provenance": [["control"]],
            "Bybit": [
                ["Entity", "Network", "Address", "Wallet Label / Role", "Evidence Type", "Source Page / Line"],
                ["Bybit", "Ethereum", "0x1111111111111111111111111111111111111111", "Reserve Wallet", "source", "12"],
            ],
            "KuCoin": [
                ["Entity", "Network", "Address", "Wallet Label / Role"],
                ["KuCoin", "BSC", "0x2222222222222222222222222222222222222222", "Cold Wallet"],
            ],
        }
    )
    preview = _upload_preview(client, "registry.xlsx", content)
    assert set(preview["profile"]["parsed_sheet_names"]) == {"Bybit", "KuCoin"}
    assert {"Summary", "Schema", "Source_Provenance"}.issubset(set(preview["profile"]["skipped_sheet_names"]))
    assert preview["profile"]["table_count"] == 2
    assert preview["profile"]["detected_columns"][0]["entity"] == "Entity"
    assert len(preview["candidates_preview"]) == 2
    assert all(candidate["status"] == "needs_review" for candidate in preview["candidates_preview"])


def test_xlsx_staking_sheet_creates_wallet_candidates_and_keeps_validator_key_as_metadata(client: TestClient) -> None:
    content = _xlsx_bytes(
        {
            "OKX_Staking_Validators": [
                ["Entity", "Network", "Deposit Address", "Validator Public Key", "Withdrawal / Cold Address", "Evidence Type"],
                [
                    "OKX",
                    "Ethereum",
                    "0x3333333333333333333333333333333333333333",
                    "0x" + "a" * 96,
                    "0x4444444444444444444444444444444444444444",
                    "staking_registry",
                ],
            ]
        }
    )
    preview = _upload_preview(client, "staking.xlsx", content)
    roles = {candidate["suggested_role"] for candidate in preview["candidates_preview"]}
    assert roles == {"staking_deposit_wallet", "staking_withdrawal_wallet"}
    assert all("validator_public_key_metadata_only" in candidate["warnings"] for candidate in preview["candidates_preview"])
    assert all(candidate["address"] != "0x" + "a" * 96 for candidate in preview["candidates_preview"])


def test_same_evm_address_on_multiple_networks_stays_separate(client: TestClient) -> None:
    address = "0x5555555555555555555555555555555555555555"
    content = (
        "Entity,Network,Address\n"
        f"Bybit,Ethereum,{address}\n"
        f"Bybit,Arbitrum,{address}\n"
        f"Bybit,Base,{address}\n"
        f"Bybit,BSC,{address}\n"
    ).encode()
    preview = _upload_preview(client, "multi-network.csv", content)
    assert len(preview["candidates_preview"]) == 4
    assert {candidate["chain_id"] for candidate in preview["candidates_preview"]} == {1, 42161, 8453, 56}


def test_long_aptos_0x_address_is_not_truncated(client: TestClient) -> None:
    address = "0x" + "a" * 64
    preview = _upload_preview(client, "aptos.csv", f"Entity,Network,Address\nIndodax,Aptos,{address}\n".encode())
    candidate = preview["candidates_preview"][0]
    assert candidate["address"] == address
    assert candidate["normalized_address"] == address
    assert candidate["chain_guess"] == "aptos"


def test_intake_console_and_input_window_behavior(client: TestClient) -> None:
    html = client.get("/intake-console").text
    assert "MQCHAIN Intake Console" in html
    assert 'accept=".pdf,.csv,.xlsx,.xls,.txt,.md,.json,.yaml,.yml"' in html
    assert "Candidate preview table" in html
    assert "candidateTableWrap" in html
    assert "Evidence table" in html
    assert "evidenceTableWrap" in html
    assert "No evidence rows found for this source job" in html
    assert "extracted_candidates" in html
    assert "reused_existing" in html
    assert "required source_type" not in html.lower()

    response = client.get("/input-window", follow_redirects=False)
    assert response.status_code in {307, 308}
    assert response.headers["location"] == "/intake-console"
