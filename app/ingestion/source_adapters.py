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
    r"(?<![A-Za-z0-9])(?:0x[a-fA-F0-9]{40,64}|bc1[ac-hj-np-z02-9]{11,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|T[1-9A-HJ-NP-Za-km-z]{33}|r[1-9A-HJ-NP-Za-km-z]{24,34}|(?:EQ|UQ)[A-Za-z0-9_-]{46})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
URL_RE = re.compile(r"^https?://", re.IGNORECASE)
XRP_RE = re.compile(r"^r[1-9A-HJ-NP-Za-km-z]{24,34}$")
EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
LONG_0X_RE = re.compile(r"^0x[a-fA-F0-9]{40,64}$")
BTC_RE = re.compile(r"^(?:bc1[ac-hj-np-z02-9]{11,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$", re.IGNORECASE)
TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
TON_RE = re.compile(r"^(?:EQ|UQ)[A-Za-z0-9_-]{46}$")
SUBSTRATE_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,64}$")
COSMOS_RE = re.compile(r"^cosmos1[0-9a-z]{20,80}$", re.IGNORECASE)
DYDX_RE = re.compile(r"^dydx1[0-9a-z]{20,80}$", re.IGNORECASE)
DOGE_RE = re.compile(r"^D[1-9A-HJ-NP-Za-km-z]{25,34}$")
AVALANCHE_X_RE = re.compile(r"^X-avax1[0-9a-z]{20,80}$", re.IGNORECASE)
HACKEN_STOP_MARKERS = {"collateral ratios", "team composition", "conclusion", "disclaimers"}
PDF_LIGATURE_TRANSLATION = str.maketrans(
    {
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
    }
)
HACKEN_NETWORKS = sorted(
    {
        "Aptos",
        "Arbitrum",
        "Arbitrum Nova",
        "Arbitrum One",
        "Avalanche-C",
        "Avalanche-X",
        "Base",
        "Bera",
        "Bitcoin",
        "BSC",
        "Celo",
        "Codex",
        "Corn",
        "Cosmos",
        "Dogecoin",
        "DYDX",
        "Ethereum",
        "Fantom",
        "Hedera",
        "HyperEVM",
        "Kaia",
        "Kava EVM",
        "Linea",
        "Litecoin",
        "Manta",
        "Mantle",
        "Monad",
        "Optimism",
        "Plasma",
        "Polkadot AH",
        "Polygon",
        "Ripple",
        "XRP Ledger",
        "Scroll",
        "Sei EVM",
        "Solana",
        "Sonic",
        "Sui",
        "Ton",
        "Tron",
        "Vaulta",
        "XDC",
        "ZKSync Era",
        "ZKSync Lite",
    },
    key=lambda value: len(value.split()),
    reverse=True,
)
COMPACT_HACKEN_NETWORK_ALIASES = {
    "Aptos": "Aptos",
    "Arbitrum": "Arbitrum",
    "ArbitrumNova": "Arbitrum Nova",
    "ArbitrumOne": "Arbitrum One",
    "Avalanche-C": "Avalanche-C",
    "AvalancheC": "Avalanche-C",
    "Avalanche-X": "Avalanche-X",
    "AvalancheX": "Avalanche-X",
    "Base": "Base",
    "Bera": "Bera",
    "Bitcoin": "Bitcoin",
    "BSC": "BSC",
    "Celo": "Celo",
    "Codex": "Codex",
    "Corn": "Corn",
    "Cosmos": "Cosmos",
    "Dogecoin": "Dogecoin",
    "DYDX": "DYDX",
    "Ethereum": "Ethereum",
    "Hedera": "Hedera",
    "HyperEVM": "HyperEVM",
    "Kaia": "Kaia",
    "KavaEVM": "Kava EVM",
    "Linea": "Linea",
    "Litecoin": "Litecoin",
    "Manta": "Manta",
    "Mantle": "Mantle",
    "Monad": "Monad",
    "Optimism": "Optimism",
    "Plasma": "Plasma",
    "PolkadotAH": "Polkadot AH",
    "Polygon": "Polygon",
    "Ripple": "Ripple",
    "XRPLedger": "XRP Ledger",
    "Scroll": "Scroll",
    "SeiEVM": "Sei EVM",
    "Solana": "Solana",
    "Sonic": "Sonic",
    "Sui": "Sui",
    "Ton": "Ton",
    "Tron": "Tron",
    "Vaulta": "Vaulta",
    "XDC": "XDC",
    "ZKSyncEra": "ZKSync Era",
    "ZKSyncLite": "ZKSync Lite",
}
COMPACT_HACKEN_NETWORKS = sorted(
    {re.sub(r"\s+", "", alias): canonical for alias, canonical in COMPACT_HACKEN_NETWORK_ALIASES.items()}.items(),
    key=lambda item: len(item[0]),
    reverse=True,
)
COMPACT_HACKEN_STOP_MARKERS = [
    "collateral ratios",
    "team composition",
    "conclusion",
    "disclaimers",
    "Hacken's BYBIT Proof of Reserve",
]
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
                if settings.pdf_max_pages and settings.pdf_max_pages > 0:
                    text = extract_text(BytesIO(raw_content), maxpages=settings.pdf_max_pages)
                else:
                    text = extract_text(BytesIO(raw_content))
            except Exception:
                text = raw_content[:16_000].decode("utf-8", errors="ignore")
                warnings.append("pdf_text_extraction_fallback")
        normalized_text = _normalize_pdf_text(text)
        text_normalized = normalized_text != text
        text = normalized_text
        entity_name = _detect_pdf_entity(text)
        table_preview = _parse_hacken_audited_wallet_rows(text)
        diagnostics = _pdf_audited_wallet_diagnostics(text, table_preview)
        if table_preview:
            candidates = _extract_table_candidates(artifact, fingerprint, table_preview, default_source_input_type="pdf_audited_wallet_table")
            diagnostics["pdf_parser_mode"] = _hacken_pdf_parser_mode(table_preview)
            metadata = {
                "source_input_type": "pdf_audited_wallet_table",
                "entity_name": entity_name,
                "category": "cex",
                "sub_category": "reserve_boundary",
                "expected_roles": ["cex_por_wallet"],
                "table_count": 1,
                "pdf_text_normalized": text_normalized,
                "warnings": warnings,
                **diagnostics,
            }
        else:
            if diagnostics["audited_wallet_heading_found"]:
                warnings.append("pdf_audited_wallet_section_found_but_no_rows")
            warnings.append("pdf_loose_text_fallback_used")
            candidates = _extract_text_candidates(artifact, fingerprint, text, source_input_type="pdf_text_fallback")
            diagnostics["pdf_parser_mode"] = "pdf_text_fallback"
            metadata = {
                "source_input_type": "pdf_text_fallback",
                "entity_name": entity_name,
                "pdf_text_normalized": text_normalized,
                "warnings": warnings,
                **diagnostics,
            }
            if entity_name == "Bybit" and _is_proof_of_reserves_text(text):
                metadata.update(
                    {
                        "category": "cex",
                        "sub_category": "reserve_boundary",
                        "expected_roles": ["cex_por_wallet"],
                    }
                )
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


