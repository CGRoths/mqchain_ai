from __future__ import annotations

from app.ingestion.candidate_builder import CandidatePreviewFactory
from app.ingestion.extraction_models import NormalizedExtractedRow, RawExtractedRow, SourceDocument
from app.ingestion.extraction_normalizer import ExtractionNormalizer
from app.ingestion.extraction_pipeline import ExtractionPipeline
from app.ingestion.extractor_base import ExtractorPlugin, ExtractorRegistry
from app.ingestion.intake_models import SourceArtifact, SourceFingerprint
from app.ingestion.protocol_profiles import ProtocolProfileRegistry


def _fingerprint(final_source_type: str = "official_docs", adapter_name: str = "web_docs_adapter") -> SourceFingerprint:
    return SourceFingerprint(
        file_extension=None,
        magic_signature=None,
        mime_type="text/html",
        url_kind=final_source_type,
        content_kind="html",
        detected_source_type=final_source_type,
        final_source_type=final_source_type,
        parser_adapter=adapter_name,
        confidence=80,
    )


def _artifact(source_url: str, *, input_method: str = "url", content_type: str = "text/html") -> SourceArtifact:
    return SourceArtifact(
        input_method=input_method,
        filename=source_url.rsplit("/", 1)[-1] or None,
        source_url=source_url,
        content_type=content_type,
        raw_content_sample=b"",
        size_bytes=0,
    )


def test_protocol_profiles_match_requested_sources_and_roles() -> None:
    registry = ProtocolProfileRegistry()

    sablier = registry.match(source_url="https://docs.sablier.com/guides/lockup/deployments")
    compound = registry.match(source_url="https://github.com/compound-finance/comet/tree/main/deployments/base/usdc")
    aave = registry.match(source_url="https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Ethereum.sol")
    safe = registry.match(source_url="https://github.com/safe-global/safe-deployments/blob/main/src/assets/v1.4.1/gnosis_safe.json")

    assert sablier.entity_name == "Sablier"
    assert sablier.category == "yield"
    assert sablier.sub_category == "streaming_payments"
    assert compound.entity_name == "Compound"
    assert aave.entity_name == "Aave"
    assert safe.entity_name == "Safe"

    assert registry.infer_role(sablier, ["SablierLockup"]) == "protocol_contract"
    assert registry.infer_role(sablier, ["LockupNFTDescriptor"]) == "nft_descriptor"
    assert registry.infer_role(aave, ["POOL_ADDRESSES_PROVIDER"]) == "address_provider"
    assert registry.infer_role(aave, ["AAVE_ORACLE"]) == "oracle"
    assert registry.infer_role(compound, ["Comet"]) == "lending_market"
    assert registry.infer_role(safe, ["ProxyFactory"]) == "proxy_factory"


class _StructuredExtractor(ExtractorPlugin):
    name = "structured"
    priority = 20

    def supports(self, document: SourceDocument) -> bool:
        return "structured" in (document.text or "")

    def extract(self, document: SourceDocument) -> list[RawExtractedRow]:
        return [
            RawExtractedRow(
                extractor_name=self.name,
                source_input_type="test_structured",
                evidence_type="source_extraction_context",
                extracted_address="0x1111111111111111111111111111111111111111",
                raw_row={"kind": "structured"},
            )
        ]


class _LooseExtractor(ExtractorPlugin):
    name = "loose"
    priority = 90
    is_loose_fallback = True

    def supports(self, document: SourceDocument) -> bool:
        return True

    def extract(self, document: SourceDocument) -> list[RawExtractedRow]:
        return [
            RawExtractedRow(
                extractor_name=self.name,
                source_input_type="test_loose",
                evidence_type="source_extraction_context",
                extracted_address="0x2222222222222222222222222222222222222222",
                raw_row={"kind": "loose"},
            )
        ]


def test_extractor_registry_suppresses_loose_fallback_when_structured_rows_exist() -> None:
    registry = ExtractorRegistry([_LooseExtractor(), _StructuredExtractor()])
    document = SourceDocument(source_document_key="doc", text="structured content")

    rows, warnings, stats = registry.run(document, allow_loose_fallback=True)

    assert warnings == []
    assert [row.extractor_name for row in rows] == ["structured"]
    assert stats["structured"]["rows_found"] == 1
    assert stats["loose"]["skipped_reason"] == "structured_rows_found"


def test_extractor_registry_runs_loose_fallback_only_when_enabled_and_needed() -> None:
    registry = ExtractorRegistry([_LooseExtractor(), _StructuredExtractor()])
    document = SourceDocument(source_document_key="doc", text="plain content")

    rows_without_fallback, _warnings, stats = registry.run(document, allow_loose_fallback=False)
    assert rows_without_fallback == []
    assert stats["loose"]["skipped_reason"] == "loose_fallback_disabled"

    rows_with_fallback, _warnings, stats = registry.run(document, allow_loose_fallback=True)
    assert [row.extractor_name for row in rows_with_fallback] == ["loose"]
    assert stats["loose"]["rows_found"] == 1


