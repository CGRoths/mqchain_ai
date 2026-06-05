from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.database import Base, SessionLocal, engine, init_db
from app.ingestion.intake_orchestrator import IntakeOrchestrator
from app.models.intake import AddressCandidate, AddressEvidence, SourceJob
from app.services.registry_service import RegistryPromotionService
from app.main import app


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


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


def test_pdf_requested_as_excel_upload_routes_to_pdf_adapter(client: TestClient) -> None:
    preview = _upload_preview(client, "reserve.pdf", b"%PDF-1.7\nfake", "excel_upload")
    assert preview["final_source_type"] == "pdf_upload"
    assert preview["adapter_name"] == "pdf_adapter"
    assert preview["override_reason"] == "source_type_overridden_by_artifact_fingerprint"


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
    assert "required source_type" not in html.lower()

    response = client.get("/input-window", follow_redirects=False)
    assert response.status_code in {307, 308}
    assert response.headers["location"] == "/intake-console"