def _parse_hacken_audited_wallet_rows(text: str) -> list[dict]:
    lines = [(line_number, line.strip()) for line_number, line in enumerate(text.splitlines(), start=1)]
    section = _audited_wallet_section_state(lines)
    start_index = section["start_index"]
    if start_index is None:
        return []

    entity_name = _detect_pdf_entity(text)
    rows: list[dict] = []
    index = start_index
    while index < len(lines):
        line_number, line = lines[index]
        if _is_hacken_stop_line(line):
            break
        if not line or _is_footer_only_line(line):
            index += 1
            continue
        network_match = _match_known_network_at(lines, index)
        if network_match is None:
            index += 1
            continue

        network, remainder, next_index = network_match
        pieces: list[str] = [remainder] if remainder else []
        index = next_index
        while index < len(lines):
            next_line = lines[index][1] if index < len(lines) else None
            if _address_from_network_pieces(network, pieces) and not _should_continue_wrapped_address(network, pieces, next_line):
                break
            _next_number, next_line = lines[index]
            if _is_hacken_stop_line(next_line) or _is_footer_only_line(next_line) or _match_known_network_at(lines, index) is not None:
                break
            if not next_line:
                index += 1
                continue
            if _looks_like_address_continuation(next_line):
                pieces.append(next_line)
                index += 1
                continue
            break

        address = _address_from_network_pieces(network, pieces)
        if not address:
            continue
        rows.append(
            {
                "Entity": entity_name,
                "Network": network,
                "Address": address,
                "Role": "audited wallet",
                "Evidence Type": "audited_wallet",
                "Confidence": "85",
                "_row_number": line_number,
            }
        )

    if rows:
        return [_hacken_wallet_table(rows, rows[0]["_row_number"], "hacken_audited_wallets")]

    if section["heading_found"] and section["header_found"]:
        compact_rows = _parse_compact_hacken_wallet_rows(text)
        if compact_rows:
            return [_hacken_wallet_table(compact_rows, start_index + 1, "hacken_audited_wallets_compact")]
    return []


