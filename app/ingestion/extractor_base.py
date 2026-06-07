from __future__ import annotations

from collections.abc import Iterable

from app.ingestion.extraction_models import RawExtractedRow, SourceDocument


class ExtractorPlugin:
    name = "base_extractor"
    priority = 100
    is_loose_fallback = False

    def supports(self, document: SourceDocument) -> bool:
        return False

    def extract(self, document: SourceDocument) -> list[RawExtractedRow]:
        return []


class ExtractorRegistry:
    def __init__(self, extractors: Iterable[ExtractorPlugin] | None = None) -> None:
        self._extractors: list[ExtractorPlugin] = []
        for extractor in extractors or []:
            self.register(extractor)

    def register(self, extractor: ExtractorPlugin) -> None:
        self._extractors.append(extractor)
        self._extractors.sort(key=lambda item: (item.priority, item.name))

    def get_supported_extractors(self, document: SourceDocument) -> list[ExtractorPlugin]:
        return [extractor for extractor in self._extractors if self._supports(extractor, document)]

    def run(
        self,
        document: SourceDocument,
        *,
        allow_loose_fallback: bool = False,
    ) -> tuple[list[RawExtractedRow], list[str], dict[str, dict]]:
        rows: list[RawExtractedRow] = []
        warnings: list[str] = []
        stats = self._initial_stats(document)

        structured_extractors = [
            extractor
            for extractor in self._extractors
            if not extractor.is_loose_fallback and stats[extractor.name]["supported"]
        ]
        loose_extractors = [
            extractor
            for extractor in self._extractors
            if extractor.is_loose_fallback and stats[extractor.name]["supported"]
        ]

        for extractor in structured_extractors:
            extracted = self._run_one(extractor, document, warnings, stats)
            rows.extend(extracted)

        if rows:
            for extractor in loose_extractors:
                stats[extractor.name]["skipped_reason"] = "structured_rows_found"
            return rows, _dedupe(warnings), stats

        if not allow_loose_fallback:
            for extractor in loose_extractors:
                stats[extractor.name]["skipped_reason"] = "loose_fallback_disabled"
            return rows, _dedupe(warnings), stats

        for extractor in loose_extractors:
            rows.extend(self._run_one(extractor, document, warnings, stats))
        return rows, _dedupe(warnings), stats

    def _initial_stats(self, document: SourceDocument) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        for extractor in self._extractors:
            supported = self._supports(extractor, document)
            stats[extractor.name] = {
                "supported": supported,
                "rows_found": 0,
                "warnings": [],
                "skipped_reason": None if supported else "unsupported",
            }
        return stats

    @staticmethod
    def _supports(extractor: ExtractorPlugin, document: SourceDocument) -> bool:
        try:
            return bool(extractor.supports(document))
        except Exception:
            return False

    @staticmethod
    def _run_one(
        extractor: ExtractorPlugin,
        document: SourceDocument,
        warnings: list[str],
        stats: dict[str, dict],
    ) -> list[RawExtractedRow]:
        try:
            rows = extractor.extract(document)
        except Exception:
            warning = f"extractor_failed:{extractor.name}"
            warnings.append(warning)
            stats[extractor.name]["warnings"].append(warning)
            stats[extractor.name]["skipped_reason"] = "extractor_failed"
            return []

        stats[extractor.name]["rows_found"] = len(rows)
        row_warnings = _dedupe([warning for row in rows for warning in row.warnings])
        stats[extractor.name]["warnings"].extend(row_warnings)
        warnings.extend(row_warnings)
        return rows


def _dedupe(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
