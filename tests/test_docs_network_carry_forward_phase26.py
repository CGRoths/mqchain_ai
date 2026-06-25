from __future__ import annotations

from app.ingestion.extraction_pipeline import ExtractionPipeline
from app.ingestion.intake_models import SourceArtifact, SourceFingerprint


def _artifact() -> SourceArtifact:
    return SourceArtifact(
        input_method="url",
        filename="deployments",
        source_url="https://docs.example.org/deployments",
        content_type="text/html",
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


def test_docs_table_rows_inherit_nearest_network_heading() -> None:
    html = """
    <h1>Deployments</h1>
    <h2>Base</h2>
    <table>
      <tr><th>Contract</th><th>Address</th><th>Deployment</th></tr>
      <tr><td>SomeContract</td><td>0x1111111111111111111111111111111111111111</td><td>v1</td></tr>
      <tr><td>OtherContract</td><td>0x2222222222222222222222222222222222222222</td><td>v1</td></tr>
    </table>
    """

    result = ExtractionPipeline().run(_artifact(), _fingerprint(), html.encode())

    assert len(result.normalized_rows) == 2
    assert {row.network for row in result.normalized_rows} == {"Base"}
    assert all("missing_network" not in row.warnings for row in result.normalized_rows)
    assert all(row.source_trust_level == "third_party_unverified" for row in result.normalized_rows)
    assert all(row.confidence_initial == 65 for row in result.normalized_rows)
    assert all(row.raw_reference["section_heading"] == "Base" for row in result.normalized_rows)


def test_docs_duplicate_table_blocks_are_deduped_by_source_contract_address_network() -> None:
    table = """
    <h2>Polygon</h2>
    <table>
      <tr><th>Contract</th><th>Address</th><th>Deployment</th></tr>
      <tr><td>SomeContract</td><td>0x1111111111111111111111111111111111111111</td><td>v1</td></tr>
    </table>
    """
    html = f"<h1>Deployments</h1>{table}{table}"

    result = ExtractionPipeline().run(_artifact(), _fingerprint(), html.encode())

    assert len(result.normalized_rows) == 1
    assert len(result.candidates_preview) == 1
    assert result.normalized_rows[0].network == "Polygon"
    assert result.normalized_rows[0].contract_name == "SomeContract"


def test_docs_explicit_row_network_overrides_heading_network() -> None:
    html = """
    <h1>Deployments</h1>
    <h2>Base</h2>
    <table>
      <tr><th>Network</th><th>Contract</th><th>Address</th></tr>
      <tr><td>Ethereum</td><td>SomeContract</td><td>0x1111111111111111111111111111111111111111</td></tr>
    </table>
    """

    result = ExtractionPipeline().run(_artifact(), _fingerprint(), html.encode())

    assert len(result.normalized_rows) == 1
    assert result.normalized_rows[0].network == "Ethereum"
    assert result.normalized_rows[0].raw_reference["section_heading"] == "Base"


def test_docs_unresolved_network_rows_are_preserved_with_missing_network_warning() -> None:
    html = """
    <h1>Deployments</h1>
    <table>
      <tr><th>Contract</th><th>Address</th></tr>
      <tr><td>SomeContract</td><td>0x1111111111111111111111111111111111111111</td></tr>
    </table>
    """

    result = ExtractionPipeline().run(_artifact(), _fingerprint(), html.encode())

    assert len(result.normalized_rows) == 1
    row = result.normalized_rows[0]
    assert row.network is None
    assert row.role == "some_contract"
    assert "missing_network" in row.warnings
    assert row.source_trust_level == "third_party_unverified"
    assert row.confidence_initial == 65


def test_docs_unknown_section_heading_is_carried_as_unrecognized_network() -> None:
    html = """
    <h1>Deployments</h1>
    <h2>Mainnets</h2>
    <h3>Berachain</h3>
    <table>
      <tr><th>Contract</th><th>Address</th></tr>
      <tr><td>SomeContract</td><td>0x1111111111111111111111111111111111111111</td></tr>
    </table>
    """

    result = ExtractionPipeline().run(_artifact(), _fingerprint(), html.encode())

    assert len(result.normalized_rows) == 1
    row = result.normalized_rows[0]
    assert row.network == "Berachain"
    assert "unrecognized_network" in row.warnings
    assert "missing_network" not in row.warnings
