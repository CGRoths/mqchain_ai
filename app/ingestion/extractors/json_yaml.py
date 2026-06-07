from __future__ import annotations

import json
import re
from typing import Any

from app.ingestion.address_utils import ADDRESS_RE
from app.ingestion.extraction_models import RawExtractedRow, SourceDocument
from app.ingestion.extractor_base import ExtractorPlugin
from app.ingestion.extractors.common import evidence_type_for, source_fields, source_input_type_for


class JsonYamlAddressExtractor(ExtractorPlugin):
    name = "json_yaml_address_extractor"
    priority = 20

    def supports(self, document: SourceDocument) -> bool:
        text = (document.text or "").lstrip()
        path = (document.source_file_path or document.filename or "").lower()
        content_type = (document.content_type or "").lower()
        return (
            path.endswith((".json", ".jsonc", ".yaml", ".yml"))
            or "json" in content_type
            or "yaml" in content_type
            or text.startswith(("{", "["))
            or bool(re.search(r"^[A-Za-z0-9_.-]+\s*:\s*", text, flags=re.MULTILINE))
        )

    def extract(self, document: SourceDocument) -> list[RawExtractedRow]:
        text = document.text or ""
        stripped = text.lstrip()
        path = (document.source_file_path or document.filename or "").lower()
        if path.endswith((".yaml", ".yml")):
            return self._extract_yaml(document)
        if stripped.startswith(("{", "[")):
            rows = self._extract_json(document)
            if rows:
                return rows
        return self._extract_yaml(document)

    def _extract_json(self, document: SourceDocument) -> list[RawExtractedRow]:
        try:
            data = json.loads(document.text or "")
        except json.JSONDecodeError:
            return []
        source_input_type = source_input_type_for(document, "json")
        evidence_type = evidence_type_for(document)
        rows: list[RawExtractedRow] = []
        for path, value in _walk_json(data):
            if isinstance(value, str):
                for address in ADDRESS_RE.findall(value):
                    raw_key = path[-1] if path else None
                    rows.append(
                        RawExtractedRow(
                            extractor_name=self.name,
                            source_input_type=source_input_type,
                            evidence_type=evidence_type,
                            json_path=path,
                            raw_key=raw_key,
                            raw_value=value,
                            raw_row={"path": path, "value": value},
                            extracted_address=address,
                            extracted_contract_name=raw_key,
                            extracted_role_hint=raw_key,
                            confidence_source="structured_json_path",
                            confidence_parser=90,
                            **source_fields(document),
                        )
                    )
        return rows

    def _extract_yaml(self, document: SourceDocument) -> list[RawExtractedRow]:
        source_input_type = source_input_type_for(document, "yaml")
        evidence_type = evidence_type_for(document)
        rows: list[RawExtractedRow] = []
        stack: list[tuple[int, str]] = []
        for line_number, line in enumerate((document.text or "").splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            key_match = re.match(r"^([A-Za-z0-9_. -]+)\s*:\s*(.*)$", stripped)
            if key_match:
                key = key_match.group(1).strip()
                value = key_match.group(2).strip().strip("'\"")
                stack.append((indent, key))
            else:
                key = stack[-1][1] if stack else None
                value = stripped.strip("- ").strip("'\"")
            path = [item for _, item in stack]
            for address in ADDRESS_RE.findall(value):
                rows.append(
                    RawExtractedRow(
                        extractor_name=self.name,
                        source_input_type=source_input_type,
                        evidence_type=evidence_type,
                        line_number=line_number,
                        json_path=path,
                        raw_key=key,
                        raw_value=value,
                        raw_row={"path": path, "raw_line": line.strip()},
                        extracted_address=address,
                        extracted_contract_name=key,
                        extracted_role_hint=key,
                        confidence_source="structured_yaml_path",
                        confidence_parser=85,
                        **source_fields(document),
                    )
                )
        return rows


def _walk_json(value: Any, path: list[str] | None = None):
    path = path or []
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_json(item, [*path, str(key)])
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_json(item, [*path, str(index)])
