from __future__ import annotations

import csv
import hashlib
import json
import re
from collections.abc import Iterable
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pdfminer.high_level import extract_text

from app.core.config import settings
from app.ingestion.column_mapping import ColumnMapping, ColumnMappingService
from app.ingestion.intake_models import CandidatePreview, ParsedSource, SourceArtifact, SourceFingerprint
from app.ingestion.network_normalizer import NetworkNormalizer


ADDRESS_RE = re.compile(
    r"(0x[a-fA-F0-9]{40,128}|bc1[ac-hj-np-z02-9]{11,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|T[1-9A-HJ-NP-Za-km-z]{33}|r[1-9A-HJ-NP-Za-km-z]{24,34}|(?:EQ|UQ)[A-Za-z0-9_-]{46})",
    re.IGNORECASE,
)
URL_RE = re.compile(r"^https?://", re.IGNORECASE)
CONTROL_SHEET_NAMES = {
    "summary",
    "schema",
    "source provenance",
    "por search backlog",
    "indodax cmc capture status",
    "capture status",
    "cmc capture status",
    "search backlog",
    "backlog",
    "provenance",
    "readme",
    "read me",
    "notes",
    "metadata",
    "instructions",
    "config",
    "settings",
    "toc",
    "cover",
}
SHEET_ENTITY_SUFFIX_TOKENS = {"wallets", "wallet", "staking", "validators", "validator", "addresses", "address", "cmc", "eth", "cet"}
SHEET_ENTITY_CASING = {
    "okx": "OKX",
    "mexc": "MEXC",
    "kucoin": "KuCoin",
    "bybit": "Bybit",
    "bitfinex": "Bitfinex",
    "bitmex": "BitMEX",
    "huobi": "Huobi",
    "htx": "HTX",
    "coinex": "CoinEx",
    "indodax": "Indodax",
    "deribit": "Deribit",
    "firi": "Firi",
}


class SourceAdapter:
    adapter_name = "base_adapter"

    def parse(self, artifact: SourceArtifact, fingerprint: SourceFingerprint, raw_content: bytes) -> ParsedSource:
        text = _decode(raw_content, fallback=artifact.pasted_text or "")
        table_preview = _markdown_tables(text)
        candidates = _extract_text_candidates(artifact, fingerprint, text)
        return _parsed(
            artifact,
            fingerprint,
            document_text=text,
            document_title=None,
            metadata={"source_input_type": "plain_text"},
            table_preview=table_preview,
            candidates=candidates,
        )


class PlainTextAdapter(SourceAdapter):
    adapter_name = "plain_text_adapter"


class JsonYamlAdapter(SourceAdapter):
    adapter_name = "json_yaml_adapter"

    def parse(self, artifact: SourceArtifact, fingerprint: SourceFingerprint, raw_content: bytes) -> ParsedSource:
        text = _decode(raw_content, fallback=artifact.pasted_text or "")
        candidates = _extract_text_candidates(artifact, fingerprint, text, source_input_type="structured_text_registry")
        metadata = {"source_input_type": "structured_text_registry"}
        return _parsed(artifact, fingerprint, document_text=text, document_title=None, metadata=metadata, table_preview=[], candidates=candidates)


class GitHubAdapter(SourceAdapter):
    adapter_name = "github_adapter"

    def parse(self, artifact: SourceArtifact, fingerprint: SourceFingerprint, raw_content: bytes) -> ParsedSource:
        text = _decode(raw_content, fallback=artifact.pasted_text or "")
        if not text and artifact.source_url:
            text = f"GitHub source: {artifact.source_url}"
        candidates = _extract_text_candidates(artifact, fingerprint, text, source_input_type="github_source")
        metadata = {"source_input_type": fingerprint.final_source_type or "github_source"}
        return _parsed(artifact, fingerprint, document_text=text, document_title=_title_from_url(artifact.source_url), metadata=metadata, table_preview=[], candidates=candidates)


