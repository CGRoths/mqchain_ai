from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.ingestion.source_identity import SourceIdentityCandidate
from app.ingestion.source_signal_extractor import SourceSignals


TRUST_LEVELS = {
    "official_verified",
    "official_likely",
    "third_party_officially_referenced",
    "third_party_audit",
    "third_party_exchange_reported",
    "third_party_unverified",
    "manual_verified",
    "manual_unverified",
    "unknown",
    "rejected",
}
GITHUB_TYPES = {"github_blob", "github_raw", "github_directory", "official_github"}
UPLOAD_TYPES = {"csv_upload", "excel_upload", "pdf_upload", "manual_seed", "plain_text"}
AUDIT_TYPES = {"por_pdf", "audit_report", "pdf_url"}


@dataclass(slots=True)
class SourceTrustClassification:
    trust_level: str
    trust_score: int
    trust_method: str
    official_status: str
    matched_signals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_source_trust(
    signals: SourceSignals,
    identity: SourceIdentityCandidate | None,
    *,
    final_source_type: str | None = None,
    metadata: dict[str, Any] | None = None,
    allow_manual_override: bool = True,
) -> SourceTrustClassification:
    metadata = metadata or {}
    source_type = (final_source_type or _metadata_source_type(metadata) or "").strip().lower()
    automatic = _automatic_trust(signals, identity, source_type, metadata)
    if not allow_manual_override:
        return automatic
    override = _manual_override(metadata, automatic)
    return override or automatic


def evidence_type_for_trust(
    *,
    final_source_type: str | None,
    trust: SourceTrustClassification,
    source_url: str | None = None,
    content_type: str | None = None,
) -> str:
    source_type = (final_source_type or "").strip().lower()
    content = (content_type or "").strip().lower()
    if source_type in AUDIT_TYPES or "pdf" in content:
        return "pdf_por_document"
    if source_type == "csv_upload":
        return "csv_wallet_list"
    if source_type == "excel_upload":
        return "excel_wallet_list"
    if source_type in {"manual_seed", "plain_text"}:
        return "manual_seed_context"
    if source_type == "pdf_upload":
        return "pdf_por_document"
    if source_type in GITHUB_TYPES:
        return "github_deployment_source"
    if source_type == "official_docs":
        return "docs_deployment_source"
    if source_type == "official_website":
        return "docs_deployment_source"
    if source_url:
        return "third_party_reserve_page" if trust.trust_level.startswith("third_party") else "url_source_context"
    return "source_extraction_context"


def _automatic_trust(
    signals: SourceSignals,
    identity: SourceIdentityCandidate | None,
    source_type: str,
    metadata: dict[str, Any],
) -> SourceTrustClassification:
    matched: list[str] = []
    verified = _verified_trust_from_metadata(metadata)
    if verified is not None:
        return verified
    if _is_officially_referenced(signals, metadata):
        return SourceTrustClassification(
            trust_level="third_party_officially_referenced",
            trust_score=82,
            trust_method="official_outbound_reference",
            official_status="referenced_third_party",
            matched_signals=["official_source_outbound_link"],
        )
    if source_type in UPLOAD_TYPES and not signals.source_url:
        return SourceTrustClassification(
            trust_level="manual_unverified",
            trust_score=45 + min(20, int((identity.identity_confidence if identity else 0) / 5)),
            trust_method="manual_upload_without_verification",
            official_status="not_official",
            matched_signals=["manual_upload"],
        )
    if source_type in AUDIT_TYPES:
        level = "third_party_unverified" if signals.source_url else "manual_unverified"
        return SourceTrustClassification(
            trust_level=level,
            trust_score=55 if level == "third_party_unverified" else 45,
            trust_method="unverified_audit_or_por_source",
            official_status=level,
            matched_signals=["audit_or_por_shape"],
            warnings=["audit_or_por_shape_is_not_source_trust"],
        )
    if identity and identity.entity_slug:
        root_label = (signals.root_domain or "").split(".")[0]
        if root_label == identity.entity_slug:
            matched.append("root_domain_matches_identity")
        if signals.github_org and identity.entity_slug in _tokenish(signals.github_org):
            matched.append("github_org_matches_identity")
        if signals.github_repo and identity.entity_slug in _tokenish(signals.github_repo):
            matched.append("github_repo_matches_identity")
        if signals.source_url:
            return SourceTrustClassification(
                trust_level="third_party_unverified",
                trust_score=max(20, min(65, identity.identity_confidence)),
                trust_method="third_party_identity_mention",
                official_status="third_party_unverified",
                matched_signals=identity.matched_signals,
                warnings=["identity_alignment_is_not_source_trust"],
            )
    if signals.source_url:
        return SourceTrustClassification(
            trust_level="unknown",
            trust_score=25,
            trust_method="url_without_identity_agreement",
            official_status="unknown",
            warnings=["source_identity_unknown"],
        )
    return SourceTrustClassification("unknown", 0, "no_source_signals", "unknown", warnings=["source_unknown"])