def _hacken_wallet_table(rows: list[dict], start_line: int, parser: str) -> dict:
    return {
        "name": "audited_wallets",
        "headers": ["Entity", "Network", "Address", "Role", "Evidence Type", "Confidence"],
        "rows": rows,
        "start_line": start_line,
        "metadata": {"parser": parser},
    }


def _parse_compact_hacken_wallet_rows(text: str) -> list[dict]:
    normalized = _normalize_pdf_text(text)
    heading_match = re.search(r"audited\s*wallets", normalized, flags=re.IGNORECASE)
    if not heading_match:
        return []
    header_match = re.search(r"network\s*address", normalized[heading_match.end() :], flags=re.IGNORECASE)
    if not header_match:
        return []

    content_start = heading_match.end() + header_match.end()
    content = normalized[content_start:]
    stop_index = _compact_hacken_stop_index(content)
    if stop_index is not None:
        content = content[:stop_index]
    compact = re.sub(r"\s+", "", content)
    if not compact:
        return []

    entity_name = _detect_pdf_entity(text)
    rows: list[dict] = []
    index = 0
    while index < len(compact):
        network_match = _compact_network_at(compact, index)
        if network_match is None:
            index += 1
            continue
        network, network_end = network_match
        address = _compact_address_for_network(compact, network_end, network)
        if not address:
            index = max(network_end, index + 1)
            continue
        rows.append(
            {
                "Entity": entity_name,
                "Network": network,
                "Address": address,
                "Role": "audited wallet",
                "Evidence Type": "audited_wallet",
                "Confidence": "85",
                "_row_number": 1,
            }
        )
        index = network_end + len(address)
    return rows


def _compact_hacken_stop_index(content: str) -> int | None:
    patterns = [
        r"collateral\s*ratios",
        r"team\s*composition",
        r"conclusion",
        r"disclaimers",
        r"hacken'?s\s*bybit\s*proof\s*of\s*reserve",
        r"page\s*\d+",
    ]
    matches = [match.start() for pattern in patterns if (match := re.search(pattern, content, flags=re.IGNORECASE))]
    return min(matches) if matches else None


def _compact_network_at(compact: str, index: int) -> tuple[str, int] | None:
    for token, canonical in COMPACT_HACKEN_NETWORKS:
        if compact[index : index + len(token)].lower() == token.lower():
            return canonical, index + len(token)
    return None