class PdfAdapter(SourceAdapter):
    adapter_name = "pdf_adapter"

    def parse(self, artifact: SourceArtifact, fingerprint: SourceFingerprint, raw_content: bytes) -> ParsedSource:
        warnings: list[str] = []
        text = ""
        if raw_content:
            try:
                text = extract_text(BytesIO(raw_content), maxpages=10)
            except Exception:
                text = raw_content[:16_000].decode("utf-8", errors="ignore")
                warnings.append("pdf_text_extraction_fallback")
        table_preview = _por_tables_from_text(text)
        candidates = _extract_table_candidates(artifact, fingerprint, table_preview, default_source_input_type="pdf_text_fallback")
        if not candidates:
            candidates = _extract_text_candidates(artifact, fingerprint, text, source_input_type="pdf_text_fallback")
        metadata = {"source_input_type": "pdf_text_fallback", "warnings": warnings}
        return _parsed(artifact, fingerprint, document_text=text, document_title=_first_line(text), metadata=metadata, table_preview=table_preview, candidates=candidates, warnings=warnings)


class ExcelCsvAdapter(SourceAdapter):
    adapter_name = "excel_csv_adapter"

    def parse(self, artifact: SourceArtifact, fingerprint: SourceFingerprint, raw_content: bytes) -> ParsedSource:
        if fingerprint.final_source_type == "csv_upload":
            tables = _csv_tables(raw_content)
            metadata = {
                "source_input_type": "csv_registry",
                "sheet_count": 0,
                "parsed_sheet_names": [],
                "skipped_sheet_names": [],
            }
        else:
            tables, parsed_sheets, skipped_sheets, warnings = _xlsx_tables(raw_content)
            metadata = {
                "source_input_type": "xlsx_multi_sheet_registry" if len(parsed_sheets) > 1 else "xlsx_registry",
                "sheet_count": len(parsed_sheets) + len(skipped_sheets),
                "parsed_sheet_names": parsed_sheets,
                "skipped_sheet_names": skipped_sheets,
                "warnings": warnings,
            }
        candidates = _extract_table_candidates(artifact, fingerprint, tables, default_source_input_type=metadata["source_input_type"])
        table_text = "\n".join(_tables_to_lines(tables))
        return _parsed(
            artifact,
            fingerprint,
            document_text=table_text,
            document_title=_file_name(artifact.local_file_path) or artifact.filename,
            metadata={**metadata, "table_count": len(tables)},
            table_preview=tables,
            candidates=candidates,
            warnings=metadata.get("warnings", []),
        )


ADAPTERS = {
    "plain_text_adapter": PlainTextAdapter,
    "json_yaml_adapter": JsonYamlAdapter,
    "github_adapter": GitHubAdapter,
    "pdf_adapter": PdfAdapter,
    "excel_csv_adapter": ExcelCsvAdapter,
}


def adapter_by_name(adapter_name: str) -> SourceAdapter:
    try:
        return ADAPTERS[adapter_name]()
    except KeyError as exc:
        raise ValueError(f"Unknown adapter: {adapter_name}") from exc


async def fetch_url_bytes(source_url: str) -> tuple[bytes, str | None, str | None]:
    async with httpx.AsyncClient(timeout=settings.source_fetch_timeout_seconds, follow_redirects=True) as client:
        response = await client.get(source_url)
        response.raise_for_status()
        content = response.content[: settings.source_fetch_max_bytes]
        return content, str(response.url), response.headers.get("content-type")


def _parsed(
    artifact: SourceArtifact,
    fingerprint: SourceFingerprint,
    *,
    document_text: str,
    document_title: str | None,
    metadata: dict,
    table_preview: list[dict],
    candidates: list[CandidatePreview],
    warnings: list[str] | None = None,
) -> ParsedSource:
    detected_columns = [ColumnMappingService.detected_columns([str(header) for header in table.get("headers", [])]) for table in table_preview]
    metadata = {
        **metadata,
        "detected_columns": detected_columns,
        "document_sha256": hashlib.sha256(document_text.encode("utf-8")).hexdigest(),
    }
    evidence_preview = [_evidence_preview(candidate, fingerprint) for candidate in candidates[: settings.preview_candidate_limit]]
    return ParsedSource(
        document_text=document_text,
        document_title=document_title,
        content_type=artifact.content_type,
        metadata=metadata,
        table_preview=table_preview[:10],
        candidates=_dedupe_candidates(candidates),
        evidence_preview=evidence_preview,
        warnings=warnings or [],
        fatal_errors=[],
    )