def _manual_override(metadata: dict[str, Any], automatic: SourceTrustClassification) -> SourceTrustClassification | None:
    if metadata.get("manual_trust_override") is not True:
        return None
    requested = str(metadata.get("manual_trust_level") or "").strip()
    if requested not in {"manual_verified", "third_party_officially_referenced", "third_party_audit", "third_party_exchange_reported", "official_verified"}:
        return SourceTrustClassification(
            trust_level=automatic.trust_level,
            trust_score=automatic.trust_score,
            trust_method=automatic.trust_method,
            official_status=automatic.official_status,
            matched_signals=automatic.matched_signals,
            warnings=[*automatic.warnings, "manual_trust_override_invalid_or_ignored"],
        )
    if not metadata.get("manual_verified_by"):
        return SourceTrustClassification(
            trust_level=automatic.trust_level,
            trust_score=automatic.trust_score,
            trust_method=automatic.trust_method,
            official_status=automatic.official_status,
            matched_signals=automatic.matched_signals,
            warnings=[*automatic.warnings, "manual_trust_override_missing_verified_by"],
        )
    score = 95 if requested == "official_verified" else 90 if requested == "manual_verified" else 82
    return SourceTrustClassification(
        trust_level=requested,
        trust_score=score,
        trust_method="manual_trust_override",
        official_status=requested,
        matched_signals=["manual_trust_override"],
    )


def _metadata_source_type(metadata: dict[str, Any]) -> str | None:
    for key in ("final_source_type", "source_type", "requested_source_type"):
        value = metadata.get(key)
        if value:
            return str(value)
    return None


def _is_officially_referenced(signals: SourceSignals, metadata: dict[str, Any]) -> bool:
    references = set()
    for key in ("official_source_outbound_urls", "official_outbound_urls", "verification_urls"):
        value = metadata.get(key)
        if isinstance(value, str):
            references.add(value)
        elif isinstance(value, (list, tuple, set)):
            references.update(str(item) for item in value if item)
    verification_url = metadata.get("manual_verification_url")
    if verification_url:
        references.add(str(verification_url))
    source_url = signals.final_url or signals.source_url
    if source_url and source_url in references:
        return True
    source_host = urlparse(source_url or "").netloc.lower().removeprefix("www.")
    return bool(source_host and any(urlparse(ref).netloc.lower().removeprefix("www.") == source_host for ref in references))


def _verified_trust_from_metadata(metadata: dict[str, Any]) -> SourceTrustClassification | None:
    verification = metadata.get("source_verification")
    if not isinstance(verification, dict):
        return None
    trust = str(verification.get("source_trust") or "").strip()
    status = str(verification.get("verification_status") or "").strip()
    if trust not in TRUST_LEVELS:
        return None
    if status == "rejected" or trust == "rejected":
        return SourceTrustClassification("rejected", 0, "source_verification_record", "rejected", matched_signals=["source_verification"], warnings=["source_rejected"])
    if status not in {"verified", "approved", "active"}:
        return None
    if not verification.get("verified_by") or not verification.get("verified_at"):
        return None
    score = {
        "official_verified": 98,
        "official_likely": 90,
        "third_party_officially_referenced": 86,
        "third_party_exchange_reported": 84,
        "third_party_audit": 82,
        "manual_verified": 80,
    }.get(trust, 55)
    return SourceTrustClassification(
        trust_level=trust,
        trust_score=score,
        trust_method="source_verification_record",
        official_status=trust,
        matched_signals=["source_verification"],
    )


def _tokenish(value: str) -> set[str]:
    return {token for token in value.lower().replace("_", "-").split("-") if token}
