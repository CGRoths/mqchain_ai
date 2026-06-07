from __future__ import annotations

from app.ingestion.address_utils import ADDRESS_RE
from app.ingestion.extraction_models import RawExtractedRow, SourceDocument
from app.ingestion.extractor_base import ExtractorPlugin
from app.ingestion.extractors.common import source_fields


class LooseAddressExtractor(ExtractorPlugin):
    name = "loose_address_extractor"
    priority = 1000
    is_loose_fallback = True

    def supports(self, document: SourceDocument) -> bool:
        return bool(document.text)

    def extract(self, document: SourceDocument) -> list[RawExtractedRow]:
        rows: list[RawExtractedRow] = []
        for line_number, line in enumerate((document.text or "").splitlines(), start=1):
            for address in ADDRESS_RE.findall(line):
                rows.append(
                    RawExtractedRow(
                        extractor_name=self.name,
                        source_input_type="loose_address_fallback",
                        evidence_type="source_extraction_context",
                        line_number=line_number,
                        raw_value=line,
                        raw_row={"raw_line": line},
                        extracted_address=address,
                        confidence_source="loose_address_pattern",
                        confidence_parser=55,
                        warnings=["loose_address_fallback_used"],
                        **source_fields(document),
                    )
                )
        return rows
