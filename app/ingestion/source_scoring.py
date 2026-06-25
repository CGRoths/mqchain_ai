from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urlparse

from app.ingestion.address_recognizer import AddressNetworkResolution, DEFAULT_RECOGNIZER


SOURCE_TRUST_CAPS = {
    "official_verified": 100,
    "official_likely": 92,
    "third_party_officially_referenced": 88,
    "third_party_audit": 82,
    "third_party_exchange_reported": 84,
    "manual_verified": 80,
    "third_party_unverified": 55,
    "manual_unverified": 45,
    "unknown": 35,
    "rejected": 0,
}

OFFICIAL_SOURCE_TYPES = {
    "official_site",
    "official_website",
    "official_docs",
    "official_github",
}
GITHUB_SOURCE_TYPES = {"github_blob", "github_raw", "github_directory", "official_github", "third_party_github"}
AUDIT_SOURCE_TYPES = {"audit_report", "por_report", "por_pdf", "pdf_url", "pdf", "audit_or_por_document", "pdf_por_document", "pdf_audit_table"}
MANUAL_SOURCE_TYPES = {"manual_paste", "manual_seed", "plain_text", "csv_upload", "excel_upload", "pdf_upload", "unknown"}
STRONG_MANUAL_VERIFICATIONS = {"checked", "official_checked", "manually_verified", "manual_verified"}


@dataclass(frozen=True)
class SourceEvidenceBlock:
    source_url: str | None = None
    source_name: str | None = None
    source_type: str | None = None
    entity_hint: str | None = None
    protocol_hint: str | None = None
    network_hint: str | int | None = None
    uploaded_filename: str | None = None
    sheet_name: str | None = None
    table_name: str | None = None
    heading: str | None = None
    source_url_domain: str | None = None
    retrieved_at: str | None = None
    manual_verification: str | None = None
    operator_notes: str | None = None
    extra_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SourceEvidenceInput = SourceEvidenceBlock


@dataclass(frozen=True)
class SourceScoreResult:
    source_score: int
    source_trust: str
    domain_authority_score: int
    source_type_score: int
    source_identity_alignment_score: int
    source_integrity_score: int
    freshness_score: int
    source_conflict_penalty: int
    confidence_cap: int
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AddressNetworkScoreResult:
    address_network_score: int
    resolution: AddressNetworkResolution
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["resolution"] = self.resolution.to_dict()
        return data