def _csv_tables(raw_content: bytes) -> list[dict]:
    text = _decode(raw_content)
    reader = csv.DictReader(StringIO(text))
    headers = list(reader.fieldnames or [])
    rows = []
    for row_number, row in enumerate(reader, start=2):
        row = {str(key): "" if value is None else str(value) for key, value in row.items()}
        row["_row_number"] = row_number
        rows.append(row)
    return [{"name": "csv_registry", "headers": headers, "rows": rows, "start_line": 2}]


def _xlsx_tables(raw_content: bytes) -> tuple[list[dict], list[str], list[str], list[str]]:
    try:
        from openpyxl import load_workbook
    except Exception:
        return [], [], [], ["openpyxl_unavailable"]
    try:
        workbook = load_workbook(BytesIO(raw_content), read_only=True, data_only=True)
    except Exception:
        return [], [], [], ["xlsx_read_failed"]

    tables: list[dict] = []
    parsed_sheets: list[str] = []
    skipped_sheets: list[str] = []
    for sheet in workbook.worksheets:
        if _skip_xlsx_sheet(sheet.title):
            skipped_sheets.append(sheet.title)
            continue
        rows: list[tuple[int, list[str]]] = []
        for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            values = ["" if value is None else str(value).strip() for value in row]
            if any(values):
                rows.append((row_number, values))
        if not rows:
            skipped_sheets.append(sheet.title)
            continue
        table = _table_from_rows(sheet.title, rows)
        table["sheet_name"] = sheet.title
        parsed_sheets.append(sheet.title)
        tables.append(table)
    return tables, parsed_sheets, skipped_sheets, []


def _table_from_rows(name: str, rows: list[tuple[int, list[str]]]) -> dict:
    first_row_number, header_values = rows[0]
    headers = [str(cell).strip() for cell in header_values]
    if not any(headers):
        headers = [f"column_{index + 1}" for index in range(max(len(values) for _, values in rows))]
    data_rows = rows[1:] if _looks_like_header(headers) else rows
    dict_rows = []
    for row_number, values in data_rows:
        padded = values + [""] * max(0, len(headers) - len(values))
        row_dict = {headers[index]: padded[index] for index in range(len(headers))}
        row_dict["_row_number"] = row_number
        dict_rows.append(row_dict)
    return {"name": name, "headers": headers, "rows": dict_rows, "start_line": first_row_number + 1}


def _markdown_tables(text: str) -> list[dict]:
    lines = text.splitlines()
    tables = []
    idx = 0
    while idx + 1 < len(lines):
        if "|" not in lines[idx] or not re.match(r"^\s*\|?[\s|:-]+\|?\s*$", lines[idx + 1]):
            idx += 1
            continue
        headers = [cell.strip() for cell in lines[idx].strip().strip("|").split("|")]
        rows = []
        idx += 2
        row_number = idx + 1
        while idx < len(lines) and "|" in lines[idx]:
            values = [cell.strip() for cell in lines[idx].strip().strip("|").split("|")]
            row = {headers[index]: values[index] if index < len(values) else "" for index in range(len(headers))}
            row["_row_number"] = row_number
            rows.append(row)
            idx += 1
            row_number += 1
        tables.append({"name": f"markdown_table_{len(tables) + 1}", "headers": headers, "rows": rows, "start_line": row_number})
    return tables


def _por_tables_from_text(text: str) -> list[dict]:
    rows = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = ADDRESS_RE.search(line)
        if not match:
            continue
        network = _network_from_line(line)
        rows.append({"Network": network or "", "Address": match.group(0), "Role": "audited wallet", "_row_number": line_number})
    return [{"name": "audited_wallets", "headers": ["Network", "Address", "Role"], "rows": rows, "start_line": 1}] if rows else []


