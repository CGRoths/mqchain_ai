from __future__ import annotations

import re
from typing import Any

from app.ingestion.address_utils import ADDRESS_RE
from app.ingestion.extraction_models import RawExtractedRow, SourceDocument


def evidence_type_for(document: SourceDocument) -> str:
    if (document.final_source_type or "").startswith("github") or document.final_source_type == "official_github":
        return "official_github_deployment"
    if document.source_url:
        return "official_docs_deployment"
    return "source_extraction_context"


def source_input_type_for(document: SourceDocument, kind: str) -> str:
    prefix = "github" if (document.final_source_type or "").startswith("github") or document.final_source_type == "official_github" else "docs"
    if kind == "html":
        return f"{prefix}_html_deployment_table" if prefix == "github" else "docs_html_deployment_table"
    if kind == "markdown":
        return f"{prefix}_markdown_deployment_table" if prefix == "github" else "docs_markdown_deployment_table"
    if kind == "json":
        return f"{prefix}_json_deployment_registry" if prefix == "github" else "json_deployment_registry"
    if kind == "yaml":
        return f"{prefix}_yaml_deployment_registry" if prefix == "github" else "yaml_deployment_registry"
    if kind == "solidity":
        return "github_solidity_address_book" if prefix == "github" else "docs_solidity_address_block"
    if kind == "typescript":
        return "github_typescript_address_map" if prefix == "github" else "docs_typescript_address_block"
    return "source_extraction_context"


def source_fields(document: SourceDocument) -> dict[str, str | None]:
    return {
        "source_url": document.source_url,
        "source_file_path": document.source_file_path,
        "source_document_key": document.source_document_key,
    }


def address_headers(headers: list[str]) -> list[str]:
    return [header for header in headers if "address" in _normalize(header) and "email" not in _normalize(header)]


def first_header(headers: list[str], *names: str) -> str | None:
    normalized = {_normalize(header): header for header in headers}
    for name in names:
        header = normalized.get(_normalize(name))
        if header:
            return header
    return None


def raw_rows_to_address_rows(
    *,
    document: SourceDocument,
    extractor_name: str,
    source_input_type: str,
    evidence_type: str,
    table_name: str,
    headers: list[str],
    rows: list[dict[str, Any]],
    heading_path: list[str],
    section_heading: str | None,
) -> list[RawExtractedRow]:
    result: list[RawExtractedRow] = []
    address_columns = address_headers(headers)
    network_column = first_header(headers, "Network", "Chain", "Blockchain")
    contract_column = first_header(headers, "Contract", "Contract Name", "Name", "Module")
    role_column = first_header(headers, "Role", "Label", "Type", "Purpose")
    for row in rows:
        network = _value(row, network_column)
        contract_name = _value(row, contract_column)
        role_hint = _value(row, role_column)
        if address_columns:
            for column in address_columns:
                for address in ADDRESS_RE.findall(str(row.get(column) or "")):
                    result.append(
                        RawExtractedRow(
                            extractor_name=extractor_name,
                            source_input_type=source_input_type,
                            evidence_type=evidence_type,
                            table_name=table_name,
                            heading_path=heading_path,
                            section_heading=section_heading,
                            row_number=_int_or_none(row.get("_row_number")),
                            column_name=column,
                            raw_key=contract_name or column,
                            raw_value=row.get(column),
                            raw_row=row,
                            extracted_address=address,
                            extracted_network=network,
                            extracted_contract_name=contract_name or _contract_name_from_header(column),
                            extracted_role_hint=role_hint or column,
                            confidence_source="structured_table",
                            confidence_parser=85,
                            **source_fields(document),
                        )
                    )
            continue
        for address in ADDRESS_RE.findall(" ".join(str(value) for value in row.values())):
            result.append(
                RawExtractedRow(
                    extractor_name=extractor_name,
                    source_input_type=source_input_type,
                    evidence_type=evidence_type,
                    table_name=table_name,
                    heading_path=heading_path,
                    section_heading=section_heading,
                    row_number=_int_or_none(row.get("_row_number")),
                    raw_key=contract_name,
                    raw_value=row,
                    raw_row=row,
                    extracted_address=address,
                    extracted_network=network,
                    extracted_contract_name=contract_name,
                    extracted_role_hint=role_hint,
                    confidence_source="structured_table_regex",
                    confidence_parser=75,
                    **source_fields(document),
                )
            )
    return result


def looks_like_html(text: str) -> bool:
    sample = text[:4096].lower()
    return "<table" in sample or "<html" in sample or "<!doctype html" in sample


def looks_like_markdown_table(text: str) -> bool:
    return bool(re.search(r"^\s*\|.+\|\s*$\n^\s*\|[\s|:-]+\|\s*$", text, flags=re.MULTILINE))


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _value(row: dict[str, Any], column: str | None) -> str | None:
    if not column:
        return None
    value = row.get(column)
    if value in {None, ""}:
        return None
    return str(value).strip()


def _contract_name_from_header(header: str) -> str:
    return re.sub(r"\s+", " ", str(header).replace("_", " ")).strip()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None
