from __future__ import annotations

import re

from app.ingestion.extraction_models import RawExtractedRow, SourceDocument
from app.ingestion.extractor_base import ExtractorPlugin
from app.ingestion.extractors.common import evidence_type_for, source_fields, source_input_type_for


SOLIDITY_CONSTANT_RE = re.compile(
    r"(?P<type>(?:address|I[A-Za-z0-9_]+))\s+"
    r"(?:(?:public|internal|private|external)\s+)?"
    r"constant\s+"
    r"(?P<name>[A-Z0-9_]+)\s*=\s*"
    r"(?:(?P<cast>I[A-Za-z0-9_]+)\s*\(\s*)?"
    r"(?P<address>0x[a-fA-F0-9]{40})",
)


class SolidityConstantExtractor(ExtractorPlugin):
    name = "solidity_constant_extractor"
    priority = 30

    def supports(self, document: SourceDocument) -> bool:
        text = document.text or ""
        path = (document.source_file_path or document.filename or "").lower()
        return path.endswith(".sol") or "pragma solidity" in text[:8192] or bool(SOLIDITY_CONSTANT_RE.search(text[:8192]))

    def extract(self, document: SourceDocument) -> list[RawExtractedRow]:
        text = document.text or ""
        lines = text.splitlines()
        source_input_type = source_input_type_for(document, "solidity")
        evidence_type = evidence_type_for(document)
        rows: list[RawExtractedRow] = []
        for match in SOLIDITY_CONSTANT_RE.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            name = match.group("name")
            comment = _nearby_comment(lines, line_number)
            rows.append(
                RawExtractedRow(
                    extractor_name=self.name,
                    source_input_type=source_input_type,
                    evidence_type=evidence_type,
                    line_number=line_number,
                    column_name="solidity_constant",
                    raw_key=name,
                    raw_value=match.group("address"),
                    raw_row={
                        "raw_line": _source_line(lines, line_number),
                        "comment": comment,
                        "constant_name": name,
                        "type": match.group("type"),
                        "cast": match.group("cast"),
                    },
                    extracted_address=match.group("address"),
                    extracted_contract_name=name,
                    extracted_role_hint=" ".join(value for value in (name, match.group("type"), match.group("cast"), comment) if value),
                    confidence_source="solidity_constant",
                    confidence_parser=90,
                    **source_fields(document),
                )
            )
        return rows


def _nearby_comment(lines: list[str], line_number: int) -> str | None:
    comments = []
    for index in range(line_number - 2, max(-1, line_number - 5), -1):
        stripped = lines[index].strip()
        if not stripped:
            if comments:
                break
            continue
        if stripped.startswith("//"):
            comments.insert(0, stripped.lstrip("/").strip())
            continue
        break
    return " ".join(comments) or None


def _source_line(lines: list[str], line_number: int) -> str | None:
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()
    return None
