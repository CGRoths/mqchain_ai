from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.ingestion.address_utils import (
    clean_wallet_address,
    infer_address_family,
    normalize_address,
)
from app.ingestion.deployment_extractor import normalize_network_label
from app.ingestion.extraction_models import NormalizedExtractedRow, RawExtractedRow
from app.ingestion.network_normalizer import NetworkNormalizer
from app.ingestion.protocol_profiles import ProtocolProfileRegistry
from app.ingestion.source_identity import identity_from_profile, infer_source_identity
from app.ingestion.source_scoring import SourceEvidenceBlock, SourceScoringService
from app.ingestion.source_signal_extractor import source_signals_from_raw_row
from app.ingestion.source_trust_classifier import classify_source_trust, evidence_type_for_trust


class ExtractionNormalizer:
    def __init__(self, profiles: ProtocolProfileRegistry | None = None) -> None:
        self.profiles = profiles or ProtocolProfileRegistry()
        self.scoring = SourceScoringService()

    def normalize(self, raw_row: RawExtractedRow, *, text_sample: str = "") -> NormalizedExtractedRow | None:
        address = clean_wallet_address(raw_row.extracted_address)
        if not address:
            return None

        profile = self.profiles.match(
            source_url=raw_row.source_url,
            source_file_path=raw_row.source_file_path,
            text_sample=text_sample,
            entity_hint=_raw_entity_hint(raw_row.raw_row),
        )
        source_signals = source_signals_from_raw_row(raw_row, text_sample=text_sample)
        detected_identity = infer_source_identity(source_signals)
        profile_identity = identity_from_profile(profile.entity_name, profile.protocol_name)
        source_identity = _choose_identity(detected_identity, profile_identity)
        source_type = _source_type_from_row(raw_row)
        automatic_source_trust = classify_source_trust(
            source_signals,
            source_identity,
            final_source_type=source_type,
            metadata=raw_row.raw_row,
            allow_manual_override=False,
        )
        source_trust = classify_source_trust(
            source_signals,
            source_identity,
            final_source_type=source_type,
            metadata=raw_row.raw_row,
        )
        safe_evidence_type = evidence_type_for_trust(
            final_source_type=source_type,
            trust=source_trust,
            source_url=raw_row.source_url,
        )
        structured_source = _is_structured_source(raw_row.source_input_type)
        network_label = self._infer_network(raw_row)
        normalized_network = NetworkNormalizer.normalize(network_label)
        entity_name = profile.entity_name or (source_identity.entity_name if source_identity else None)
        protocol_name = profile.protocol_name or (source_identity.protocol_name if source_identity else None)
        source_evidence = _source_evidence_block(
            raw_row,
            source_type=source_type,
            entity_name=entity_name,
            protocol_name=protocol_name,
            network_label=network_label,
        )
        source_score = self.scoring.score_source(source_evidence)
        address_network_score = self.scoring.score_address_network(address, source_evidence)
        if address_network_score.resolution.resolution_method == "no_format_match":
            return None

        normalized_address = address_network_score.resolution.normalized_address or normalize_address(address, normalized_network)
        address_family = infer_address_family(address)
        contract_name = _first_present(
            raw_row.extracted_contract_name,
            _raw_get(raw_row.raw_row, "Contract Name", "Contract", "Name", "Module"),
            raw_row.raw_key,
            raw_row.column_name,
        )
        wallet_label = _first_present(
            raw_row.extracted_wallet_label,
            _raw_get(raw_row.raw_row, "Wallet Label / Role", "Wallet Label", "Label", "Role"),
            contract_name,
        )
        role_values = [
            raw_row.extracted_role_hint,
            raw_row.extracted_label_type,
            contract_name,
            wallet_label,
            raw_row.raw_key,
            raw_row.column_name,
            raw_row.raw_value,
        ]
        role, role_meta = _infer_role_with_fallback(
            profiles=self.profiles,
            profile=profile,
            raw_row=raw_row,
            role_values=role_values,
            contract_name=contract_name,
            wallet_label=wallet_label,
            structured_source=structured_source,
        )
        label_type = raw_row.extracted_label_type or self.profiles.infer_label_type(profile, role)
        confidence_parser = raw_row.confidence_parser or _parser_confidence(raw_row.extractor_name)
        confidence_role = 90 if role else 50
        network_status = _network_status(network_label, normalized_network)
        confidence_initial = _confidence_initial(
            network=network_label,
            role=role,
            contract_name=contract_name,
            source_input_type=raw_row.source_input_type,
            evidence_type=safe_evidence_type,
            confidence_parser=confidence_parser,
            structured_source=structured_source,
            role_fallback_used=bool(role_meta["role_fallback_used"]),
            network_status=network_status,
            source_trust_level=source_trust.trust_level,
            source_trust_score=source_trust.trust_score,
        )
        warnings = list(raw_row.warnings)
        known_pipeline_network = _known_network_heading(network_label) is not None
        if network_label and not normalized_network.canonical_chain and not known_pipeline_network:
            warnings.append("unrecognized_network" if structured_source else "unrecognized_source_network")
        if not network_label:
            warnings.append("missing_network" if structured_source else "missing_network_context")
        if structured_source and not role and not _meaningful_role_candidate(contract_name or wallet_label):
            warnings.append("missing_role_context")
        if structured_source and _is_long_0x(address) and not network_label:
            warnings.append("unknown_long_0x_address_family")

        conflict_penalty = 45 if address_network_score.resolution.resolution_method == "network_format_conflict" else 0
        candidate_score = self.scoring.score_candidate(
            source_score,
            source_identity_score=source_score.source_identity_alignment_score,
            address_network_score=address_network_score.address_network_score,
            onchain_behavior_score=_optional_int(raw_row.raw_row, "onchain_behavior_score", "onchain_score") or 0,
            review_quality_score=confidence_initial,
            conflict_penalty=conflict_penalty,
            evidence={
                "entity_name": entity_name,
                "protocol_name": protocol_name,
                "role": role,
                "confidence_initial": confidence_initial,
            },
        )
        discovery = self.scoring.determine_discovery_permission(
            source_score,
            candidate_score,
            address_network_warnings=address_network_score.warnings,
            conflict_warnings=[warning for warning in address_network_score.warnings if "conflict" in warning],
        )
        scoring_warnings = _dedupe([*source_score.warnings, *address_network_score.warnings, *candidate_score.warnings, *discovery.warnings])

        raw_reference = {
            "extractor_name": raw_row.extractor_name,
            "source_input_type": raw_row.source_input_type,
            "evidence_type": raw_row.evidence_type,
            "safe_evidence_type": safe_evidence_type,
            "heading_path": raw_row.heading_path,
            "section_heading": raw_row.section_heading,
            "raw_row": raw_row.raw_row,
            "source_url": raw_row.source_url,
            "source_file_path": raw_row.source_file_path,
            "source_document_key": raw_row.source_document_key,
            "line_number": raw_row.line_number,
            "row_number": raw_row.row_number,
            "table_name": raw_row.table_name,
            "json_path": raw_row.json_path,
            "column_name": raw_row.column_name,
            "raw_key": raw_row.raw_key,
            "raw_value": _json_safe(raw_row.raw_value),
            "deployment": _raw_get(raw_row.raw_row, "Deployment"),
            "deployment_version": _raw_get(raw_row.raw_row, "Deployment Version", "Deployment"),
            "inferred_network": _raw_get(raw_row.raw_row, "inferred_network"),
            "inferred_market": _raw_get(raw_row.raw_row, "inferred_market"),
            "market": _raw_get(raw_row.raw_row, "market"),
            "github_owner": _raw_get(raw_row.raw_row, "github_owner"),
            "github_repo": _raw_get(raw_row.raw_row, "github_repo"),
            "github_branch": _raw_get(raw_row.raw_row, "github_branch"),
            "github_directory_path": _raw_get(raw_row.raw_row, "github_directory_path"),
            "github_api_url": _raw_get(raw_row.raw_row, "github_api_url"),
            "crawler_depth": _raw_get(raw_row.raw_row, "crawler_depth"),
            "root_deployment_scan_mode": _raw_get_any(raw_row.raw_row, "root_deployment_scan_mode"),
            "contract_name": contract_name,
            "original_network": raw_row.extracted_network or _raw_get(raw_row.raw_row, "Network", "Chain", "Blockchain"),
            "normalized_network": normalized_network.canonical_chain,
            "role_source": role_meta["role_source"],
            "role_fallback_used": role_meta["role_fallback_used"],
            "role_fallback_source": role_meta["role_fallback_source"],
            "original_role_text": role_meta["original_role_text"],
            "confidence_source": raw_row.confidence_source or profile.default_confidence_source,
            "confidence_parser": confidence_parser,
            "confidence_role": confidence_role,
            "source_signals": source_signals.to_dict(),
            "source_identity": source_identity.to_dict() if source_identity else None,
            "source_trust": source_trust.to_dict(),
            "automatic_source_trust": automatic_source_trust.to_dict(),
            "source_trust_level": source_trust.trust_level,
            "source_trust_score": source_trust.trust_score,
            "source_identity_confidence": source_identity.identity_confidence if source_identity else None,
            "source_evidence": source_evidence.to_dict(),
            "source_score": source_score.to_dict(),
            "source_score_value": source_score.source_score,
            "scored_source_trust": source_score.source_trust,
            "source_identity_score": source_score.source_identity_alignment_score,
            "address_network_score": address_network_score.to_dict(),
            "address_network_score_value": address_network_score.address_network_score,
            "candidate_confidence_score": candidate_score.to_dict(),
            "candidate_confidence": candidate_score.candidate_confidence,
            "confidence_cap": candidate_score.confidence_cap,
            "discovery_permission": discovery.to_dict(),
            "discovery_depth": discovery.discovery_depth,
            "approval_readiness": discovery.approval_readiness,
            "scoring_warnings": scoring_warnings,
            "warnings": warnings,
        }

        return NormalizedExtractedRow(
            entity_name=entity_name,
            protocol_name=protocol_name,
            category=profile.category,
            sub_category=profile.sub_category,
            network=network_label,
            chain_id=normalized_network.chain_id,
            address=address,
            normalized_address=normalized_address,
            address_family=address_family,
            contract_name=contract_name,
            wallet_label=wallet_label,
            role=role,
            label_type=label_type,
            evidence_type=safe_evidence_type or raw_row.evidence_type or "source_extraction_context",
            source_input_type=raw_row.source_input_type,
            source_url=raw_row.source_url,
            source_file_path=raw_row.source_file_path,
            source_document_key=raw_row.source_document_key,
            confidence_initial=confidence_initial,
            confidence_source=raw_row.confidence_source or profile.default_confidence_source,
            confidence_parser=confidence_parser,
            confidence_role=confidence_role,
            source_identity=source_identity.to_dict() if source_identity else None,
            source_trust=source_trust.to_dict(),
            source_trust_level=source_trust.trust_level,
            source_trust_score=source_trust.trust_score,
            source_identity_confidence=source_identity.identity_confidence if source_identity else None,
            source_score=source_score.source_score,
            source_identity_score=source_score.source_identity_alignment_score,
            address_network_score=address_network_score.address_network_score,
            candidate_confidence=candidate_score.candidate_confidence,
            confidence_cap=candidate_score.confidence_cap,
            discovery_depth=discovery.discovery_depth,
            discovery_permission=discovery.discovery_permission,
            approval_readiness=discovery.approval_readiness,
            scoring_warnings=scoring_warnings,
            scoring_metadata={
                "source_evidence": source_evidence.to_dict(),
                "source_score": source_score.to_dict(),
                "address_network_score": address_network_score.to_dict(),
                "candidate_confidence_score": candidate_score.to_dict(),
                "discovery_permission": discovery.to_dict(),
            },
            warnings=_dedupe(warnings),
            raw_reference=raw_reference,
        )

    def _infer_network(self, raw_row: RawExtractedRow) -> str | None:
        source_evidence = raw_row.raw_row.get("source_evidence")
        if isinstance(source_evidence, dict) and source_evidence.get("network_hint") not in {None, ""}:
            return str(source_evidence["network_hint"])
        explicit_network = _known_or_clean_network_label(raw_row.extracted_network) or _known_or_clean_network_label(
            _raw_get(raw_row.raw_row, "Network", "Chain", "Blockchain")
        )
        if explicit_network:
            return explicit_network
        for value in (raw_row.section_heading, *reversed(raw_row.heading_path)):
            label = _known_network_heading(value)
            if label:
                return label
        docs_heading_network = _docs_heading_network_label(raw_row)
        if docs_heading_network:
            return docs_heading_network
        for value in (_network_from_path(raw_row.source_file_path), _network_from_path(urlparse(raw_row.source_url or "").path)):
            label = _known_network_heading(value)
            if label:
                return label

        fallback = _default_evm_network_for_raw_row(raw_row)
        if fallback:
            return fallback

        return None


