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
    valid_address_for_network,
)
from app.ingestion.deployment_extractor import normalize_network_label
from app.ingestion.extraction_models import NormalizedExtractedRow, RawExtractedRow
from app.ingestion.network_normalizer import NetworkNormalizer
from app.ingestion.protocol_profiles import ProtocolProfileRegistry


class ExtractionNormalizer:
    def __init__(self, profiles: ProtocolProfileRegistry | None = None) -> None:
        self.profiles = profiles or ProtocolProfileRegistry()

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
        structured_source = _is_structured_source(raw_row.source_input_type)
        network_label = self._infer_network(raw_row)
        normalized_network = NetworkNormalizer.normalize(network_label)
        if not valid_address_for_network(address, normalized_network):
            return None

        normalized_address = normalize_address(address, normalized_network)
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
            evidence_type=raw_row.evidence_type,
            confidence_parser=confidence_parser,
            structured_source=structured_source,
            role_fallback_used=bool(role_meta["role_fallback_used"]),
            network_status=network_status,
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

        raw_reference = {
            "extractor_name": raw_row.extractor_name,
            "source_input_type": raw_row.source_input_type,
            "evidence_type": raw_row.evidence_type,
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
            "warnings": warnings,
        }

        return NormalizedExtractedRow(
            entity_name=profile.entity_name,
            protocol_name=profile.protocol_name,
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
            evidence_type=raw_row.evidence_type or "source_extraction_context",
            source_input_type=raw_row.source_input_type,
            source_url=raw_row.source_url,
            source_file_path=raw_row.source_file_path,
            source_document_key=raw_row.source_document_key,
            confidence_initial=confidence_initial,
            confidence_source=raw_row.confidence_source or profile.default_confidence_source,
            confidence_parser=confidence_parser,
            confidence_role=confidence_role,
            warnings=_dedupe(warnings),
            raw_reference=raw_reference,
        )

    def _infer_network(self, raw_row: RawExtractedRow) -> str | None:
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


def _raw_get(row: dict[str, Any], *keys: str) -> str | None:
    normalized = {_normalize_key(key): key for key in row}
    for key in keys:
        actual = normalized.get(_normalize_key(key))
        if actual is not None and row.get(actual) not in {None, ""}:
            return str(row.get(actual)).strip()
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
) -> int:
    if structured_source and evidence_type in {"official_docs_deployment", "official_github_deployment", "source_extraction_context"}:
        if evidence_type == "official_docs_deployment" and source_input_type in {"docs_html_deployment_table", "docs_markdown_deployment_table"}:
            if network_status == "recognized" and contract_name:
                return 95
            if network_status == "missing" and contract_name:
                return 75
        if network_status == "recognized" and role and not role_fallback_used:
            return 95
        if network_status == "recognized" and role:
            return 88
        if network_status in {"missing", "unrecognized"} and role:
            return 75
        if contract_name:
            return 65
    if evidence_type == "official_docs_deployment" and source_input_type == "docs_html_deployment_table":
        if network and contract_name:
            return 90
        if contract_name:
            return 75
    score = confidence_parser
    if network:
        score += 5
    if role:
        score += 5
    return max(0, min(100, score))


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