@dataclass(frozen=True)
class CandidateConfidenceResult:
    candidate_confidence: int
    source_score: int
    source_trust_score: int
    source_identity_score: int
    address_network_score: int
    onchain_behavior_score: int
    review_quality_score: int
    conflict_penalty: int
    confidence_cap_applied: bool
    confidence_cap: int
    evidence: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryPermissionResult:
    discovery_depth: int
    discovery_permission: str
    can_expand_cluster: bool
    can_use_as_seed: bool
    requires_review_before_expansion: bool
    reason: str
    approval_readiness: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SourceScoringService:
    def score_source(self, evidence: SourceEvidenceBlock) -> SourceScoreResult:
        source_type = _source_type(evidence.source_type)
        manual_verification = _manual_verification(evidence.manual_verification)
        domain = _source_domain(evidence)
        identity_score, identity_evidence = _identity_alignment_score(evidence, domain)
        official_domain_match = identity_evidence.get("official_domain_match") is True
        github_org_match = identity_evidence.get("github_org_match") is True

        domain_authority = _domain_authority_score(evidence, domain, official_domain_match, github_org_match, manual_verification)
        source_type_score = _source_type_score(source_type, manual_verification)
        integrity = _source_integrity_score(evidence, domain)
        freshness = _freshness_score(evidence.retrieved_at)
        conflict_penalty, conflict_warnings = _source_conflict_penalty(evidence, source_type, domain, identity_score, official_domain_match, github_org_match)
        trust = _source_trust(evidence, source_type, domain, official_domain_match, github_org_match, manual_verification)

        raw_score = (
            domain_authority * 0.35
            + source_type_score * 0.20
            + identity_score * 0.20
            + integrity * 0.15
            + freshness * 0.10
            - conflict_penalty
        )
        source_score = _clamp(round(raw_score))
        confidence_cap = SOURCE_TRUST_CAPS.get(trust, SOURCE_TRUST_CAPS["unknown"])
        warnings = list(conflict_warnings)
        if not evidence.source_url and manual_verification not in STRONG_MANUAL_VERIFICATIONS:
            warnings.append("source_url_missing_caps_trust")
        if source_type in OFFICIAL_SOURCE_TYPES and trust not in {"official_verified", "official_likely", "manual_verified"}:
            warnings.append("official_source_type_is_not_source_trust_without_verification")

        return SourceScoreResult(
            source_score=source_score,
            source_trust=trust,
            domain_authority_score=domain_authority,
            source_type_score=source_type_score,
            source_identity_alignment_score=identity_score,
            source_integrity_score=integrity,
            freshness_score=freshness,
            source_conflict_penalty=conflict_penalty,
            confidence_cap=confidence_cap,
            evidence={
                "source_evidence": evidence.to_dict(),
                "domain": domain,
                "identity_alignment": identity_evidence,
                "formula": "domain*0.35 + source_type*0.20 + identity*0.20 + integrity*0.15 + freshness*0.10 - penalty",
            },
            warnings=_dedupe(warnings),
        )

    def score_address_network(self, address: str | None, evidence: SourceEvidenceBlock) -> AddressNetworkScoreResult:
        resolution = DEFAULT_RECOGNIZER.resolve_with_context(
            address,
            network_hint=evidence.network_hint,
            source_context=_source_context(evidence),
        )
        warnings = list(resolution.warnings)
        method = resolution.resolution_method
        if method == "no_format_match":
            score = 0
        elif method == "network_format_conflict":
            score = 5
            warnings.append("address_network_conflict")
        elif method == "network_hint":
            score = min(100, max(90, resolution.confidence))
        elif method == "source_context":
            score = min(84, max(72, resolution.confidence))
            warnings.append("address_chain_resolved_from_weak_context")
        elif method == "format_exact":
            score = min(90, max(76, resolution.confidence))
        else:
            score = 45 if resolution.address_family == "evm20" else 35
            warnings.append("address_chain_unresolved")
            if resolution.address_family in {"evm20", "hex32"}:
                warnings.append("missing_network_for_ambiguous_address")

        return AddressNetworkScoreResult(
            address_network_score=_clamp(score),
            resolution=resolution,
            evidence={"address_resolution": resolution.to_dict(), "source_context": _source_context(evidence)},
            warnings=_dedupe(warnings),
        )

    def score_candidate(
        self,
        source_score: SourceScoreResult,
        source_identity_score: int,
        address_network_score: int,
        onchain_behavior_score: int = 0,
        review_quality_score: int = 0,
        conflict_penalty: int = 0,
        evidence: dict[str, Any] | None = None,
    ) -> CandidateConfidenceResult:
        evidence = evidence or {}
        raw = (
            source_score.source_score * 0.30
            + source_identity_score * 0.20
            + address_network_score * 0.15
            + onchain_behavior_score * 0.25
            + review_quality_score * 0.10
            - conflict_penalty
        )
        candidate = _clamp(round(raw))
        cap = source_score.confidence_cap
        reviewer_override = evidence.get("manual_reviewer_override") is True or evidence.get("reviewer_override") is True
        cap_applied = candidate > cap and not reviewer_override
        if cap_applied:
            candidate = cap
        warnings = list(source_score.warnings)
        if cap_applied:
            warnings.append("candidate_confidence_capped_by_source_trust")
        if conflict_penalty:
            warnings.append("candidate_conflict_penalty_applied")
        return CandidateConfidenceResult(
            candidate_confidence=candidate,
            source_score=source_score.source_score,
            source_trust_score=source_score.source_score,
            source_identity_score=_clamp(source_identity_score),
            address_network_score=_clamp(address_network_score),
            onchain_behavior_score=_clamp(onchain_behavior_score),
            review_quality_score=_clamp(review_quality_score),
            conflict_penalty=_clamp(conflict_penalty),
            confidence_cap_applied=cap_applied,
            confidence_cap=cap,
            evidence={
                **evidence,
                "formula": "source*0.30 + identity*0.20 + address_network*0.15 + onchain*0.25 + review*0.10 - penalty",
            },
            warnings=_dedupe(warnings),
        )

    def determine_discovery_permission(
        self,
        source_score: SourceScoreResult,
        candidate_score: CandidateConfidenceResult,
        address_network_warnings: list[str] | None = None,
        conflict_warnings: list[str] | None = None,
    ) -> DiscoveryPermissionResult:
        address_network_warnings = list(address_network_warnings or [])
        conflict_warnings = list(conflict_warnings or [])
        warnings = _dedupe([*source_score.warnings, *candidate_score.warnings, *address_network_warnings, *conflict_warnings])
        strong_conflict = bool(conflict_warnings) or any("conflict" in warning for warning in warnings)
        missing_ambiguous_network = any(warning in {"missing_network_for_ambiguous_address", "address_chain_unresolved"} for warning in warnings)

        if strong_conflict:
            return DiscoveryPermissionResult(0, "blocked_conflict", False, False, True, "strong_conflict", "blocked_conflict", warnings)
        if missing_ambiguous_network:
            depth = 1 if candidate_score.candidate_confidence >= 55 else 0
            return DiscoveryPermissionResult(depth, "review_required_validation_only", False, False, True, "missing_network_for_ambiguous_address", "blocked_missing_network", warnings)

        trust = source_score.source_trust
        confidence = candidate_score.candidate_confidence
        if trust == "official_verified" and confidence >= 90:
            return DiscoveryPermissionResult(3, "trusted_seed_expansion", True, True, False, "official_verified_high_confidence", "auto_ready_official_verified", warnings)
        if trust in {"official_likely", "third_party_officially_referenced", "third_party_exchange_reported"} and confidence >= 80:
            readiness = "needs_review_official_likely" if trust == "official_likely" else "needs_review_third_party_official_reference"
            return DiscoveryPermissionResult(2, "review_gated_cluster_expansion", False, True, True, trust, readiness, warnings)
        if trust == "third_party_audit" and confidence >= 75:
            depth = 1 if warnings else 2
            permission = "one_hop_validation_only" if depth == 1 else "review_gated_cluster_expansion"
            return DiscoveryPermissionResult(depth, permission, False, depth >= 2, True, "third_party_audit", "needs_review_third_party_audit", warnings)
        if trust in {"third_party_unverified", "manual_unverified"}:
            depth = 1 if confidence >= 60 else 0
            permission = "one_hop_validation_only" if depth == 1 else "extract_only"
            return DiscoveryPermissionResult(depth, permission, False, False, True, trust, "needs_review_unverified_source" if depth else "extract_only_low_confidence", warnings)
        if trust == "manual_verified" and confidence >= 75:
            return DiscoveryPermissionResult(1, "one_hop_validation_only", False, False, True, "manual_verified", "needs_review_manual_verified", warnings)
        return DiscoveryPermissionResult(0, "extract_only", False, False, True, "unknown_or_low_confidence", "extract_only_low_confidence", warnings)