NETWORK_HEADING_ALIASES = {
    "abstract": "Abstract",
    "arbitrum": "Arbitrum",
    "avalanche": "Avalanche-C",
    "avalanche c": "Avalanche-C",
    "avalanche c chain": "Avalanche-C",
    "avalanche c-chain": "Avalanche-C",
    "base": "Base",
    "bnb": "BNB Chain",
    "bnb chain": "BNB Chain",
    "bsc": "BSC",
    "binance smart chain": "BNB Chain",
    "ethereum": "Ethereum",
    "ethereum mainnet": "Ethereum",
    "mainnet": "Ethereum",
    "optimism": "Optimism",
    "polygon": "Polygon",
    "scroll": "Scroll",
    "sonic": "Sonic",
    "unichain": "Unichain",
    "xdc": "XDC",
    "zksync era": "ZKSync Era",
    "zk sync era": "ZKSync Era",
    "blast": "Blast",
    "linea": "Linea",
    "mantle": "Mantle",
}

STRUCTURED_SOURCE_INPUT_TYPES = {
    "github_solidity_address_book",
    "github_typescript_address_map",
    "github_json_deployment_registry",
    "github_yaml_deployment_registry",
    "github_markdown_deployment_table",
    "docs_html_deployment_table",
    "docs_markdown_deployment_table",
    "json_deployment_registry",
    "yaml_deployment_registry",
    "standardized_registry_upload",
}

