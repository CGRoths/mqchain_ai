from __future__ import annotations

from app.ingestion.candidate_builder import CandidatePreviewFactory
from app.ingestion.extraction_models import RawExtractedRow
from app.ingestion.extraction_normalizer import ExtractionNormalizer
from app.ingestion.source_scoring import SourceEvidenceBlock, SourceScoringService


EVM_ADDRESS = "0x1111111111111111111111111111111111111111"
BTC_ADDRESS = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"


def test_official_okx_source_scores_high_without_fake_url_matching() -> None:
    score = SourceScoringService().score_source(
        SourceEvidenceBlock(
            source_url="https://www.okx.com/proof-of-reserves",
            source_type="official_site",
            entity_hint="OKX",
        )
    )

    assert score.source_score >= 80
    assert score.source_trust == "third_party_unverified"
    assert score.confidence_cap == 55
    assert "official_source_type_is_not_source_trust_without_verification" in score.warnings


def test_random_okx_csv_has_identity_alignment_but_low_source_trust() -> None:
    score = SourceScoringService().score_source(
        SourceEvidenceBlock(
            source_url="https://random.example.com/okx_wallets.csv",
            source_type="csv_upload",
            uploaded_filename="okx_wallets.csv",
            entity_hint="OKX",
        )
    )

    assert score.source_identity_alignment_score > 0
    assert score.source_trust in {"third_party_unverified", "unknown"}
    assert score.confidence_cap <= 55


def test_manual_paste_without_url_is_extract_only() -> None:
    scoring = SourceScoringService()
    source = scoring.score_source(SourceEvidenceBlock(source_type="manual_paste", entity_hint="Binance", manual_verification="unverified"))
    candidate = scoring.score_candidate(source, source.source_identity_alignment_score, 45, review_quality_score=30)
    permission = scoring.determine_discovery_permission(source, candidate)

    assert source.source_trust == "manual_unverified"
    assert source.source_score < 50
    assert permission.discovery_depth == 0
    assert permission.discovery_permission == "extract_only"


def test_manual_official_checked_binance_source_can_raise_trust() -> None:
    score = SourceScoringService().score_source(
        SourceEvidenceBlock(
            source_url="https://www.binance.com/en/proof-of-reserves",
            source_type="manual_seed",
            entity_hint="Binance",
            manual_verification="official_checked",
        )
    )

    assert score.source_trust in {"official_verified", "official_likely", "manual_verified"}
    assert score.confidence_cap >= 80


def test_evm_address_without_network_is_blocked_missing_network() -> None:
    scoring = SourceScoringService()
    source = scoring.score_source(SourceEvidenceBlock(source_url="https://www.okx.com/proof-of-reserves", source_type="official_site", entity_hint="OKX"))
    address = scoring.score_address_network(EVM_ADDRESS, SourceEvidenceBlock(source_url="https://www.okx.com/proof-of-reserves", source_type="official_site", entity_hint="OKX"))
    candidate = scoring.score_candidate(source, source.source_identity_alignment_score, address.address_network_score, onchain_behavior_score=80, review_quality_score=80)
    permission = scoring.determine_discovery_permission(source, candidate, address_network_warnings=address.warnings)

    assert address.resolution.address_family == "evm20"
    assert address.resolution.resolved_chain == "evm_unknown"
    assert address.address_network_score < 60
    assert permission.approval_readiness == "blocked_missing_network"


def test_evm_address_with_base_hint_scores_high() -> None:
    result = SourceScoringService().score_address_network(
        EVM_ADDRESS,
        SourceEvidenceBlock(network_hint="base", source_url="https://www.okx.com/proof-of-reserves", source_type="official_site", entity_hint="OKX"),
    )

    assert result.resolution.resolved_chain == "base"
    assert result.resolution.chain_id == 8453
    assert result.address_network_score >= 90


