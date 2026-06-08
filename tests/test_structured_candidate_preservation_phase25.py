from __future__ import annotations

from app.ingestion.extraction_pipeline import ExtractionPipeline
from app.ingestion.intake_models import SourceArtifact, SourceFingerprint


def _artifact(source_url: str, *, content_type: str = "text/plain", input_method: str = "github") -> SourceArtifact:
    return SourceArtifact(
        input_method=input_method,
        filename=source_url.rsplit("/", 1)[-1] or None,
        source_url=source_url,
        content_type=content_type,
        raw_content_sample=b"",
        size_bytes=0,
    )


def _fingerprint(final_source_type: str = "github_blob", adapter_name: str = "github_adapter", content_kind: str = "plain_text") -> SourceFingerprint:
    return SourceFingerprint(
        file_extension=None,
        magic_signature=None,
        mime_type="text/plain",
        url_kind=final_source_type,
        content_kind=content_kind,
        detected_source_type=final_source_type,
        final_source_type=final_source_type,
        parser_adapter=adapter_name,
        confidence=80,
    )


def test_aave_risk_steward_uses_structured_role_fallback() -> None:
    source = "address internal constant RISK_STEWARD = 0x13a9CC64344b02bACC5AD9Cf38B5711F1B9ec3d4;"

    result = ExtractionPipeline().run(
        _artifact("https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Ethereum.sol"),
        _fingerprint(),
        source.encode(),
    )

    row = result.normalized_rows[0]
    assert row.entity_name == "Aave"
    assert row.network == "Ethereum"
    assert row.contract_name == "RISK_STEWARD"
    assert row.role == "risk_steward"
    assert row.confidence_initial >= 85
    assert row.raw_reference["role_fallback_used"] is True
    assert row.raw_reference["role_fallback_source"] == "contract_name"
    assert row.raw_reference["original_role_text"] == "RISK_STEWARD"
    assert result.candidates_preview[0].status == "needs_review"


def test_aave_profile_priority_and_universal_fallback_roles() -> None:
    source = """
    IPoolAddressesProvider internal constant POOL_ADDRESSES_PROVIDER =
      IPoolAddressesProvider(0x1111111111111111111111111111111111111111);
    address internal constant AAVE_ORACLE = 0x2222222222222222222222222222222222222222;
    address internal constant COLLECTOR = 0x3333333333333333333333333333333333333333;
    address internal constant RISK_STEWARD = 0x13a9CC64344b02bACC5AD9Cf38B5711F1B9ec3d4;
    """

    result = ExtractionPipeline().run(
        _artifact("https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Ethereum.sol"),
        _fingerprint(),
        source.encode(),
    )

    by_name = {row.contract_name: row for row in result.normalized_rows}
    assert by_name["POOL_ADDRESSES_PROVIDER"].role == "address_provider"
    assert by_name["POOL_ADDRESSES_PROVIDER"].raw_reference["role_fallback_used"] is False
    assert by_name["AAVE_ORACLE"].role == "oracle"
    assert by_name["COLLECTOR"].role == "treasury"
    assert by_name["RISK_STEWARD"].role == "risk_steward"


def test_generic_official_github_solidity_falls_back_to_constant_name() -> None:
    source = "address internal constant SOME_NEW_CONTRACT = 0x1111111111111111111111111111111111111111;"

    result = ExtractionPipeline().run(
        _artifact("https://github.com/example/protocol/blob/main/src/Deployments.sol"),
        _fingerprint(),
        source.encode(),
    )

    assert len(result.normalized_rows) == 1
    row = result.normalized_rows[0]
    assert row.contract_name == "SOME_NEW_CONTRACT"
    assert row.role == "some_new_contract"
    assert row.raw_reference["role_fallback_used"] is True


def test_sablier_roles_remain_profile_specific_before_fallback() -> None:
    html = """
    <h1>Deployments</h1>
    <h2>Ethereum</h2>
    <table>
      <tr><th>Contract</th><th>Address</th></tr>
      <tr><td>SablierLockup</td><td>0x1111111111111111111111111111111111111111</td></tr>
      <tr><td>SablierBatchLockup</td><td>0x2222222222222222222222222222222222222222</td></tr>
      <tr><td>LockupNFTDescriptor</td><td>0x3333333333333333333333333333333333333333</td></tr>
      <tr><td>LockupMath</td><td>0x4444444444444444444444444444444444444444</td></tr>
    </table>
    """

    result = ExtractionPipeline().run(
        _artifact("https://docs.sablier.com/guides/lockup/deployments", content_type="text/html", input_method="url"),
        _fingerprint("official_docs", "web_docs_adapter", "html"),
        html.encode(),
    )

    by_name = {row.contract_name: row for row in result.normalized_rows}
    assert by_name["SablierLockup"].role == "protocol_contract"
    assert by_name["SablierBatchLockup"].role == "batch_contract"
    assert by_name["LockupNFTDescriptor"].role == "nft_descriptor"
    assert by_name["LockupMath"].role == "math_library"
    assert all(row.raw_reference["role_fallback_used"] is False for row in result.normalized_rows)


def test_missing_network_structured_source_preserves_candidate_with_warning() -> None:
    html = """
    <h1>Deployments</h1>
    <table>
      <tr><th>Contract</th><th>Address</th></tr>
      <tr><td>SomeContract</td><td>0x1111111111111111111111111111111111111111</td></tr>
    </table>
    """

    result = ExtractionPipeline().run(
        _artifact("https://docs.example.org/deployments", content_type="text/html", input_method="url"),
        _fingerprint("official_docs", "web_docs_adapter", "html"),
        html.encode(),
    )

    row = result.normalized_rows[0]
    assert row.network is None
    assert row.role == "some_contract"
    assert "missing_network" in row.warnings
    assert row.confidence_initial < 90
    assert result.candidates_preview[0].suggested_role == "some_contract"


def test_unrecognized_network_structured_source_preserves_candidate_with_warning() -> None:
    html = """
    <h1>Deployments</h1>
    <table>
      <tr><th>Network</th><th>Contract</th><th>Address</th></tr>
      <tr><td>NewChainXYZ</td><td>SomeContract</td><td>0x1111111111111111111111111111111111111111</td></tr>
    </table>
    """

    result = ExtractionPipeline().run(
        _artifact("https://docs.example.org/deployments", content_type="text/html", input_method="url"),
        _fingerprint("official_docs", "web_docs_adapter", "html"),
        html.encode(),
    )

    row = result.normalized_rows[0]
    assert row.network == "NewChainXYZ"
    assert row.role == "some_contract"
    assert "unrecognized_network" in row.warnings
    assert result.candidates_preview[0].source_network == "NewChainXYZ"


def test_loose_fallback_does_not_invent_role_from_junk_context() -> None:
    text = "random address 0x1111111111111111111111111111111111111111"

    result = ExtractionPipeline().run(
        _artifact("https://example.org/raw.txt", content_type="text/plain", input_method="url"),
        _fingerprint("official_website", "web_docs_adapter", "plain_text"),
        text.encode(),
        allow_loose_fallback=True,
    )

    assert len(result.normalized_rows) == 1
    assert result.normalized_rows[0].role is None
    assert result.normalized_rows[0].raw_reference["role_fallback_used"] is False