USELESS_ROLE_NAMES = {
    "address",
    "contract_address",
    "deployment_address",
    "source",
    "url",
    "value",
    "unknown",
    "none",
    "address_column",
    "solidity_constant",
    "raw_value",
    "table_address",
    "wallet",
    "contract",
}

GENERIC_DOCS_HEADINGS = {
    "deployment",
    "deployments",
    "deployment_address",
    "deployment_addresses",
    "lockup_deployments",
    "flow_deployments",
    "mainnet",
    "mainnets",
    "testnet",
    "testnets",
    "network",
    "networks",
}


def _is_structured_source(source_input_type: str | None) -> bool:
    return source_input_type in STRUCTURED_SOURCE_INPUT_TYPES


def _infer_role_with_fallback(
    *,
    profiles: ProtocolProfileRegistry,
    profile,
    raw_row: RawExtractedRow,
    role_values: list[Any],
    contract_name: str | None,
    wallet_label: str | None,
    structured_source: bool,
) -> tuple[str | None, dict[str, Any]]:
    profile_role = profiles.infer_profile_role(profile, role_values)
    if profile_role:
        source = raw_row.extracted_role_hint or raw_row.raw_key or contract_name or wallet_label
        return profile_role, {
            "role_source": source,
            "role_fallback_used": False,
            "role_fallback_source": None,
            "original_role_text": source,
        }

    if not structured_source:
        return None, {
            "role_source": None,
            "role_fallback_used": False,
            "role_fallback_source": None,
            "original_role_text": None,
        }

    universal = profiles.infer_universal_role(role_values)
    if universal:
        source_name, original = _first_meaningful_role_source(raw_row, contract_name, wallet_label)
        return universal, {
            "role_source": source_name,
            "role_fallback_used": True,
            "role_fallback_source": source_name,
            "original_role_text": original,
        }

    source_name, original = _first_meaningful_role_source(raw_row, contract_name, wallet_label)
    if not source_name or not original:
        return None, {
            "role_source": None,
            "role_fallback_used": False,
            "role_fallback_source": None,
            "original_role_text": None,
        }
    return _to_snake_role(original), {
        "role_source": source_name,
        "role_fallback_used": True,
        "role_fallback_source": source_name,
        "original_role_text": original,
    }


