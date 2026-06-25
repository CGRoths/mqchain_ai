from __future__ import annotations

from io import BytesIO

from app.ingestion.extraction_pipeline import ExtractionPipeline
from app.ingestion.extractors.common import evidence_type_for_source
from app.ingestion.intake_models import SourceArtifact, SourceFingerprint
from app.ingestion.source_adapters import ExcelCsvAdapter
from app.ingestion.source_identity import infer_source_identity
from app.ingestion.source_signal_extractor import extract_source_signals
from app.ingestion.source_trust_classifier import classify_source_trust
from app.review.candidate_audit import classify_source_trust_status


def _fingerprint(final_source_type: str, adapter_name: str, *, content_kind: str = "html", mime_type: str = "text/html") -> SourceFingerprint:
    return SourceFingerprint(
        file_extension=None,
        magic_signature=None,
        mime_type=mime_type,
        url_kind=final_source_type,
        content_kind=content_kind,
        detected_source_type=final_source_type,
        final_source_type=final_source_type,
        parser_adapter=adapter_name,
        confidence=80,
    )


def _artifact(source_url: str | None, *, input_method: str = "url", filename: str | None = None, content_type: str = "text/html") -> SourceArtifact:
    return SourceArtifact(
        input_method=input_method,
        filename=filename or (source_url.rsplit("/", 1)[-1] if source_url else None),
        source_url=source_url,
        content_type=content_type,
        raw_content_sample=b"",
        size_bytes=0,
    )


def test_generic_docs_source_infers_identity_without_official_trust() -> None:
    html = """
    <html><body>
      <h1>ExampleProtocol Deployments</h1>
      <h2>Base</h2>
      <table>
        <tr><th>Contract</th><th>Address</th></tr>
        <tr><td>ExampleRouter</td><td>0x1111111111111111111111111111111111111111</td></tr>
      </table>
    </body></html>
    """

    result = ExtractionPipeline().run(
        _artifact("https://docs.exampleprotocol.com/deployments"),
        _fingerprint("official_docs", "web_docs_adapter"),
        html.encode(),
    )

    assert len(result.candidates_preview) == 1
    row = result.normalized_rows[0]
    assert row.entity_name.lower() == "exampleprotocol"
    assert row.protocol_name.lower() == "exampleprotocol"
    assert row.category == "unknown"
    assert row.source_trust_level == "third_party_unverified"
    assert row.evidence_type == "docs_deployment_source"
    assert row.raw_reference["source_identity"]["identity_method"] == "multi_signal_identity"


def test_github_unknown_protocol_uses_universal_identity_without_official_verified() -> None:
    body = b'{"base":{"MorphoBlue":"0x2222222222222222222222222222222222222222"}}'

    result = ExtractionPipeline().run(
        _artifact("https://github.com/morpho-org/morpho-blue/blob/main/deployments.json", input_method="github", content_type="application/json"),
        _fingerprint("github_blob", "github_adapter", content_kind="json", mime_type="application/json"),
        body,
    )

    assert len(result.candidates_preview) == 1
    row = result.normalized_rows[0]
    assert row.entity_name.lower() == "morpho"
    assert row.category == "unknown"
    assert row.source_trust_level != "official_verified"
    assert row.raw_reference["source_trust"]["trust_level"] == row.source_trust_level
    assert row.raw_reference["source_signals"]["github_org"] == "morpho-org"


def test_fake_third_party_with_known_protocol_name_is_not_official_evidence() -> None:
    markdown = """
    # Aave deployment addresses
    | Network | Contract | Address |
    | --- | --- | --- |
    | Ethereum | Pool | 0x3333333333333333333333333333333333333333 |
    """

    result = ExtractionPipeline().run(
        _artifact("https://random-blog.example/aave-addresses"),
        _fingerprint("official_website", "web_docs_adapter", content_kind="markdown", mime_type="text/markdown"),
        markdown.encode(),
    )

    assert len(result.candidates_preview) == 1
    candidate = result.candidates_preview[0]
    assert candidate.entity_name == "Aave"
    assert candidate.status == "needs_review"
    assert candidate.evidence_type == "docs_deployment_source"
    assert not candidate.evidence_type.startswith("official_")
    assert candidate.raw_reference["source_trust_level"] == "third_party_unverified"