def _extract_table_candidates(
    artifact: SourceArtifact,
    fingerprint: SourceFingerprint,
    tables: list[dict],
    *,
    default_source_input_type: str,
) -> list[CandidatePreview]:
    candidates: list[CandidatePreview] = []
    for table in tables:
        mapping = ColumnMappingService.map_headers([str(header) for header in table.get("headers", [])])
        for row in table.get("rows", []):
            if not isinstance(row, dict) or not any(str(value).strip() for value in row.values()):
                continue
            raw_network = mapping.get(row, "chain")
            role_hint = mapping.get(row, "role")
            source_url, source_file_name = _source_reference_from_row(mapping.get(row, "source_url"), artifact.source_url)
            row_number = _int_or_none(mapping.get(row, "source_row")) or _int_or_none(str(row.get("_row_number")))
            base_reference = _row_reference(
                row=row,
                table=table,
                mapping=mapping,
                source_file_name=source_file_name,
                default_source_input_type=default_source_input_type,
            )
            for address_kind, address, role in _structured_address_fields(mapping, row, role_hint, table):
                candidate = _candidate_from_address(
                    artifact,
                    fingerprint,
                    address=address,
                    raw_network=raw_network,
                    role_hint=role_hint,
                    suggested_role=role,
                    source_url=source_url,
                    source_sheet=table.get("sheet_name"),
                    source_row=row_number,
                    source_page=_int_or_none(mapping.get(row, "source_row")) if "page" in (mapping.columns.get("source_row") or "").lower() else None,
                    table_name=table.get("name"),
                    evidence_type=mapping.get(row, "evidence_type") or "source_extraction_context",
                    source_input_type=default_source_input_type,
                    raw_reference={**base_reference, "address_column_kind": address_kind},
                )
                if candidate:
                    candidates.append(candidate)
            if any(mapping.get(row, field) for field in ("address", "deposit_address", "withdrawal_address")):
                continue
            for match in ADDRESS_RE.finditer(" ".join(_visible_row_values(row))):
                candidate = _candidate_from_address(
                    artifact,
                    fingerprint,
                    address=match.group(0),
                    raw_network=raw_network,
                    role_hint=role_hint,
                    suggested_role=_suggest_role(role_hint),
                    source_url=source_url,
                    source_sheet=table.get("sheet_name"),
                    source_row=row_number,
                    source_page=None,
                    table_name=table.get("name"),
                    evidence_type=mapping.get(row, "evidence_type") or "source_extraction_context",
                    source_input_type=default_source_input_type,
                    raw_reference={**base_reference, "address_column_kind": "row_regex"},
                )
                if candidate:
                    candidates.append(candidate)
    return candidates


def _extract_text_candidates(
    artifact: SourceArtifact,
    fingerprint: SourceFingerprint,
    text: str,
    *,
    source_input_type: str = "plain_text",
) -> list[CandidatePreview]:
    candidates: list[CandidatePreview] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in ADDRESS_RE.finditer(line):
            candidate = _candidate_from_address(
                artifact,
                fingerprint,
                address=match.group(0),
                raw_network=_network_from_line(line),
                role_hint=None,
                suggested_role=None,
                source_url=artifact.source_url,
                source_sheet=None,
                source_row=line_number,
                source_page=None,
                table_name=None,
                evidence_type="source_extraction_context",
                source_input_type=source_input_type,
                raw_reference={"raw_line": line, "line_number": line_number},
            )
            if candidate:
                candidates.append(candidate)
    return candidates


def _candidate_from_address(
    artifact: SourceArtifact,
    fingerprint: SourceFingerprint,
    *,
    address: str,
    raw_network: str | None,
    role_hint: str | None,
    suggested_role: str | None,
    source_url: str | None,
    source_sheet: str | None,
    source_row: int | None,
    source_page: int | None,
    table_name: str | None,
    evidence_type: str,
    source_input_type: str,
    raw_reference: dict,
) -> CandidatePreview | None:
    clean = clean_wallet_address(address)
    if not clean:
        return None
    network = NetworkNormalizer.normalize(raw_network)
    inferred_family = _infer_address_family(clean)
    chain_guess = network.chain_guess or inferred_family
    chain_slug = network.canonical_chain
    chain_id = network.chain_id
    warnings: list[str] = list(raw_reference.get("warnings") or [])
    if raw_network and not network.canonical_chain:
        warnings.append("unrecognized_source_network")
    if raw_reference.get("validator_public_key"):
        warnings.append("validator_public_key_metadata_only")
    normalized = clean.lower() if clean.startswith("0x") else clean
    confidence = _confidence_from_value(raw_reference.get("confidence")) or (70 if raw_network else 45)
    entity = raw_reference.get("row_entity") or _entity_from_sheet_name(source_sheet)
    return CandidatePreview(
        address=clean,
        normalized_address=normalized,
        entity_name=entity,
        source_network=raw_network,
        chain_guess=chain_guess,
        chain_slug=chain_slug,
        chain_id=chain_id,
        address_family=_address_family(chain_guess, inferred_family),
        suggested_role=suggested_role or _suggest_role(role_hint),
        confidence_initial=confidence,
        status="needs_review",
        source_type=fingerprint.final_source_type or "unknown",
        source_input_type=source_input_type,
        source_sheet=source_sheet,
        source_row=source_row,
        source_page=source_page,
        source_url=source_url,
        file_path=artifact.local_file_path,
        evidence_type=evidence_type,
        warnings=warnings,
        raw_reference={
            **raw_reference,
            "table_name": table_name,
            "original_value": address,
            "normalized_value": normalized,
            "final_source_type": fingerprint.final_source_type,
            "adapter_name": fingerprint.parser_adapter,
            "source_url": source_url,
            "file_path": artifact.local_file_path,
        },
    )