def _first_meaningful_role_source(
    raw_row: RawExtractedRow,
    contract_name: str | None,
    wallet_label: str | None,
) -> tuple[str | None, str | None]:
    candidates = [
        ("contract_name", raw_row.extracted_contract_name),
        ("contract_name", contract_name),
        ("raw_key", raw_row.raw_key),
        ("wallet_label", wallet_label),
        ("raw_row.Contract", _raw_get(raw_row.raw_row, "Contract")),
        ("raw_row.Contract Name", _raw_get(raw_row.raw_row, "Contract Name")),
        ("raw_row.Name", _raw_get(raw_row.raw_row, "Name")),
        ("raw_row.Module", _raw_get(raw_row.raw_row, "Module")),
        ("raw_row.Key", _raw_get(raw_row.raw_row, "Key")),
        ("raw_row.Label", _raw_get(raw_row.raw_row, "Label")),
        ("raw_row.Role", _raw_get(raw_row.raw_row, "Role")),
        ("raw_row.Wallet Label", _raw_get(raw_row.raw_row, "Wallet Label", "Wallet Label / Role")),
        ("column_name", raw_row.column_name),
    ]
    for source_name, value in candidates:
        if _meaningful_role_candidate(value):
            return source_name, str(value).strip()
    return None, None


def _meaningful_role_candidate(value: str | None) -> bool:
    if value in {None, ""}:
        return False
    normalized = _to_snake_role(str(value))
    return normalized not in USELESS_ROLE_NAMES and bool(re.search(r"[a-zA-Z]", str(value)))