def _compact_address_for_network(compact: str, index: int, network: str) -> str | None:
    normalized_network = NetworkNormalizer.normalize(network)
    if normalized_network.chain_guess in {"aptos", "sui"}:
        return _compact_0x_address(compact, index, normalized_network, min_hex=40, max_hex=64)
    if normalized_network.chain_guess == "evm":
        return _compact_0x_address(compact, index, normalized_network, min_hex=40, max_hex=40)

    regexes = [BTC_RE, TRON_RE, TON_RE, XRP_RE, COSMOS_RE, DYDX_RE, DOGE_RE, AVALANCHE_X_RE]
    for regex in regexes:
        for end in range(min(len(compact), index + 96), index + 7, -1):
            candidate = compact[index:end]
            if not regex.fullmatch(candidate):
                continue
            if not _compact_boundary_after_address(compact, end):
                continue
            if _valid_address_for_network(candidate, normalized_network):
                return candidate
    return None


def _compact_0x_address(compact: str, index: int, network, *, min_hex: int, max_hex: int) -> str | None:
    if compact[index : index + 2].lower() != "0x":
        return None
    for hex_length in range(max_hex, min_hex - 1, -1):
        end = index + 2 + hex_length
        candidate = compact[index:end]
        if not re.fullmatch(r"0x[a-fA-F0-9]+", candidate):
            continue
        if not _compact_boundary_after_address(compact, end):
            continue
        if _valid_address_for_network(candidate, network):
            return candidate
    return None


def _compact_boundary_after_address(compact: str, index: int) -> bool:
    if index >= len(compact):
        return True
    if _compact_network_at(compact, index) is not None:
        return True
    return _compact_hacken_stop_index(compact[index : index + 80]) == 0


def _pdf_audited_wallet_diagnostics(text: str, tables: list[dict]) -> dict:
    lines = [(line_number, line.strip()) for line_number, line in enumerate(text.splitlines(), start=1)]
    section = _audited_wallet_section_state(lines)
    heading_index = section["heading_index"]
    excerpt: list[str] = []
    if heading_index is not None:
        start = max(0, heading_index - 5)
        end = min(len(lines), heading_index + 15)
        excerpt = [line for _line_number, line in lines[start:end]]
    rows_detected = sum(len(table.get("rows", [])) for table in tables)
    start_index = section["start_index"]
    return {
        "pdf_text_line_count": len(lines),
        "audited_wallet_heading_found": bool(section["heading_found"]),
        "network_address_header_found": bool(section["header_found"]),
        "audited_wallet_start_line": lines[start_index][0] if isinstance(start_index, int) and start_index < len(lines) else None,
        "audited_wallet_rows_detected": rows_detected,
        "pdf_parser_mode": "hacken_audited_wallet_table" if rows_detected else "pdf_text_fallback",
        "pdf_text_excerpt_around_audited_wallet": excerpt[:20],
    }


def _hacken_pdf_parser_mode(tables: list[dict]) -> str:
    parser = (tables[0].get("metadata") or {}).get("parser") if tables else None
    if parser == "hacken_audited_wallets_compact":
        return "hacken_audited_wallet_compact_table"
    return "hacken_audited_wallet_table"


def _audited_wallet_section_start(lines: list[tuple[int, str]]) -> int | None:
    return _audited_wallet_section_state(lines)["start_index"]


def _audited_wallet_section_state(lines: list[tuple[int, str]]) -> dict:
    for index, (_line_number, line) in enumerate(lines):
        if not _is_audited_wallet_heading(line):
            continue
        if _line_has_network_address_header(line):
            return {
                "heading_found": True,
                "header_found": True,
                "heading_index": index,
                "start_index": index,
            }
        network_line: int | None = None
        address_line: int | None = None
        for header_index in range(index + 1, min(index + 10, len(lines))):
            header_line = lines[header_index][1]
            header = re.sub(r"\s+", " ", header_line.lower()).strip()
            if "network" in header and network_line is None:
                network_line = header_index
            if "address" in header and address_line is None:
                address_line = header_index
            if (network_line is not None and address_line is not None) or _line_has_network_address_header(header_line):
                return {
                    "heading_found": True,
                    "header_found": True,
                    "heading_index": index,
                    "start_index": max(value for value in [network_line, address_line, header_index] if value is not None) + 1,
                }
        return {"heading_found": True, "header_found": False, "heading_index": index, "start_index": index + 1}
    return {"heading_found": False, "header_found": False, "heading_index": None, "start_index": None}


