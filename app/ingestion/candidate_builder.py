from __future__ import annotations

import json
from typing import Any

from app.ingestion.extraction_models import NormalizedExtractedRow
from app.ingestion.intake_models import CandidatePreview
from app.ingestion.network_normalizer import NetworkNormalizer


DEPLOYMENT_HEADERS = [
    "Entity",
    "Protocol",
    "Category",
    "Network",
    "Chain",
    "Address",
    "Contract Name",
    "Role",
    "Evidence Type",
    "Confidence",
    "Source URL",
    "Source Row / Line",
    "Raw Row JSON",
]


class CandidatePreviewFactory:
    def from_normalized_rows(
        self,
        rows: list[NormalizedExtractedRow],
    ) -> tuple[list[dict[str, Any]], list[CandidatePreview], dict[str, Any]]:
        deduped = self._dedupe(rows)
        table_preview = self._table_preview(deduped)
        candidates = [self._candidate(row) for row in deduped]
        metadata = self._metadata(deduped)
        return table_preview, candidates, metadata

    def _candidate(self, row: NormalizedExtractedRow) -> CandidatePreview:
        effective_network = row.network or self._default_network_for_row(row)
        network = NetworkNormalizer.normalize(effective_network)
        raw_reference = {
            **row.raw_reference,
            "source_document_key": row.source_document_key,
            "source_file_path": row.source_file_path,
            "original_value": row.address,
            "normalized_value": row.normalized_address,
            "confidence_source": row.confidence_source,
            "confidence_parser": row.confidence_parser,
            "confidence_role": row.confidence_role,
        }
        if effective_network and not row.network:
            raw_reference.setdefault("network_inference_reason", "default_evm_network_for_pipeline_row")
        source_type = str(raw_reference.get("final_source_type") or raw_reference.get("source_type") or "deployment_source")
        return CandidatePreview(
            address=row.address,
            normalized_address=row.normalized_address,
            entity_name=row.entity_name,
            source_network=effective_network,
            chain_guess=network.chain_guess or row.address_family,
            chain_slug=network.canonical_chain,
            chain_id=row.chain_id if row.chain_id is not None else network.chain_id,
            address_family=row.address_family,
            suggested_role=row.role,
            confidence_initial=row.confidence_initial,
            source_score=row.source_score,
            source_trust=row.source_trust_level,
            source_identity_score=row.source_identity_score,
            address_network_score=row.address_network_score,
            candidate_confidence=row.candidate_confidence,
            confidence_cap=row.confidence_cap,
            discovery_depth=row.discovery_depth,
            discovery_permission=row.discovery_permission,
            approval_readiness=row.approval_readiness,
            scoring_warnings=row.scoring_warnings,
            status="needs_review",
            source_type=source_type,
            source_input_type=row.source_input_type,
            source_sheet=None,
            source_row=_source_row(row),
            source_page=None,
            source_url=row.source_url,
            file_path=row.source_file_path,
            evidence_type=row.evidence_type,
            warnings=row.warnings,
            raw_reference=raw_reference,
        )


    @staticmethod
    def _default_network_for_row(row: NormalizedExtractedRow) -> str | None:
        if row.address_family != "evm":
            return None
        if row.source_input_type not in {
            "github_solidity_address_book",
            "github_json_deployment_registry",
            "github_markdown_deployment_table",
            "official_github_deployment_table",
        }:
            return None

        haystack = " ".join(
            str(value)
            for value in [
                row.source_url,
                row.source_file_path,
                row.raw_reference.get("source_url") if isinstance(row.raw_reference, dict) else None,
                row.raw_reference.get("source_file_path") if isinstance(row.raw_reference, dict) else None,
                row.raw_reference.get("github_directory_path") if isinstance(row.raw_reference, dict) else None,
            ]
            if value
        ).lower()

        if "arbitrum" in haystack:
            return "arbitrum"
        if "base" in haystack:
            return "base"
        if "optimism" in haystack:
            return "optimism"
        if "polygon" in haystack or "matic" in haystack:
            return "polygon"
        if "bsc" in haystack or "bnb" in haystack:
            return "bsc"
        if "avalanche" in haystack or "avax" in haystack:
            return "avalanche-c"
        if "ethereum" in haystack or "mainnet" in haystack or "eth" in haystack:
            return "ethereum"

        return "ethereum"


    def _table_preview(self, rows: list[NormalizedExtractedRow]) -> list[dict[str, Any]]:
        if not rows:
            return []
        return [
            {
                "name": "normalized_deployment_rows",
                "headers": DEPLOYMENT_HEADERS,
                "rows": [
                    {
                        "Entity": row.entity_name,
                        "Protocol": row.protocol_name,
                        "Category": row.category,
                        "Network": row.network or self._default_network_for_row(row),
                        "Chain": row.network or self._default_network_for_row(row),
                        "Address": row.address,
                        "Contract Name": row.contract_name or row.wallet_label,
                        "Role": row.role,
                        "Evidence Type": row.evidence_type,
                        "Confidence": str(row.confidence_initial),
                        "Source URL": row.source_url,
                        "Source Row / Line": _source_row(row),
                        "Raw Row JSON": json.dumps(row.raw_reference, sort_keys=True, default=str),
                        "_row_number": _source_row(row),
                    }
                    for row in rows
                ],
                "start_line": _source_row(rows[0]) or 1,
                "metadata": {
                    "source_input_type": rows[0].source_input_type,
                    "evidence_type": rows[0].evidence_type,
                    "entity_name": rows[0].entity_name,
                    "protocol_name": rows[0].protocol_name,
                    "category": rows[0].category,
                    "sub_category": rows[0].sub_category,
                },
            }
        ]

    def _metadata(self, rows: list[NormalizedExtractedRow]) -> dict[str, Any]:
        if not rows:
            return {"table_count": 0}
        return {
            "source_input_type": rows[0].source_input_type,
            "entity_name": _first(row.entity_name for row in rows),
            "protocol_name": _first(row.protocol_name for row in rows),
            "category": _first(row.category for row in rows) or "unknown",
            "sub_category": _first(row.sub_category for row in rows),
            "expected_roles": sorted({row.role for row in rows if row.role}),
            "evidence_types": sorted({row.evidence_type for row in rows if row.evidence_type}),
            "source_document_keys": sorted({row.source_document_key for row in rows if row.source_document_key}),
            "source_file_paths": sorted({row.source_file_path for row in rows if row.source_file_path}),
            "source_score_min": min((row.source_score for row in rows if row.source_score is not None), default=None),
            "candidate_confidence_min": min((row.candidate_confidence for row in rows if row.candidate_confidence is not None), default=None),
            "discovery_permissions": sorted({row.discovery_permission for row in rows if row.discovery_permission}),
            "approval_readiness": sorted({row.approval_readiness for row in rows if row.approval_readiness}),
            "table_count": 1,
        }

    @staticmethod
    def _dedupe(rows: list[NormalizedExtractedRow]) -> list[NormalizedExtractedRow]:
        seen: set[tuple[str | None, str, str | None, str | None, str | None]] = set()
        result: list[NormalizedExtractedRow] = []
        for row in rows:
            key = (
                row.network,
                row.normalized_address,
                row.contract_name,
                row.role,
                row.source_file_path,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result


def _source_row(row: NormalizedExtractedRow) -> int | None:
    value = row.raw_reference.get("row_number") or row.raw_reference.get("line_number")
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _first(values) -> str | None:
    for value in values:
        if value:
            return value
    return None
