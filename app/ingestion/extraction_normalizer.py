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
        role = self.profiles.infer_role(profile, role_values)
        label_type = raw_row.extracted_label_type or self.profiles.infer_label_type(profile, role)
        confidence_parser = raw_row.confidence_parser or _parser_confidence(raw_row.extractor_name)
        confidence_role = 90 if role else 50
        confidence_initial = _confidence_initial(
            network=network_label,
            role=role,
            confidence_parser=confidence_parser,
        )
        warnings = list(raw_row.warnings)
        if network_label and not normalized_network.canonical_chain:
            warnings.append("unrecognized_source_network")
        if not network_label:
            warnings.append("missing_network_context")

        raw_reference = {
            "extractor_name": raw_row.extractor_name,
            "heading_path": raw_row.heading_path,
            "section_heading": raw_row.section_heading,
            "raw_row": raw_row.raw_row,
            "source_url": raw_row.source_url,
            "source_file_path": raw_row.source_file_path,
            "source_document_key": raw_row.source_document_key,
            "line_number": raw_row.line_number,
            "row_number": raw_row.row_number,
            "json_path": raw_row.json_path,
            "column_name": raw_row.column_name,
            "raw_key": raw_row.raw_key,
            "raw_value": _json_safe(raw_row.raw_value),
            "contract_name": contract_name,
            "role_source": raw_row.extracted_role_hint or raw_row.raw_key or raw_row.column_name or contract_name,
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
        for value in (
            raw_row.extracted_network,
            _raw_get(raw_row.raw_row, "Network", "Chain", "Blockchain"),
            raw_row.section_heading,
            *reversed(raw_row.heading_path),
            _network_from_path(raw_row.source_file_path),
            _network_from_path(urlparse(raw_row.source_url or "").path),
        ):
            label = normalize_network_label(value)
            if label:
                return label
        return None


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


def _confidence_initial(*, network: str | None, role: str | None, confidence_parser: int) -> int:
    score = confidence_parser
    if network:
        score += 5
    if role:
        score += 5
    return max(0, min(100, score))


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