def test_normalizer_handles_sablier_heading_network() -> None:
    row = RawExtractedRow(
        extractor_name="html_heading_table_extractor",
        source_input_type="docs_html_deployment_table",
        evidence_type="official_docs_deployment",
        source_url="https://docs.sablier.com/guides/lockup/deployments",
        source_document_key="docs:sablier",
        heading_path=["Deployments", "Abstract"],
        section_heading="Abstract",
        raw_row={"Contract": "SablierLockup", "Address": "0x1111111111111111111111111111111111111111"},
        extracted_address="0x1111111111111111111111111111111111111111",
        extracted_contract_name="SablierLockup",
    )

    normalized = ExtractionNormalizer().normalize(row)

    assert normalized is not None
    assert normalized.entity_name == "Sablier"
    assert normalized.category == "yield"
    assert normalized.sub_category == "streaming_payments"
    assert normalized.network == "Abstract"
    assert normalized.role == "protocol_contract"
    assert normalized.evidence_type == "official_docs_deployment"
    assert normalized.raw_reference["heading_path"] == ["Deployments", "Abstract"]


def test_normalizer_infers_compound_network_from_file_path() -> None:
    row = RawExtractedRow(
        extractor_name="json_yaml_address_extractor",
        source_input_type="github_json_deployment_registry",
        evidence_type="official_github_deployment",
        source_url="https://github.com/compound-finance/comet/tree/main/deployments/base/usdc",
        source_file_path="deployments/base/usdc/configuration.json",
        source_document_key="github:configuration.json",
        raw_key="comet",
        raw_row={"path": ["deployments", "base", "usdc", "comet"]},
        extracted_address="0x3333333333333333333333333333333333333333",
    )

    normalized = ExtractionNormalizer().normalize(row)

    assert normalized is not None
    assert normalized.entity_name == "Compound"
    assert normalized.category == "lending"
    assert normalized.network == "Base"
    assert normalized.role == "lending_market"
    assert normalized.raw_reference["source_file_path"] == "deployments/base/usdc/configuration.json"


def test_candidate_preview_factory_preserves_shape_and_dedupes() -> None:
    normalized = NormalizedExtractedRow(
        entity_name="Compound",
        protocol_name="Compound",
        category="lending",
        network="Base",
        chain_id=8453,
        address="0x3333333333333333333333333333333333333333",
        normalized_address="0x3333333333333333333333333333333333333333",
        address_family="evm",
        contract_name="Comet",
        role="lending_market",
        label_type="lending_market",
        evidence_type="official_github_deployment",
        source_input_type="github_json_deployment_registry",
        source_url="https://github.com/compound-finance/comet/tree/main/deployments/base/usdc",
        source_file_path="deployments/base/usdc/configuration.json",
        source_document_key="github:configuration.json",
        confidence_initial=90,
        raw_reference={"source_file_path": "deployments/base/usdc/configuration.json", "final_source_type": "github_directory"},
    )

    table_preview, candidates, metadata = CandidatePreviewFactory().from_normalized_rows([normalized, normalized])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source_type == "github_directory"
    assert candidate.source_input_type == "github_json_deployment_registry"
    assert candidate.file_path == "deployments/base/usdc/configuration.json"
    assert candidate.raw_reference["source_document_key"] == "github:configuration.json"
    assert table_preview[0]["rows"][0]["Contract Name"] == "Comet"
    assert metadata["entity_name"] == "Compound"


def test_pipeline_extracts_html_heading_table_candidates() -> None:
    html = """
    <html><body>
      <h1>Sablier Deployments</h1>
      <h2>Abstract</h2>
      <table>
        <tr><th>Contract</th><th>Address</th></tr>
        <tr><td>SablierLockup</td><td>0x1111111111111111111111111111111111111111</td></tr>
      </table>
    </body></html>
    """

    result = ExtractionPipeline().run(
        _artifact("https://docs.sablier.com/guides/lockup/deployments"),
        _fingerprint(),
        html.encode(),
    )

    assert result.fatal_errors == []
    assert len(result.normalized_rows) == 1
    assert result.normalized_rows[0].entity_name == "Sablier"
    assert result.normalized_rows[0].network == "Abstract"
    assert result.candidates_preview[0].suggested_role == "protocol_contract"


def test_pipeline_extracts_aave_solidity_candidates() -> None:
    source = """
    pragma solidity ^0.8.0;
    IPoolAddressesProvider internal constant POOL_ADDRESSES_PROVIDER =
      IPoolAddressesProvider(0x1111111111111111111111111111111111111111);
    address internal constant AAVE_ORACLE = 0x2222222222222222222222222222222222222222;
    """

    result = ExtractionPipeline().run(
        _artifact("https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Ethereum.sol", input_method="github", content_type="text/plain"),
        _fingerprint("github_blob", "github_adapter"),
        source.encode(),
    )

    assert len(result.normalized_rows) == 2
    assert {row.role for row in result.normalized_rows} == {"address_provider", "oracle"}
    assert {row.network for row in result.normalized_rows} == {"Ethereum"}


def test_pipeline_extracts_json_and_typescript_candidates() -> None:
    json_result = ExtractionPipeline().run(
        _artifact("https://github.com/compound-finance/comet/tree/main/deployments/base/usdc/configuration.json", input_method="github", content_type="application/json"),
        _fingerprint("github_directory", "github_adapter"),
        b'{"comet":"0x3333333333333333333333333333333333333333"}',
    )
    assert json_result.normalized_rows[0].entity_name == "Compound"
    assert json_result.normalized_rows[0].role == "lending_market"
    assert json_result.normalized_rows[0].network == "Base"

    ts_result = ExtractionPipeline().run(
        _artifact("https://github.com/safe-global/safe-deployments/blob/main/src/assets/v1.4.1/base.ts", input_method="github", content_type="text/plain"),
        _fingerprint("github_blob", "github_adapter"),
        b'export const ProxyFactory = "0x4444444444444444444444444444444444444444";',
    )
    assert ts_result.normalized_rows[0].entity_name == "Safe"
    assert ts_result.normalized_rows[0].role == "proxy_factory"