def test_evm_address_with_bitcoin_hint_blocks_conflict() -> None:
    scoring = SourceScoringService()
    source = scoring.score_source(SourceEvidenceBlock(source_url="https://www.okx.com/proof-of-reserves", source_type="official_site", entity_hint="OKX"))
    address = scoring.score_address_network(EVM_ADDRESS, SourceEvidenceBlock(network_hint="bitcoin", source_url="https://www.okx.com/proof-of-reserves", source_type="official_site", entity_hint="OKX"))
    candidate = scoring.score_candidate(source, source.source_identity_alignment_score, address.address_network_score, conflict_penalty=45)
    permission = scoring.determine_discovery_permission(source, candidate, address_network_warnings=address.warnings, conflict_warnings=address.warnings)

    assert address.resolution.resolution_method == "network_format_conflict"
    assert address.address_network_score <= 5
    assert permission.approval_readiness == "blocked_conflict"
    assert permission.discovery_depth == 0


def test_btc_bech32_with_okx_btc_source_scores_high_address_network() -> None:
    result = SourceScoringService().score_address_network(
        BTC_ADDRESS,
        SourceEvidenceBlock(network_hint="bitcoin", source_url="https://www.okx.com/proof-of-reserves", source_type="official_site", entity_hint="OKX"),
    )

    assert result.resolution.resolved_chain == "bitcoin"
    assert result.address_network_score >= 90


def test_filename_and_sheet_context_resolve_base_weaker_than_explicit_hint() -> None:
    scoring = SourceScoringService()
    contextual = scoring.score_address_network(
        EVM_ADDRESS,
        SourceEvidenceBlock(uploaded_filename="addresses.csv", sheet_name="Base Mainnet", source_type="csv_upload", entity_hint="Example"),
    )
    explicit = scoring.score_address_network(
        EVM_ADDRESS,
        SourceEvidenceBlock(network_hint="base", uploaded_filename="addresses.csv", sheet_name="Base Mainnet", source_type="csv_upload", entity_hint="Example"),
    )

    assert contextual.resolution.resolved_chain == "base"
    assert contextual.resolution.resolution_method == "source_context"
    assert contextual.address_network_score < explicit.address_network_score


def test_weak_source_with_good_onchain_score_is_capped_by_source_trust() -> None:
    scoring = SourceScoringService()
    source = scoring.score_source(
        SourceEvidenceBlock(
            source_url="https://random.example.com/binance_wallets.csv",
            source_type="csv_upload",
            uploaded_filename="binance_wallets.csv",
            entity_hint="Binance",
        )
    )
    candidate = scoring.score_candidate(
        source,
        source_identity_score=90,
        address_network_score=95,
        onchain_behavior_score=100,
        review_quality_score=95,
    )

    assert source.source_trust == "third_party_unverified"
    assert candidate.confidence_cap_applied is True
    assert candidate.candidate_confidence == source.confidence_cap


def test_conflict_penalty_blocks_discovery_even_for_official_source() -> None:
    scoring = SourceScoringService()
    source = scoring.score_source(SourceEvidenceBlock(source_url="https://www.okx.com/proof-of-reserves", source_type="official_site", entity_hint="OKX"))
    candidate = scoring.score_candidate(source, 95, 95, onchain_behavior_score=95, review_quality_score=95, conflict_penalty=70)
    permission = scoring.determine_discovery_permission(source, candidate, conflict_warnings=["entity_conflict"])

    assert candidate.candidate_confidence < 90
    assert permission.approval_readiness == "blocked_conflict"
    assert permission.discovery_depth == 0


