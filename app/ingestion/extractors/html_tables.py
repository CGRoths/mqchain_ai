from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

from app.ingestion.extraction_models import RawExtractedRow, SourceDocument
from app.ingestion.extractor_base import ExtractorPlugin
from app.ingestion.extractors.common import (
    evidence_type_for,
    looks_like_html,
    raw_rows_to_address_rows,
    source_input_type_for,
)


class HTMLHeadingTableExtractor(ExtractorPlugin):
    name = "html_heading_table_extractor"
    priority = 10

    def supports(self, document: SourceDocument) -> bool:
        text = document.text or ""
        return looks_like_html(text) or "html" in (document.content_type or "").lower()

    def extract(self, document: SourceDocument) -> list[RawExtractedRow]:
        parser = _HeadingTableParser()
        parser.feed(document.text or "")
        source_input_type = source_input_type_for(document, "html")
        evidence_type = evidence_type_for(document)
        rows: list[RawExtractedRow] = []
        for table in parser.tables:
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


class _HeadingTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict[str, Any]] = []
        self._heading_by_level: dict[int, str] = {}
        self._current_heading_level: int | None = None
        self._current_heading: list[str] | None = None
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._current_heading_level = int(tag[1])
            self._current_heading = []
        elif tag == "table":
            self._current_table = []
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in {"th", "td"} and self._current_row is not None:
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._current_heading is not None:
            text = " ".join("".join(self._current_heading).split())
            if text and self._current_heading_level is not None:
                level = self._current_heading_level
                self._heading_by_level = {key: value for key, value in self._heading_by_level.items() if key < level}
                self._heading_by_level[level] = text
            self._current_heading = None
            self._current_heading_level = None
        elif tag in {"th", "td"} and self._current_cell is not None and self._current_row is not None:
            self._current_row.append(" ".join("".join(self._current_cell).split()))
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None and self._current_table is not None:
            if any(cell.strip() for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            table = self._table_from_rows(self._current_table)
            if table:
                self.tables.append(table)
            self._current_table = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)
        elif self._current_heading is not None:
            self._current_heading.append(data)

    def _table_from_rows(self, rows: list[list[str]]) -> dict[str, Any] | None:
        if len(rows) < 2:
            return None
        headers = rows[0]
        if not any(headers):
            return None
        heading_path = [self._heading_by_level[key] for key in sorted(self._heading_by_level)]
        dict_rows = []
        for row_number, values in enumerate(rows[1:], start=2):
            padded = values + [""] * max(0, len(headers) - len(values))
            dict_rows.append({headers[index]: padded[index] for index in range(len(headers))} | {"_row_number": row_number})
        return {
            "name": f"html_table_{len(self.tables) + 1}",
            "headers": headers,
            "rows": dict_rows,
            "heading_path": heading_path,
            "section_heading": heading_path[-1] if heading_path else None,
        }
