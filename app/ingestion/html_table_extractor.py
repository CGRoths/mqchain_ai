from __future__ import annotations

from html.parser import HTMLParser

from app.ingestion.deployment_extractor import deployment_tables_from_structured_tables
from app.ingestion.extractors.common import evidence_type_for_source


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self.heading: str | None = None
        self._tag_stack: list[str] = []
        self._current_heading: list[str] | None = None
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        self._tag_stack.append(tag)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
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
            if text:
                self.heading = text
            self._current_heading = None
        elif tag in {"th", "td"} and self._current_cell is not None and self._current_row is not None:
            self._current_row.append(" ".join("".join(self._current_cell).split()))
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None and self._current_table is not None:
            if any(cell.strip() for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            table = _table_from_rows(self._current_table, self.heading, len(self.tables) + 1)
            if table:
                self.tables.append(table)
            self._current_table = None
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)
        elif self._current_heading is not None:
            self._current_heading.append(data)


def extract_html_deployment_tables(html: str, *, source_url: str | None, final_source_type: str | None = "official_docs", evidence_type: str | None = None) -> list[dict]:
    parser = _TableHTMLParser()
    parser.feed(html)
    safe_evidence_type = evidence_type or evidence_type_for_source(
        final_source_type=final_source_type,
        source_url=source_url,
        content_type="text/html",
        text_sample=html,
    )
    return deployment_tables_from_structured_tables(
        parser.tables,
        source_url=source_url,
        source_input_type="docs_html_deployment_table",
        evidence_type=safe_evidence_type,
        text=html,
    )


def _table_from_rows(rows: list[list[str]], heading: str | None, index: int) -> dict | None:
    if not rows:
        return None
    headers = rows[0]
    data_rows = rows[1:]
    if not headers or not data_rows:
        return None
    dict_rows = []
    for row_number, values in enumerate(data_rows, start=2):
        padded = values + [""] * max(0, len(headers) - len(values))
        dict_rows.append({headers[column]: padded[column] for column in range(len(headers))} | {"_row_number": row_number})
    return {"name": f"html_table_{index}", "headers": headers, "rows": dict_rows, "heading": heading, "start_line": 2}