def _structured_address_fields(mapping: ColumnMapping, row: dict, role_hint: str | None, table: dict) -> list[tuple[str, str, str | None]]:
    fields: list[tuple[str, str, str | None]] = []
    address = mapping.get(row, "address")
    if address:
        fields.append(("address", address, _suggest_role(role_hint) or "cex_reserve_wallet_candidate"))
    deposit_address = mapping.get(row, "deposit_address")
    if deposit_address:
        fields.append(("deposit_address", deposit_address, "staking_deposit_wallet"))
    withdrawal_address = mapping.get(row, "withdrawal_address")
    if withdrawal_address:
        role = "staking_withdrawal_wallet" if "staking" in _normalize_sheet_name(str(table.get("sheet_name") or table.get("name") or "")) else "cex_cold_wallet"
        fields.append(("withdrawal_address", withdrawal_address, role))
    return fields


def _row_reference(*, row: dict, table: dict, mapping: ColumnMapping, source_file_name: str | None, default_source_input_type: str) -> dict:
    validator_public_key = mapping.get(row, "validator_public_key")
    return {
        "raw_row_json": {key: value for key, value in row.items() if not str(key).startswith("_")},
        "source_input_type": default_source_input_type,
        "source_sheet": table.get("sheet_name"),
        "source_row": row.get("_row_number"),
        "source_file_name": source_file_name,
        "row_entity": mapping.get(row, "entity"),
        "row_protocol": mapping.get(row, "protocol"),
        "row_category": mapping.get(row, "category"),
        "evidence_type": mapping.get(row, "evidence_type"),
        "report_date": mapping.get(row, "report_date"),
        "audit_date": mapping.get(row, "audit_date"),
        "confidence": mapping.get(row, "confidence"),
        "notes": mapping.get(row, "notes"),
        "validator_public_key": validator_public_key,
        "warnings": ["validator_public_key_metadata_only"] if validator_public_key else [],
    }


