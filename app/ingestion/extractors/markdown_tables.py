from __future__ import annotations

import re
from typing import Any

from app.ingestion.extraction_models import RawExtractedRow, SourceDocument
from app.ingestion.extractor_base import ExtractorPlugin
from app.ingestion.extractors.common import (
    evidence_type_for,
    looks_like_markdown_table,
    raw_rows_to_address_rows,
    source_input_type_for,
)


class MarkdownTableExtractor(ExtractorPlugin):
    name = "markdown_table_extractor"
    priority = 40

    def supports(self, document: SourceDocument) -> bool:
        text = document.text or ""
        path = (document.source_file_path or document.filename or "").lower()
        return path.endswith(".md") or looks_like_markdown_table(text)

    def extract(self, document: SourceDocument) -> list[RawExtractedRow]:
        tables = _markdown_tables(document.text or "")
        source_input_type = source_input_type_for(document, "markdown")
        evidence_type = evidence_type_for(document)
        rows: list[RawExtractedRow] = []
        for table in tables:
            rows.extend(
                raw_rows_to_address_rows(
                    document=document,
                    extractor_name=self.name,
                    source_input_type=source_input_type,
                    evidence_type=evidence_type,
                    table_name=table["name"],
                    headers=table["headers"],
                    rows=table["rows"],
                    heading_path=table["heading_path"],
                    section_heading=table["section_heading"],
                )
            )
        return rows


def _markdown_tables(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    heading_by_level: dict[int, str] = {}
    tables: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        heading_match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", lines[index])
        if heading_match:
            level = len(heading_match.group(1))
            heading_by_level = {key: value for key, value in heading_by_level.items() if key < level}
            heading_by_level[level] = heading_match.group(2).strip()
            index += 1
            continue
        if index + 1 >= len(lines) or "|" not in lines[index] or not re.match(r"^\s*\|?[\s|:-]+\|?\s*$", lines[index + 1]):
            index += 1
            continue
        headers = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
        data_rows = []
        index += 2
        row_number = index + 1
        while index < len(lines) and "|" in lines[index]:
            values = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
            data_rows.append({headers[column]: values[column] if column < len(values) else "" for column in range(len(headers))} | {"_row_number": row_number})
            index += 1
            row_number += 1
        heading_path = [heading_by_level[key] for key in sorted(heading_by_level)]
        tables.append(
            {
                "name": f"markdown_table_{len(tables) + 1}",
                "headers": headers,
                "rows": data_rows,
                "heading_path": heading_path,
                "section_heading": heading_path[-1] if heading_path else None,
            }
        )
    return tables
