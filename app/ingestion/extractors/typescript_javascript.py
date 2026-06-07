from __future__ import annotations

import re

from app.ingestion.extraction_models import RawExtractedRow, SourceDocument
from app.ingestion.extractor_base import ExtractorPlugin
from app.ingestion.extractors.common import evidence_type_for, source_fields, source_input_type_for


CONST_ADDRESS_RE = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*[\"'](?P<address>0x[a-fA-F0-9]{40})[\"']",
)
KEY_ADDRESS_RE = re.compile(
    r"(?P<key>[A-Za-z_$][A-Za-z0-9_$-]*)\s*:\s*[\"'](?P<address>0x[a-fA-F0-9]{40})[\"']",
)


class TypeScriptJavascriptAddressExtractor(ExtractorPlugin):
    name = "typescript_javascript_address_extractor"
    priority = 35

    def supports(self, document: SourceDocument) -> bool:
        text = document.text or ""
        path = (document.source_file_path or document.filename or "").lower()
        return path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")) or bool(CONST_ADDRESS_RE.search(text[:8192]))

    def extract(self, document: SourceDocument) -> list[RawExtractedRow]:
        text = document.text or ""
        source_input_type = source_input_type_for(document, "typescript")
        evidence_type = evidence_type_for(document)
        rows: list[RawExtractedRow] = []
        seen: set[tuple[str, str, int]] = set()
        for regex, column_name in ((CONST_ADDRESS_RE, "javascript_constant"), (KEY_ADDRESS_RE, "javascript_object_key")):
            for match in regex.finditer(text):
                name = match.group("name") if "name" in match.groupdict() else match.group("key")
                address = match.group("address")
                line_number = text.count("\n", 0, match.start()) + 1
                key = (name, address.lower(), line_number)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    RawExtractedRow(
                        extractor_name=self.name,
                        source_input_type=source_input_type,
                        evidence_type=evidence_type,
                        line_number=line_number,
                        column_name=column_name,
                        raw_key=name,
                        raw_value=address,
                        raw_row={"raw_line": _source_line(text, line_number), "key": name},
                        extracted_address=address,
                        extracted_contract_name=name,
                        extracted_role_hint=name,
                        confidence_source=column_name,
                        confidence_parser=85,
                        **source_fields(document),
                    )
                )
        return rows


def _source_line(text: str, line_number: int) -> str | None:
    lines = text.splitlines()
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()
    return None