def clean_wallet_address(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = str(value).strip().strip("`'\";,()[]{}")
    cleaned = re.sub(r"\s+", "", cleaned)
    if len(cleaned) < 8:
        return None
    return cleaned


def _suggest_role(role_hint: str | None) -> str | None:
    if not role_hint:
        return None
    text = role_hint.lower()
    if "deposit" in text and "staking" in text:
        return "staking_deposit_wallet"
    if "withdrawal" in text:
        return "staking_withdrawal_wallet" if "staking" in text else "cex_cold_wallet"
    if "cold" in text:
        return "cex_cold_wallet"
    if "hot" in text:
        return "cex_hot_wallet"
    if "reserve" in text or "por" in text or "audited" in text:
        return "cex_por_wallet"
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return normalized or None


def _source_reference_from_row(value: str | None, fallback_url: str | None) -> tuple[str | None, str | None]:
    if not value:
        return fallback_url, None
    text = value.strip()
    if URL_RE.match(text):
        return text, None
    return fallback_url, text


def _evidence_preview(candidate: CandidatePreview, fingerprint: SourceFingerprint) -> dict:
    return {
        "address": candidate.address,
        "source_type": candidate.source_type,
        "final_source_type": fingerprint.final_source_type,
        "adapter_name": fingerprint.parser_adapter,
        "source_url": candidate.source_url,
        "file_path": candidate.file_path,
        "sheet_name": candidate.source_sheet,
        "row_number": candidate.source_row,
        "page_number": candidate.source_page,
        "raw_reference": candidate.raw_reference,
        "confidence_reason": "structured_network_column" if candidate.source_network else "address_pattern_fallback",
    }


def _dedupe_candidates(candidates: list[CandidatePreview]) -> list[CandidatePreview]:
    seen: set[tuple[str, str | None, int | None, str | None, int | None, str | None]] = set()
    deduped: list[CandidatePreview] = []
    for candidate in candidates:
        key = (
            candidate.normalized_address,
            candidate.chain_slug,
            candidate.chain_id,
            candidate.source_sheet,
            candidate.source_row,
            candidate.suggested_role,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _decode(content: bytes, fallback: str = "") -> str:
    if not content:
        return fallback
    return content.decode("utf-8-sig", errors="replace")


def _skip_xlsx_sheet(name: str | None) -> bool:
    if not name:
        return True
    normalized = _normalize_sheet_name(name)
    if normalized in CONTROL_SHEET_NAMES:
        return True
    if any(token in normalized for token in {"provenance", "backlog", "capture status", "schema"}):
        return True
    return normalized.startswith(("summary", "readme", "instruction"))


def _normalize_sheet_name(name: str) -> str:
    normalized = re.sub(r"[_\-/]+", " ", name.strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _entity_from_sheet_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = _normalize_sheet_name(value)
    if not normalized or _skip_xlsx_sheet(normalized):
        return None
    tokens = [token for token in normalized.split() if token not in SHEET_ENTITY_SUFFIX_TOKENS]
    if not tokens:
        return None
    if tokens[:2] == ["huobi", "htx"]:
        return "Huobi_HTX"
    primary = tokens[0]
    return SHEET_ENTITY_CASING.get(primary, primary.upper() if len(primary) <= 4 else primary.title())


def _network_from_line(line: str) -> str | None:
    before = ADDRESS_RE.split(line, maxsplit=1)[0]
    tokens = before.strip(" :-\t|")
    if not tokens:
        return None
    parts = re.split(r"\s{2,}|\t|\|", tokens)
    for part in reversed(parts):
        normalized = NetworkNormalizer.normalize(part)
        if normalized.canonical_chain:
            return part
    return tokens if NetworkNormalizer.normalize(tokens).canonical_chain else None


def _infer_address_family(address: str) -> str | None:
    lower = address.lower()
    if lower.startswith("0x"):
        return "evm"
    if lower.startswith("bc1") or re.match(r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$", address):
        return "btc"
    if address.startswith("T") and len(address) == 34:
        return "tron"
    if address.startswith("r") and 25 <= len(address) <= 35:
        return "xrp"
    if address.startswith(("EQ", "UQ")):
        return "ton"
    return None


def _address_family(*values: str | None) -> str | None:
    for value in values:
        if value:
            return "bitcoin" if value == "btc" else value
    return None


def _confidence_from_value(value: object) -> int | None:
    if value in {None, ""}:
        return None
    text = str(value).strip().lower()
    if text in {"high", "confirmed", "strong"}:
        return 95
    if text in {"medium", "med", "review", "needs review"}:
        return 75
    if text in {"low", "weak", "candidate"}:
        return 55
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group(0))
    if number <= 1:
        number *= 100
    return max(0, min(100, int(round(number))))


def _visible_row_values(row: dict) -> list[str]:
    return [str(value) for key, value in row.items() if not str(key).startswith("_") and value not in {None, ""}]


def _tables_to_lines(tables: list[dict]) -> Iterable[str]:
    for table in tables:
        yield ",".join(str(header) for header in table.get("headers", []))
        for row in table.get("rows", []):
            if isinstance(row, dict):
                yield ",".join(_visible_row_values(row))


def _file_name(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).name


def _title_from_url(source_url: str | None) -> str | None:
    if not source_url:
        return None
    return Path(urlparse(source_url).path).name or source_url


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:255]
    return None


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _looks_like_header(headers: list[str]) -> bool:
    mapped = ColumnMappingService.map_headers(headers)
    return bool(mapped.columns) or any(re.search(r"[A-Za-z]", header) for header in headers)