def _source_type(value: str | None) -> str:
    source_type = str(value or "unknown").strip().lower()
    aliases = {
        "official_website": "official_site",
        "official_site": "official_site",
        "official_docs": "official_docs",
        "github": "github_blob",
        "url": "unknown",
        "paste": "manual_paste",
    }
    return aliases.get(source_type, source_type)


def _manual_verification(value: str | None) -> str:
    return str(value or "unverified").strip().lower()


def _source_domain(evidence: SourceEvidenceBlock) -> str | None:
    explicit = str(evidence.source_url_domain or "").strip().lower().removeprefix("www.")
    if explicit:
        return explicit
    parsed = urlparse(evidence.source_url or "")
    return parsed.netloc.lower().removeprefix("www.") or None


def _domain_authority_score(
    evidence: SourceEvidenceBlock,
    domain: str | None,
    official_domain_match: bool,
    github_org_match: bool,
    manual_verification: str,
) -> int:
    if official_domain_match:
        return 98 if manual_verification == "official_checked" else 90
    if github_org_match:
        return 85
    if not domain:
        return 75 if manual_verification in STRONG_MANUAL_VERIFICATIONS else 20
    if domain in {"github.com", "raw.githubusercontent.com"}:
        return 50
    return 45


def _source_type_score(source_type: str, manual_verification: str) -> int:
    if source_type in {"official_site", "official_docs"}:
        return 92
    if source_type in GITHUB_SOURCE_TYPES:
        return 82
    if source_type in AUDIT_SOURCE_TYPES:
        return 78
    if source_type in {"manual_seed", "manual_paste", "plain_text"}:
        return 62 if manual_verification in STRONG_MANUAL_VERIFICATIONS else 35
    if source_type in {"csv_upload", "excel_upload", "pdf_upload"}:
        return 45
    return 25