def _to_snake_role(value: str) -> str:
    text = str(value).strip()
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text.lower()


def _network_status(network: str | None, normalized_network) -> str:
    if not network:
        return "missing"
    if not normalized_network.canonical_chain and not _known_network_heading(network):
        return "unrecognized"
    return "recognized"


def _known_or_clean_network_label(value: str | None) -> str | None:
    if not value:
        return None
    known = _known_network_heading(value)
    if known:
        return known
    return _clean_original_network_text(value)


def _known_network_heading(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _clean_network_text(value)
    key = cleaned.lower().replace("-", " ")
    if key in NETWORK_HEADING_ALIASES:
        return NETWORK_HEADING_ALIASES[key]
    if NetworkNormalizer.normalize(cleaned).canonical_chain:
        return cleaned
    return None


def _docs_heading_network_label(raw_row: RawExtractedRow) -> str | None:
    if raw_row.source_input_type not in {"docs_html_deployment_table", "docs_markdown_deployment_table"}:
        return None
    if len(raw_row.heading_path) < 2:
        return None
    for value in (raw_row.section_heading, *reversed(raw_row.heading_path)):
        if not value:
            continue
        cleaned = _clean_original_network_text(value)
        if _to_snake_role(cleaned) in GENERIC_DOCS_HEADINGS:
            continue
        return cleaned
    return None


def _clean_network_text(value: str) -> str:
    cleaned = str(value).replace("\u200b", "")
    cleaned = re.sub(r"[_/]+", " ", cleaned).strip()
    cleaned = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _clean_original_network_text(value: str) -> str:
    cleaned = str(value).replace("\u200b", "")
    return re.sub(r"\s+", " ", cleaned).strip()



def _default_evm_network_for_raw_row(raw_row: RawExtractedRow) -> str | None:
    address_family = infer_address_family(raw_row.extracted_address)
    if address_family != "evm":
        return None
    if raw_row.source_input_type not in {
        "github_solidity_address_book",
        "github_json_deployment_registry",
        "github_markdown_deployment_table",
        "official_github_deployment_table",
    }:
        return None

    haystack = " ".join(
        str(value)
        for value in [
            raw_row.source_url,
            raw_row.source_file_path,
            raw_row.raw_row.get("source_url") if isinstance(raw_row.raw_row, dict) else None,
            raw_row.raw_row.get("source_file_path") if isinstance(raw_row.raw_row, dict) else None,
            raw_row.raw_row.get("github_directory_path") if isinstance(raw_row.raw_row, dict) else None,
        ]
        if value
    ).lower()

    if "arbitrum" in haystack:
        return "Arbitrum"
    if "base" in haystack:
        return "Base"
    if "optimism" in haystack:
        return "Optimism"
    if "polygon" in haystack or "matic" in haystack:
        return "Polygon"
    if "bsc" in haystack or "bnb" in haystack:
        return "BSC"
    if "avalanche" in haystack or "avax" in haystack:
        return "Avalanche-C"
    if "ethereum" in haystack or "mainnet" in haystack or "eth" in haystack:
        return "Ethereum"

    return "Ethereum"

def _network_from_path(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.replace("\\", "/")
    stem = Path(normalized).stem
    match = re.search(r"AaveV3([A-Za-z0-9]+)$", stem)
    if match:
        return normalize_network_label(match.group(1))
    parts = [part for part in normalized.split("/") if part]
    lowered = [part.lower() for part in parts]
    for marker in ("deployments", "deployment"):
        if marker in lowered:
            index = lowered.index(marker)
            if index + 1 < len(parts):
                return normalize_network_label(parts[index + 1])
    for part in reversed(parts):
        label = normalize_network_label(part)
        if label and NetworkNormalizer.normalize(label).canonical_chain:
            return label
    return None


def _choose_identity(detected_identity, profile_identity):
    if profile_identity is None:
        return detected_identity
    if detected_identity is None or not detected_identity.entity_slug:
        return profile_identity
    if detected_identity.entity_slug == profile_identity.entity_slug:
        return detected_identity
    if getattr(profile_identity, "identity_method", "") == "protocol_profile_enrichment":
        return profile_identity
    return detected_identity


def _source_type_from_row(raw_row: RawExtractedRow) -> str | None:
    source_evidence = raw_row.raw_row.get("source_evidence")
    if isinstance(source_evidence, dict) and source_evidence.get("source_type"):
        return str(source_evidence["source_type"])
    for value in (
        _raw_get(raw_row.raw_row, "final_source_type", "source_type", "requested_source_type"),
        raw_row.source_input_type,
    ):
        if not value:
            continue
        text = str(value)
        if text.startswith("github_"):
            return "github_blob"
        if text.startswith("docs_"):
            return "official_docs"
        if text.startswith("pdf_"):
            return "pdf_url" if raw_row.source_url else "pdf_upload"
        if text.startswith("xlsx_"):
            return "excel_upload"
        if text.startswith("csv_"):
            return "csv_upload"
    if raw_row.source_url:
        return "official_website"
    return None


def _source_evidence_block(
    raw_row: RawExtractedRow,
    *,
    source_type: str | None,
    entity_name: str | None,
    protocol_name: str | None,
    network_label: str | None,
) -> SourceEvidenceBlock:
    explicit = raw_row.raw_row.get("source_evidence")
    source_evidence = dict(explicit) if isinstance(explicit, dict) else {}
    extra_context = source_evidence.get("extra_context") if isinstance(source_evidence.get("extra_context"), dict) else {}
    preserved_context = {
        key: value
        for key, value in source_evidence.items()
        if key
        in {
            "evidence_shape",
            "official_referrer_url",
            "operator_note",
            "provenance_type",
            "provenance_warnings",
            "source_origin",
            "source_url",
        }
        and value is not None
        and value != ""
    }
    return SourceEvidenceBlock(
        source_url=_first_present(source_evidence.get("source_url"), raw_row.source_url),
        source_name=_first_present(source_evidence.get("source_name"), _raw_get(raw_row.raw_row, "source_name", "Source Name")),
        source_type=_first_present(source_evidence.get("source_type"), source_type),
        entity_hint=_first_present(source_evidence.get("entity_hint"), entity_name, _raw_entity_hint(raw_row.raw_row)),
        protocol_hint=_first_present(source_evidence.get("protocol_hint"), protocol_name),
        network_hint=source_evidence.get("network_hint") if source_evidence.get("network_hint") not in {None, ""} else network_label,
        uploaded_filename=_first_present(
            source_evidence.get("uploaded_filename"),
            _raw_get(raw_row.raw_row, "uploaded_filename", "filename", "source_file_name"),
            Path(raw_row.source_file_path or "").name if raw_row.source_file_path else None,
        ),
        sheet_name=_first_present(source_evidence.get("sheet_name"), _raw_get(raw_row.raw_row, "source_sheet", "sheet_name")),
        table_name=_first_present(source_evidence.get("table_name"), raw_row.table_name),
        heading=_first_present(source_evidence.get("heading"), raw_row.section_heading, " > ".join(raw_row.heading_path)),
        source_url_domain=_first_present(source_evidence.get("source_url_domain"), _raw_get(raw_row.raw_row, "source_url_domain")),
        retrieved_at=_first_present(source_evidence.get("retrieved_at"), _raw_get(raw_row.raw_row, "retrieved_at")),
        manual_verification=_first_present(source_evidence.get("manual_verification"), _raw_get(raw_row.raw_row, "manual_verification")),
        operator_notes=_first_present(source_evidence.get("operator_note"), source_evidence.get("operator_notes"), _raw_get(raw_row.raw_row, "operator_notes", "notes")),
        extra_context={
            **extra_context,
            **preserved_context,
            "source_document_key": raw_row.source_document_key,
            "source_file_path": raw_row.source_file_path,
            "raw_row": raw_row.raw_row,
            "heading_path": raw_row.heading_path,
            "section_heading": raw_row.section_heading,
            "table_name": raw_row.table_name,
        },
    )


def _raw_get(row: dict[str, Any], *keys: str) -> str | None:
    normalized = {_normalize_key(key): key for key in row}
    for key in keys:
        actual = normalized.get(_normalize_key(key))
        if actual is not None and row.get(actual) not in {None, ""}:
            return str(row.get(actual)).strip()
    return None


def _raw_get_any(row: dict[str, Any], *keys: str) -> Any | None:
    normalized = {_normalize_key(key): key for key in row}
    for key in keys:
        actual = normalized.get(_normalize_key(key))
        if actual is not None and row.get(actual) not in {None, ""}:
            return row.get(actual)
    return None


def _optional_int(row: dict[str, Any], *keys: str) -> int | None:
    value = _raw_get_any(row, *keys)
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _raw_entity_hint(row: dict[str, Any]) -> str | None:
    return _raw_get(row, "Entity", "Protocol", "Project")


def _first_present(*values: Any | None) -> str | None:
    for value in values:
        if value not in {None, ""}:
            return str(value).strip()
    return None


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _parser_confidence(extractor_name: str) -> int:
    if "html" in extractor_name or "markdown" in extractor_name:
        return 85
    if "json" in extractor_name or "yaml" in extractor_name:
        return 90
    if "solidity" in extractor_name or "typescript" in extractor_name or "javascript" in extractor_name:
        return 90
    return 60


def _confidence_initial(
    *,
    network: str | None,
    role: str | None,
    contract_name: str | None,
    source_input_type: str,
    evidence_type: str,
    confidence_parser: int,
    structured_source: bool,
    role_fallback_used: bool,
    network_status: str,
    source_trust_level: str | None,
    source_trust_score: int | None,
) -> int:
    base = None
    if structured_source and evidence_type in {"docs_deployment_source", "github_deployment_source", "source_extraction_context"}:
        if evidence_type == "docs_deployment_source" and source_input_type in {"docs_html_deployment_table", "docs_markdown_deployment_table"}:
            if network_status == "recognized" and contract_name:
                base = 95
            if network_status == "missing" and contract_name:
                base = base or 75
        if base is None and network_status == "recognized" and role and not role_fallback_used:
            base = 95
        if base is None and network_status == "recognized" and role:
            base = 88
        if base is None and network_status in {"missing", "unrecognized"} and role:
            base = 75
        if base is None and contract_name:
            base = 65
    if base is None and evidence_type == "docs_deployment_source" and source_input_type == "docs_html_deployment_table":
        if network and contract_name:
            base = 90
        elif contract_name:
            base = 75
    if base is not None:
        score = base
    else:
        score = confidence_parser
        if network:
            score += 5
        if role:
            score += 5
    score = max(0, min(100, score))
    if source_trust_level in {"official_verified", "official_likely", "manual_verified"}:
        return score
    if source_trust_level == "third_party_officially_referenced":
        return min(max(score, 80), 88)
    if source_trust_level == "third_party_audit":
        return min(max(score, 72), 82)
    if source_trust_level == "third_party_unverified":
        return min(score, 65)
    if source_trust_level == "manual_unverified":
        bonus = min(10, int((source_trust_score or 0) / 10))
        return min(max(score, 55 + bonus), 75)
    if source_trust_level == "unknown":
        return min(score, 70 if structured_source and network and (role or contract_name) else 55)
    return score


def _is_long_0x(address: str) -> bool:
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{64}", address))


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _dedupe(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