def test_uploaded_excel_gets_identity_from_filename_and_sheet_but_not_official_trust() -> None:
    raw_content = _xlsx_bytes(
        "Binance BTC Reserve",
        [
            ["Network", "Address", "Role"],
            ["Ethereum", "0x4444444444444444444444444444444444444444", "cold wallet"],
        ],
    )
    artifact = SourceArtifact(
        input_method="upload",
        filename="binance_por_wallets.xlsx",
        local_file_path="binance_por_wallets.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        raw_content_sample=raw_content[:64],
        size_bytes=len(raw_content),
    )
    fingerprint = SourceFingerprint(
        file_extension=".xlsx",
        magic_signature="xlsx_zip",
        mime_type=artifact.content_type,
        url_kind=None,
        content_kind=None,
        detected_source_type="excel_upload",
        final_source_type="excel_upload",
        parser_adapter="excel_csv_adapter",
        confidence=98,
    )

    parsed = ExcelCsvAdapter().parse(artifact, fingerprint, raw_content)

    assert len(parsed.candidates) == 1
    candidate = parsed.candidates[0]
    assert candidate.entity_name == "Binance"
    assert candidate.status == "needs_review"
    assert candidate.evidence_type == "excel_wallet_list"
    assert candidate.raw_reference["source_trust_level"] == "manual_unverified"
    assert candidate.raw_reference["source_identity"]["entity_slug"] == "binance"
    assert candidate.confidence_initial != 45


def test_third_party_officially_referenced_is_not_official_verified() -> None:
    source_url = "https://audits.example/reports/exampleprotocol-por.pdf"
    signals = extract_source_signals(
        source_url=source_url,
        filename="exampleprotocol-por.pdf",
        text_sample="ExampleProtocol proof of reserves audit",
        metadata={"official_source_outbound_urls": [source_url]},
    )
    identity = infer_source_identity(signals)
    trust = classify_source_trust(signals, identity, final_source_type="pdf_url", metadata={"official_source_outbound_urls": [source_url]})

    assert identity.entity_slug == "exampleprotocol"
    assert trust.trust_level == "third_party_officially_referenced"
    assert trust.trust_level != "official_verified"
    assert evidence_type_for_source(
        final_source_type="pdf_url",
        source_url=source_url,
        filename="exampleprotocol-por.pdf",
        text_sample="ExampleProtocol proof of reserves audit",
        metadata={"official_source_outbound_urls": [source_url]},
    ) == "pdf_por_document"


def test_review_audit_honors_explicit_third_party_trust_before_text_heuristics() -> None:
    class Candidate:
        evidence_type = "third_party_reference"
        source_type = "pdf_url"
        source_input_type = "pdf_text_fallback"
        suggested_role = "cex_por_wallet"
        raw_reference = {
            "source_trust_level": "third_party_officially_referenced",
            "source_trust": {"trust_level": "third_party_officially_referenced", "matched_signals": ["official_source_outbound_link"]},
        }
        evidence = []

    assert classify_source_trust_status(Candidate()) == "official_reference"


def test_source_verification_metadata_is_required_for_exchange_reported_trust() -> None:
    source_url = "https://coinmarketcap.com/exchanges/indodax/"
    signals = extract_source_signals(
        source_url=source_url,
        filename="indodax_reserves.xlsx",
        text_sample="Indodax exchange wallet list",
        metadata={},
    )
    identity = infer_source_identity(signals)

    unverified = classify_source_trust(signals, identity, final_source_type="excel_upload", metadata={})
    verified = classify_source_trust(
        signals,
        identity,
        final_source_type="excel_upload",
        metadata={
            "source_verification": {
                "verification_status": "verified",
                "source_trust": "third_party_exchange_reported",
                "verified_by": "analyst",
                "verified_at": "2026-06-26T00:00:00Z",
            }
        },
    )

    assert unverified.trust_level != "third_party_exchange_reported"
    assert verified.trust_level == "third_party_exchange_reported"


def _xlsx_bytes(sheet_name: str, rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