def _source_integrity_score(evidence: SourceEvidenceBlock, domain: str | None) -> int:
    score = 0
    if evidence.source_url:
        score += 55 if str(evidence.source_url).startswith("https://") else 35
    elif evidence.uploaded_filename or evidence.operator_notes:
        score += 30
    if domain:
        score += 20
    if evidence.retrieved_at:
        score += 10
    if evidence.extra_context:
        score += 10
    return _clamp(score)


def _freshness_score(retrieved_at: str | None) -> int:
    if not retrieved_at:
        return 60
    try:
        parsed = datetime.fromisoformat(str(retrieved_at).replace("Z", "+00:00"))
    except ValueError:
        return 50
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_days = max(0, (datetime.now(timezone.utc) - parsed).days)
    if age_days <= 30:
        return 95
    if age_days <= 180:
        return 80
    if age_days <= 730:
        return 65
    return 45


def _source_conflict_penalty(
    evidence: SourceEvidenceBlock,
    source_type: str,
    domain: str | None,
    identity_score: int,
    official_domain_match: bool,
    github_org_match: bool,
) -> tuple[int, list[str]]:
    warnings: list[str] = []
    penalty = 0
    if source_type in OFFICIAL_SOURCE_TYPES and domain and not official_domain_match and not github_org_match:
        penalty += 25
        warnings.append("official_source_type_without_identity_alignment")
    if evidence.entity_hint and domain and identity_score < 35:
        penalty += 10
        warnings.append("entity_hint_not_aligned_to_source")
    return _clamp(penalty), warnings


def _source_trust(
    evidence: SourceEvidenceBlock,
    source_type: str,
    domain: str | None,
    official_domain_match: bool,
    github_org_match: bool,
    manual_verification: str,
) -> str:
    verified = _verified_trust_from_extra_context(evidence.extra_context)
    if verified:
        return verified
    if manual_verification == "official_checked" and (official_domain_match or github_org_match):
        return "official_verified"
    if manual_verification in {"manually_verified", "manual_verified", "checked", "official_checked"}:
        return "manual_verified"
    if domain:
        return "third_party_unverified"
    if source_type in MANUAL_SOURCE_TYPES:
        return "manual_unverified"
    return "unknown"


def _verified_trust_from_extra_context(extra_context: dict[str, Any]) -> str | None:
    verification = extra_context.get("source_verification")
    if not isinstance(verification, dict):
        return None
    trust = str(verification.get("source_trust") or "").strip()
    status = str(verification.get("verification_status") or "").strip()
    if trust not in SOURCE_TRUST_CAPS:
        return None
    if status == "rejected" or trust == "rejected":
        return "rejected"
    if status not in {"verified", "approved", "active"}:
        return None
    if not verification.get("verified_by") or not verification.get("verified_at"):
        return None
    return trust


