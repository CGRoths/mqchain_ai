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
                normalized = self.normalizer.normalize(raw_row, text_sample=document.text or "")
                if normalized is None:
                    continue
                normalized.raw_reference.setdefault("final_source_type", document.final_source_type)
                normalized.raw_reference.setdefault("adapter_name", document.adapter_name)
                normalized.raw_reference.setdefault("source_type", document.final_source_type)
                all_normalized_rows.append(normalized)

        table_preview, candidates, candidate_metadata = self.candidate_factory.from_normalized_rows(all_normalized_rows)
        metadata = {
            **resolved.metadata,
            **candidate_metadata,
            "pipeline_enabled": True,
            "raw_row_count": len(all_raw_rows),
            "normalized_row_count": len(all_normalized_rows),
            "extractor_stats": extractor_stats,
        }
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
        )


def _dedupe(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _static_html_table_not_detected(document, stats: dict[str, dict], raw_rows: list[RawExtractedRow]) -> bool:
    html_stats = stats.get("html_heading_table_extractor") or {}
    return (
        not raw_rows
        and html_stats.get("supported") is True
        and html_stats.get("rows_found") == 0
        and document.source_url is not None
        and not (document.final_source_type or "").startswith("github")
    )