def _is_audited_wallet_heading(line: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", line.lower()).strip()
    compact = re.sub(r"[^a-z0-9]+", "", line.lower())
    return "audited wallets" in normalized or "auditedwallets" in compact


def _line_has_network_address_header(line: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", line.lower())
    return "networkaddress" in compact


def _is_hacken_stop_line(line: str) -> bool:
    normalized = re.sub(r"\s+", " ", line.strip().lower())
    return any(marker in normalized for marker in HACKEN_STOP_MARKERS)


def _is_footer_only_line(line: str) -> bool:
    normalized = line.strip()
    if not normalized:
        return True
    if re.fullmatch(r"\d{1,3}", normalized):
        return True
    if re.fullmatch(r"page\s+\d+(?:\s+of\s+\d+)?", normalized, flags=re.IGNORECASE):
        return True
    lower = normalized.lower()
    if "bybit proof of reserve" in lower or "bybit proof of reserves" in lower:
        return True
    if "hacken" in lower and "proof of reserve" in lower:
        return True
    return normalized.lower() in {"hacken", "proof of reserves audit report", "bybit proof of reserves audit report"}


def _match_known_network_at(lines: list[tuple[int, str]], index: int) -> tuple[str, str, int] | None:
    line = lines[index][1]
    direct = _match_known_network_prefix(line)
    if direct is not None:
        network, remainder = direct
        if not remainder and index + 1 < len(lines):
            combined_line = f"{line} {lines[index + 1][1]}".strip()
            combined = _match_known_network_prefix(combined_line)
            if combined is not None and len(combined[0].split()) > len(network.split()):
                return combined[0], combined[1], index + 2
        return network, remainder, index + 1

    if index + 1 < len(lines):
        combined_line = f"{line} {lines[index + 1][1]}".strip()
        combined = _match_known_network_prefix(combined_line)
        if combined is not None and len(combined[0].split()) > 1:
            return combined[0], combined[1], index + 2
    return None


def _match_known_network_prefix(line: str) -> tuple[str, str] | None:
    normalized = re.sub(r"\s+", " ", line.strip())
    for network in HACKEN_NETWORKS:
        pattern = re.compile(rf"^{re.escape(network)}(?=$|\s|:|-)", flags=re.IGNORECASE)
        match = pattern.match(normalized)
        if match:
            return network, normalized[match.end() :].strip(" :-\t")
    return None


def _looks_like_address_continuation(line: str) -> bool:
    stripped = re.sub(r"\s+", "", line.strip())
    if not stripped:
        return False
    if _match_known_network_prefix(line) is not None:
        return False
    return bool(re.fullmatch(r"[A-Fa-f0-9]{4,64}", stripped) or re.fullmatch(r"[A-Za-z0-9_-]{8,96}", stripped))


def _address_from_network_pieces(network: str, pieces: list[str]) -> str | None:
    compact = "".join(re.sub(r"\s+", "", piece.strip()) for piece in pieces if piece and piece.strip())
    if not compact:
        return None
    normalized_network = NetworkNormalizer.normalize(network)
    if normalized_network.chain_guess == "substrate":
        match = SUBSTRATE_RE.search(compact)
        if match:
            address = match.group(0)
            return address if _valid_address_for_network(address, normalized_network) else None
    if normalized_network.chain_guess in {"aptos", "sui"}:
        match = re.search(r"0x[a-fA-F0-9]{40,64}", compact)
        if match:
            address = match.group(0)
            return address if _valid_address_for_network(address, normalized_network) else None
    if normalized_network.chain_guess == "evm":
        match = re.search(r"0x[a-fA-F0-9]{40}(?![a-fA-F0-9])", compact)
        if match:
            address = match.group(0)
            return address if _valid_address_for_network(address, normalized_network) else None
    for regex in (BTC_RE, TRON_RE, TON_RE, XRP_RE):
        for match in regex.finditer(compact):
            address = match.group(0)
            if _valid_address_for_network(address, normalized_network):
                return address
    match = ADDRESS_RE.search(compact)
    if match and _valid_address_for_network(match.group(0), normalized_network):
        return match.group(0)
    return None


def _should_continue_wrapped_address(network: str, pieces: list[str], next_line: str | None) -> bool:
    if not next_line:
        return False
    if _is_hacken_stop_line(next_line) or _is_footer_only_line(next_line) or _match_known_network_prefix(next_line):
        return False
    normalized_network = NetworkNormalizer.normalize(network)
    if normalized_network.chain_guess not in {"aptos", "sui"}:
        return False
    compact = "".join(re.sub(r"\s+", "", piece.strip()) for piece in pieces if piece and piece.strip())
    match = re.search(r"0x[a-fA-F0-9]{40,63}$", compact)
    if not match:
        return False
    continuation = re.sub(r"\s+", "", next_line.strip())
    if not re.fullmatch(r"[a-fA-F0-9]{1,24}", continuation):
        return False
    return len(match.group(0)) - 2 + len(continuation) <= 64


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
            raw_network = _network_from_line(line)
            warnings = ["no_source_network_for_text_candidate"] if source_input_type == "pdf_text_fallback" and not raw_network else []
            candidate = _candidate_from_address(
                artifact,
                fingerprint,
                address=match.group(0),
                raw_network=raw_network,
                role_hint=None,
                suggested_role=None,
                source_url=artifact.source_url,
                source_sheet=None,
                source_row=line_number,
                source_page=None,
                table_name=None,
                evidence_type="source_extraction_context",
                source_input_type=source_input_type,
                raw_reference={"raw_line": line, "line_number": line_number, "warnings": warnings},
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
    if not _valid_address_for_network(clean, network):
        return None
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


def _valid_address_for_network(address: str, network) -> bool:
    chain_guess = network.chain_guess
    if EVM_RE.fullmatch(address):
        return chain_guess not in {"aptos", "sui", "xrp", "ton", "tron", "btc", "bitcoin"}
    if LONG_0X_RE.fullmatch(address):
        return chain_guess in {"aptos", "sui"}
    if XRP_RE.fullmatch(address):
        return chain_guess == "xrp"
    if BTC_RE.fullmatch(address):
        return chain_guess in {"btc", "bitcoin", "litecoin", "dogecoin"}
    if TRON_RE.fullmatch(address):
        return chain_guess == "tron"
    if TON_RE.fullmatch(address):
        return chain_guess == "ton"
    if SUBSTRATE_RE.fullmatch(address):
        return chain_guess == "substrate"
    if COSMOS_RE.fullmatch(address):
        return network.canonical_chain == "cosmos"
    if DYDX_RE.fullmatch(address):
        return network.canonical_chain == "dydx"
    if DOGE_RE.fullmatch(address):
        return chain_guess == "dogecoin"
    if AVALANCHE_X_RE.fullmatch(address):
        return network.canonical_chain == "avalanche-x"
    return False


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


def _normalize_pdf_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.translate(PDF_LIGATURE_TRANSLATION)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[\t\f\v\u00a0\u1680\u180e\u2000-\u200b\u2028\u2029\u202f\u205f\u3000]", " ", normalized)
    return normalized


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
    if lower.startswith("cosmos1") or lower.startswith("dydx1"):
        return "cosmos"
    if address.startswith("D"):
        return "dogecoin"
    if lower.startswith("x-avax1"):
        return "avalanche"
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


def _detect_pdf_entity(text: str) -> str | None:
    if re.search(r"\bAuditee\s+Bybit\b", text, flags=re.IGNORECASE):
        return "Bybit"
    if re.search(r"\bBYBIT\b", text):
        return "Bybit"
    return None


def _is_proof_of_reserves_text(text: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return "proof of reserves" in normalized or "proof of reserve" in normalized


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