def _identity_alignment_score(evidence: SourceEvidenceBlock, domain: str | None) -> tuple[int, dict[str, Any]]:
    hints = _hint_slugs([evidence.entity_hint, evidence.protocol_hint])
    if not hints:
        return 0, {"hint_slugs": [], "matched_fields": []}

    root_label = _root_label(domain)
    github_org = _github_org(evidence)
    fields = {
        "domain": root_label,
        "github_org": github_org,
        "source_name": evidence.source_name,
        "source_url_path": urlparse(evidence.source_url or "").path,
        "uploaded_filename": evidence.uploaded_filename,
        "sheet_name": evidence.sheet_name,
        "table_name": evidence.table_name,
        "heading": evidence.heading,
        "operator_notes": evidence.operator_notes,
        "extra_context": " ".join(str(item) for item in _flatten(evidence.extra_context)),
    }
    scores: list[int] = []
    matched_fields: list[str] = []
    for field_name, value in fields.items():
        tokens = _text_tokens(value)
        if not tokens:
            continue
        for hint in hints:
            if _slug_matches_tokens(hint, tokens):
                matched_fields.append(field_name)
                if field_name == "domain":
                    scores.append(92)
                elif field_name == "github_org":
                    scores.append(88)
                elif field_name in {"source_name", "uploaded_filename", "sheet_name", "heading"}:
                    scores.append(60)
                else:
                    scores.append(45)
    score = max(scores, default=0)
    if len(set(matched_fields)) >= 2:
        score = min(95, score + 15)
    return _clamp(score), {
        "hint_slugs": hints,
        "matched_fields": _dedupe(matched_fields),
        "official_domain_match": "domain" in matched_fields,
        "github_org_match": "github_org" in matched_fields,
    }


def _source_context(evidence: SourceEvidenceBlock) -> dict[str, Any]:
    return {
        "source_url": evidence.source_url,
        "source_name": evidence.source_name,
        "uploaded_filename": evidence.uploaded_filename,
        "sheet_name": evidence.sheet_name,
        "table_name": evidence.table_name,
        "heading": evidence.heading,
        "extra_context": evidence.extra_context,
    }


def _hint_slugs(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        tokens = [token for token in _text_tokens(value) if token not in _generic_tokens()]
        if tokens:
            result.append("-".join(tokens))
            result.extend(tokens)
    return _dedupe(result)


def _slug_matches_tokens(slug: str, tokens: set[str]) -> bool:
    parts = [part for part in slug.split("-") if part]
    if not parts:
        return False
    compact = "".join(parts)
    token_compact = "".join(tokens)
    return slug in tokens or compact in tokens or all(part in tokens for part in parts) or compact in token_compact


def _root_label(domain: str | None) -> str | None:
    if not domain:
        return None
    parts = [part for part in domain.split(".") if part]
    if len(parts) < 2:
        return domain
    return parts[-2]


def _github_org(evidence: SourceEvidenceBlock) -> str | None:
    for key in ("github_owner", "github_org", "owner"):
        value = evidence.extra_context.get(key)
        if value:
            return str(value)
    parsed = urlparse(evidence.source_url or "")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "github.com" and parts:
        return parts[0]
    if host == "raw.githubusercontent.com" and parts:
        return parts[0]
    return None


def _text_tokens(value: Any) -> set[str]:
    raw = str(value or "")
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    return {token.lower() for token in re.split(r"[^A-Za-z0-9]+", expanded) if token and not token.isdigit()}


def _generic_tokens() -> set[str]:
    return {
        "address",
        "addresses",
        "audit",
        "contracts",
        "deployment",
        "deployments",
        "docs",
        "official",
        "por",
        "proof",
        "reserve",
        "reserves",
        "wallet",
        "wallets",
    }


def _flatten(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        result: list[Any] = []
        for key, item in value.items():
            result.append(key)
            result.extend(_flatten(item))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            result.extend(_flatten(item))
        return result
    return [value]


def _clamp(value: int | float) -> int:
    return max(0, min(100, int(round(value))))


def _dedupe(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
