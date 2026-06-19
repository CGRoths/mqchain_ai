from __future__ import annotations

from typing import Any

from app.ingestion.candidate_builder import CandidatePreviewFactory
from app.ingestion.extraction_models import ExtractionResult, NormalizedExtractedRow, RawExtractedRow
from app.ingestion.extraction_normalizer import ExtractionNormalizer
from app.ingestion.extractor_base import ExtractorRegistry
from app.ingestion.extractors import default_extractor_registry
from app.ingestion.intake_models import SourceArtifact, SourceFingerprint
from app.ingestion.source_resolver import SourceResolver


class ExtractionPipeline:
    def __init__(
        self,
        *,
        resolver: SourceResolver | None = None,
        registry: ExtractorRegistry | None = None,
        normalizer: ExtractionNormalizer | None = None,
        candidate_factory: CandidatePreviewFactory | None = None,
    ) -> None:
        self.resolver = resolver or SourceResolver()
        self.registry = registry or default_extractor_registry()
        self.normalizer = normalizer or ExtractionNormalizer()
        self.candidate_factory = candidate_factory or CandidatePreviewFactory()

    def run(
        self,
        artifact: SourceArtifact,
        fingerprint: SourceFingerprint,
        raw_content: bytes,
        *,
        allow_loose_fallback: bool = False,
    ) -> ExtractionResult:
        resolved = self.resolver.resolve(artifact, fingerprint, raw_content)
        all_raw_rows: list[RawExtractedRow] = []
        all_normalized_rows: list[NormalizedExtractedRow] = []
        warnings = list(resolved.warnings)
        extractor_stats: dict[str, Any] = {}

        for document in resolved.documents:
            raw_rows, extractor_warnings, stats = self.registry.run(
                document,
                allow_loose_fallback=allow_loose_fallback,
            )
            warnings.extend(extractor_warnings)
            extractor_stats[document.source_document_key] = stats
            all_raw_rows.extend(raw_rows)
            if _static_html_table_not_detected(document, stats, raw_rows):
                warnings.append("docs_table_not_detected_static_html")
            for raw_row in raw_rows:
                _enrich_raw_row_from_document(raw_row, document)
                normalized = self.normalizer.normalize(raw_row, text_sample=document.text or "")
                if normalized is None:
                    continue
                normalized.raw_reference.setdefault("final_source_type", document.final_source_type)
                normalized.raw_reference.setdefault("adapter_name", document.adapter_name)
                normalized.raw_reference.setdefault("source_type", document.final_source_type)
                all_normalized_rows.append(normalized)

        all_normalized_rows = _dedupe_normalized_rows(all_normalized_rows)
        table_preview, candidates, candidate_metadata = self.candidate_factory.from_normalized_rows(all_normalized_rows)
        metadata = {
            **resolved.metadata,
            **candidate_metadata,
            "pipeline_enabled": True,
            "raw_row_count": len(all_raw_rows),
            "normalized_row_count": len(all_normalized_rows),
            "extractor_stats": extractor_stats,
            "source_trust_levels": sorted({row.source_trust_level for row in all_normalized_rows if row.source_trust_level}),
            "source_identity_confidences": [row.source_identity_confidence for row in all_normalized_rows if row.source_identity_confidence is not None],
        }
        first_row = all_normalized_rows[0] if all_normalized_rows else None
        return ExtractionResult(
            source_documents=resolved.documents,
            raw_rows=all_raw_rows,
            normalized_rows=all_normalized_rows,
            table_preview=table_preview,
            candidates_preview=candidates,
            warnings=_dedupe(warnings),
            fatal_errors=resolved.fatal_errors,
            extractor_stats=extractor_stats,
            metadata=metadata,
            source_identity=first_row.source_identity if first_row else None,
            source_trust=first_row.source_trust if first_row else None,
            source_trust_level=first_row.source_trust_level if first_row else None,
            source_trust_score=first_row.source_trust_score if first_row else None,
            source_identity_confidence=first_row.source_identity_confidence if first_row else None,
        )


def _dedupe(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _dedupe_normalized_rows(rows: list[NormalizedExtractedRow]) -> list[NormalizedExtractedRow]:
    seen: set[tuple[str | None, str | None, str | None, str, str | None]] = set()
    result: list[NormalizedExtractedRow] = []
    for row in rows:
        key = (
            row.source_url,
            row.contract_name or row.wallet_label,
            row.network,
            row.normalized_address,
            row.source_input_type,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _enrich_raw_row_from_document(raw_row: RawExtractedRow, document) -> None:
    metadata = document.metadata or {}
    if raw_row.extracted_network is None and metadata.get("inferred_network"):
        raw_row.extracted_network = str(metadata["inferred_network"])
    for target, source in (
        ("inferred_network", "inferred_network"),
        ("inferred_market", "inferred_market"),
        ("market", "inferred_market"),
        ("github_owner", "owner"),
        ("github_repo", "repo"),
        ("github_branch", "branch"),
        ("github_directory_path", "directory_path"),
        ("github_api_url", "github_api_url"),
        ("crawler_depth", "crawler_depth"),
        ("root_deployment_scan_mode", "root_deployment_scan_mode"),
    ):
        value = metadata.get(source)
        if value not in {None, ""}:
            raw_row.raw_row.setdefault(target, value)
    source_evidence = metadata.get("source_evidence")
    if isinstance(source_evidence, dict) and source_evidence:
        raw_row.raw_row.setdefault("source_evidence", source_evidence)


def _static_html_table_not_detected(document, stats: dict[str, dict], raw_rows: list[RawExtractedRow]) -> bool:
    html_stats = stats.get("html_heading_table_extractor") or {}
    return (
        not raw_rows
        and html_stats.get("supported") is True
        and html_stats.get("rows_found") == 0
        and document.source_url is not None
        and not (document.final_source_type or "").startswith("github")
    )
