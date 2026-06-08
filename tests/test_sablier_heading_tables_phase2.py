from __future__ import annotations

from app.ingestion.extraction_pipeline import ExtractionPipeline
from app.ingestion.intake_models import SourceArtifact, SourceFingerprint
from app.ingestion.source_adapters import WebDocsAdapter


SABLIER_URL = "https://docs.sablier.com/guides/lockup/deployments"


def _artifact(source_url: str = SABLIER_URL, content_type: str = "text/html") -> SourceArtifact:
    return SourceArtifact(
        input_method="url",
        filename=source_url.rsplit("/", 1)[-1],
        source_url=source_url,
        content_type=content_type,
        raw_content_sample=b"",
        size_bytes=0,
    )


def _fingerprint() -> SourceFingerprint:
    return SourceFingerprint(
        file_extension=None,
        magic_signature=None,
        mime_type="text/html",
        url_kind="official_docs",
        content_kind="html",
        detected_source_type="official_docs",
        final_source_type="official_docs",
        parser_adapter="web_docs_adapter",
        confidence=80,
    )


def test_sablier_lockup_heading_tables_normalize_networks_roles_and_provenance() -> None:
    html = """
    <h1>Deployment Addresses</h1>
    <h2>Abstract</h2>
    <table>
      <tr><th>Contract</th><th>Address</th><th>Deployment</th></tr>
      <tr><td>SablierLockup</td><td>0x1111111111111111111111111111111111111111</td><td>lockup-v4.0</td></tr>
      <tr><td>LockupNFTDescriptor</td><td>0x2222222222222222222222222222222222222222</td><td>lockup-v4.0</td></tr>
    </table>
    <h2>Arbitrum</h2>
    <table>
      <tr><th>Contract</th><th>Address</th><th>Deployment</th></tr>
      <tr><td>SablierBatchLockup</td><td>0x3333333333333333333333333333333333333333</td><td>lockup-v4.0</td></tr>
    </table>
    """

    result = ExtractionPipeline().run(_artifact(), _fingerprint(), html.encode())

    assert result.warnings == []
    assert len(result.normalized_rows) == 3
    assert {row.network for row in result.normalized_rows} == {"Abstract", "Arbitrum"}
    assert {row.entity_name for row in result.normalized_rows} == {"Sablier"}
    assert {row.category for row in result.normalized_rows} == {"yield"}
    assert {row.sub_category for row in result.normalized_rows} == {"streaming_payments"}
    assert {row.role for row in result.normalized_rows} == {"protocol_contract", "nft_descriptor", "batch_contract"}
    assert {row.evidence_type for row in result.normalized_rows} == {"official_docs_deployment"}
    assert {row.source_input_type for row in result.normalized_rows} == {"docs_html_deployment_table"}
    assert all(row.confidence_initial == 90 for row in result.normalized_rows)
    assert all(row.raw_reference["heading_path"][0] == "Deployment Addresses" for row in result.normalized_rows)
    assert all(row.raw_reference["table_name"].startswith("html_table_") for row in result.normalized_rows)
    assert all(row.raw_reference["row_number"] in {2, 3} for row in result.normalized_rows)
    assert all(row.raw_reference["deployment_version"] == "lockup-v4.0" for row in result.normalized_rows)
    assert all(candidate.status == "needs_review" for candidate in result.candidates_preview)


def test_sablier_explicit_network_column_overrides_heading() -> None:
    html = """
    <h1>Deployment Addresses</h1>
    <h2>Abstract</h2>
    <table>
      <tr><th>Network</th><th>Contract</th><th>Address</th></tr>
      <tr><td>Ethereum</td><td>SablierLockup</td><td>0x1111111111111111111111111111111111111111</td></tr>
    </table>
    """

    result = ExtractionPipeline().run(_artifact(), _fingerprint(), html.encode())

    assert len(result.normalized_rows) == 1
    assert result.normalized_rows[0].network == "Ethereum"
    assert result.normalized_rows[0].raw_reference["section_heading"] == "Abstract"


def test_sablier_static_html_without_tables_returns_warning_and_no_candidates() -> None:
    html = "<html><body><h1>Deployment Addresses</h1><h2>Abstract</h2><p>No static table here.</p></body></html>"

    parsed = WebDocsAdapter().parse(_artifact(), _fingerprint(), html.encode())

    assert parsed.candidates == []
    assert parsed.table_preview == []
    assert parsed.warnings == ["docs_table_not_detected_static_html"]
    assert parsed.metadata["pipeline_enabled"] is True
    assert parsed.metadata["normalized_row_count"] == 0


def test_web_docs_adapter_sablier_profile_uses_yield_category() -> None:
    html = """
    <h1>Deployment Addresses</h1>
    <h2>Sonic</h2>
    <table>
      <tr><th>Contract</th><th>Address</th><th>Deployment</th></tr>
      <tr><td>SablierFlow</td><td>0x4444444444444444444444444444444444444444</td><td>flow-v1.0</td></tr>
      <tr><td>FlowNFTDescriptor</td><td>0x5555555555555555555555555555555555555555</td><td>flow-v1.0</td></tr>
    </table>
    """

    parsed = WebDocsAdapter().parse(_artifact(), _fingerprint(), html.encode())

    assert parsed.metadata["entity_name"] == "Sablier"
    assert parsed.metadata["protocol_name"] == "Sablier"
    assert parsed.metadata["category"] == "yield"
    assert parsed.metadata["sub_category"] == "streaming_payments"
    assert len(parsed.candidates) == 2
    assert {candidate.source_network for candidate in parsed.candidates} == {"Sonic"}
    assert {candidate.suggested_role for candidate in parsed.candidates} == {"protocol_contract", "nft_descriptor"}
    assert all(candidate.evidence_type == "official_docs_deployment" for candidate in parsed.candidates)
    assert all(candidate.source_input_type == "docs_html_deployment_table" for candidate in parsed.candidates)