def test_discovery_permission_tiers() -> None:
    scoring = SourceScoringService()
    official = scoring.score_source(SourceEvidenceBlock(source_url="https://www.okx.com/proof-of-reserves", source_type="official_site", entity_hint="OKX", manual_verification="official_checked"))
    unverified_official_url = scoring.score_source(SourceEvidenceBlock(source_url="https://www.okx.com/proof-of-reserves", source_type="official_site", entity_hint="OKX"))
    verified_exchange = scoring.score_source(
        SourceEvidenceBlock(
            source_url="https://www.okx.com/proof-of-reserves",
            source_type="official_site",
            entity_hint="OKX",
            extra_context={
                "source_verification": {
                    "verification_status": "verified",
                    "source_trust": "third_party_exchange_reported",
                    "verified_by": "pytest",
                    "verified_at": "2026-06-26T00:00:00Z",
                }
            },
        )
    )
    unverified_audit = scoring.score_source(SourceEvidenceBlock(source_url="https://audits.example.com/okx.pdf", source_type="audit_report", entity_hint="OKX"))
    verified_audit = scoring.score_source(
        SourceEvidenceBlock(
            source_url="https://audits.example.com/okx.pdf",
            source_type="audit_report",
            entity_hint="OKX",
            extra_context={
                "source_verification": {
                    "verification_status": "verified",
                    "source_trust": "third_party_audit",
                    "verified_by": "pytest",
                    "verified_at": "2026-06-26T00:00:00Z",
                }
            },
        )
    )
    weak = scoring.score_source(SourceEvidenceBlock(source_url="https://random.example.com/okx.csv", source_type="csv_upload", entity_hint="OKX"))

    assert scoring.determine_discovery_permission(official, scoring.score_candidate(official, 95, 95, 100, 100)).discovery_depth == 3
    assert scoring.determine_discovery_permission(unverified_official_url, scoring.score_candidate(unverified_official_url, 95, 95, 100, 100)).discovery_depth == 0
    assert scoring.determine_discovery_permission(verified_exchange, scoring.score_candidate(verified_exchange, 95, 95, 100, 100)).discovery_depth == 2
    assert scoring.determine_discovery_permission(unverified_audit, scoring.score_candidate(unverified_audit, 65, 95, 100, 100)).discovery_depth == 0
    assert scoring.determine_discovery_permission(verified_audit, scoring.score_candidate(verified_audit, 65, 95, 100, 100)).discovery_depth in {1, 2}
    assert scoring.determine_discovery_permission(weak, scoring.score_candidate(weak, 65, 95, 100, 100)).discovery_depth <= 1


def test_normalizer_preserves_conflict_candidate_with_blocking_scoring_metadata() -> None:
    row = RawExtractedRow(
        extractor_name="json_yaml_address_extractor",
        source_input_type="json_deployment_registry",
        evidence_type="source_extraction_context",
        source_url="https://www.okx.com/proof-of-reserves",
        raw_row={
            "source_evidence": {
                "source_url": "https://www.okx.com/proof-of-reserves",
                "source_type": "official_site",
                "entity_hint": "OKX",
                "network_hint": "bitcoin",
            }
        },
        extracted_address=EVM_ADDRESS,
        extracted_contract_name="ReserveWallet",
        extracted_role_hint="cex_por_wallet",
    )

    normalized = ExtractionNormalizer().normalize(row)

    assert normalized is not None
    assert normalized.network == "bitcoin"
    assert normalized.approval_readiness == "blocked_conflict"
    assert normalized.discovery_depth == 0
    assert normalized.raw_reference["address_network_score"]["resolution"]["resolution_method"] == "network_format_conflict"


def test_candidate_preview_exposes_scoring_fields() -> None:
    row = RawExtractedRow(
        extractor_name="json_yaml_address_extractor",
        source_input_type="json_deployment_registry",
        evidence_type="source_extraction_context",
        source_url="https://www.okx.com/proof-of-reserves",
        raw_row={
            "source_evidence": {
                "source_url": "https://www.okx.com/proof-of-reserves",
                "source_type": "official_site",
                "entity_hint": "OKX",
                "network_hint": "bitcoin",
            }
        },
        extracted_address=BTC_ADDRESS,
        extracted_role_hint="cex_por_wallet",
    )

    normalized = ExtractionNormalizer().normalize(row)
    _table, candidates, metadata = CandidatePreviewFactory().from_normalized_rows([normalized])
    candidate = candidates[0]

    assert candidate.source_score is not None
    assert candidate.address_network_score >= 90
    assert candidate.candidate_confidence is not None
    assert candidate.approval_readiness == normalized.approval_readiness
    assert "source_score_min" in metadata
