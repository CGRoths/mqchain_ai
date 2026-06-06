from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse


EVM_ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
DEPLOYMENT_HEADERS = [
    "Entity",
    "Protocol",
    "Category",
    "Network",
    "Chain",
    "Address",
    "Contract Name",
    "Role",
    "Evidence Type",
    "Confidence",
    "Source URL",
    "Source Row / Line",
    "Raw Row JSON",
]


def infer_profile(source_url: str | None, text: str = "") -> dict:
    haystack = f"{source_url or ''} {text[:2048]}".lower()
    if "aave-dao/aave-address-book" in haystack or "aave" in haystack:
        return {"entity_name": "Aave", "protocol_name": "Aave", "category": "lending"}
    if "developers.uniswap.org" in haystack or "uniswap" in haystack:
        return {"entity_name": "Uniswap", "protocol_name": "Uniswap", "category": "dex"}
    if "docs.sablier.com" in haystack or "sablier" in haystack:
        return {"entity_name": "Sablier", "protocol_name": "Sablier", "category": "streaming_payments"}
    return {"entity_name": None, "protocol_name": None, "category": "unknown"}


def network_from_source_url(source_url: str | None) -> str | None:
    if not source_url:
        return None
    stem = Path(urlparse(source_url).path).stem
    match = re.search(r"AaveV3([A-Za-z0-9]+)$", stem)
    if not match:
        return None
    return normalize_network_label(match.group(1))


def normalize_network_label(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[_/]+", " ", str(value)).strip()
    cleaned = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    key = cleaned.lower().replace("-", " ")
    aliases = {
        "mainnet": "Ethereum",
        "ethereum mainnet": "Ethereum",
        "eth": "Ethereum",
        "bnb": "BSC",
        "bnb chain": "BSC",
        "binance smart chain": "BSC",
        "avalanche": "Avalanche-C",
        "avalanche c": "Avalanche-C",
        "avalanche c chain": "Avalanche-C",
        "avalanche c-chain": "Avalanche-C",
        "polygon pos": "Polygon",
        "zksync era": "ZKSync Era",
        "zk sync era": "ZKSync Era",
    }
    if key in aliases:
        return aliases[key]
    if key in {"arbitrum", "base", "optimism", "polygon", "ethereum", "abstract"}:
        return key.title() if key != "ethereum" else "Ethereum"
    return cleaned


def infer_role(*values: str | None) -> str:
    text = " ".join(value or "" for value in values).lower()
    if "factory" in text:
        return "factory_contract"
    if "router" in text:
        return "router_contract"
    if "pool_addresses_provider" in text or "addresses_provider" in text or "address provider" in text or "provider" in text:
        return "address_provider"
    if "price_oracle" in text or "oracle" in text:
        return "oracle"
    if "configurator" in text:
        return "protocol_configurator"
    if "collector" in text or "treasury" in text:
        return "treasury"
    if "nftdescriptor" in text or "nft descriptor" in text or "descriptor" in text:
        return "nft_descriptor"
    if "flow" in text or "stream" in text or "lockup" in text:
        return "protocol_contract"
    if "token" in text:
        return "token_contract"
    if "pool" in text:
        return "liquidity_pool"
    return "protocol_contract"


def deployment_tables_from_structured_tables(
    tables: list[dict],
    *,
    source_url: str | None,
    source_input_type: str,
    evidence_type: str,
    text: str = "",
    default_network: str | None = None,
    default_confidence: int = 90,
) -> list[dict]:
    profile = infer_profile(source_url, text)
    deployment_tables: list[dict] = []
    for table_index, table in enumerate(tables, start=1):
        headers = [str(header) for header in table.get("headers", [])]
        address_headers = _address_headers(headers)
        if not address_headers:
            continue
        if not _looks_like_deployment_table(headers, address_headers, table.get("heading") or default_network):
            continue
        rows = []
        for row_number, row in enumerate(table.get("rows", []), start=int(table.get("start_line") or 1)):
            if not isinstance(row, dict):
                continue
            network = _network_from_row(row, headers) or normalize_network_label(table.get("heading")) or default_network
            contract_name = _contract_name_from_row(row, headers)
            for address_header in address_headers:
                for address in EVM_ADDRESS_RE.findall(str(row.get(address_header) or "")):
                    role = infer_role(address_header, contract_name)
                    confidence = default_confidence if network else 75
                    rows.append(
                        _deployment_row(
                            profile=profile,
                            network=network,
                            address=address,
                            contract_name=contract_name or _contract_name_from_header(address_header),
                            role=role,
                            evidence_type=evidence_type,
                            confidence=confidence,
                            source_url=source_url,
                            source_row=row.get("_row_number") or row_number,
                            raw_row=row,
                            column_name=address_header,
                            role_source=address_header,
                        )
                    )
        if rows:
            deployment_tables.append(
                {
                    "name": table.get("name") or f"deployment_table_{table_index}",
                    "headers": DEPLOYMENT_HEADERS,
                    "rows": rows,
                    "start_line": rows[0].get("_row_number") or table.get("start_line") or 1,
                    "metadata": {
                        "source_input_type": source_input_type,
                        "evidence_type": evidence_type,
                        **profile,
                    },
                }
            )
    return deployment_tables


def deployment_table_from_rows(
    rows: list[dict],
    *,
    source_url: str | None,
    source_input_type: str,
    evidence_type: str,
    text: str = "",
    table_name: str = "deployment_rows",
) -> list[dict]:
    if not rows:
        return []
    profile = infer_profile(source_url, text)
    normalized_rows = [
        _deployment_row(
            profile=profile,
            network=row.get("network"),
            address=str(row.get("address") or ""),
            contract_name=row.get("contract_name"),
            role=row.get("role") or infer_role(row.get("contract_name")),
            evidence_type=evidence_type,
            confidence=int(row.get("confidence") or 90),
            source_url=source_url,
            source_row=row.get("line_number"),
            raw_row=row.get("raw_row") or row,
            column_name=row.get("column_name"),
            role_source=row.get("role_source"),
        )
        for row in rows
        if row.get("address")
    ]
    return [
        {
            "name": table_name,
            "headers": DEPLOYMENT_HEADERS,
            "rows": normalized_rows,
            "start_line": normalized_rows[0].get("_row_number") or 1,
            "metadata": {"source_input_type": source_input_type, "evidence_type": evidence_type, **profile},
        }
    ] if normalized_rows else []


def markdown_tables(text: str) -> list[dict]:
    lines = text.splitlines()
    tables: list[dict] = []
    heading: str | None = None
    index = 0
    while index < len(lines):
        heading_match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", lines[index])
        if heading_match:
            heading = heading_match.group(1).strip()
            index += 1
            continue
        if index + 1 >= len(lines) or "|" not in lines[index] or not re.match(r"^\s*\|?[\s|:-]+\|?\s*$", lines[index + 1]):
            index += 1
            continue
        headers = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
        rows = []
        index += 2
        row_number = index + 1
        while index < len(lines) and "|" in lines[index]:
            values = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
            row = {headers[column]: values[column] if column < len(values) else "" for column in range(len(headers))}
            row["_row_number"] = row_number
            rows.append(row)
            index += 1
            row_number += 1
        tables.append({"name": f"markdown_table_{len(tables) + 1}", "headers": headers, "rows": rows, "heading": heading, "start_line": row_number})
    return tables


def json_deployment_tables(text: str, *, source_url: str | None, source_input_type: str, evidence_type: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    rows: list[dict] = []
    for path, value in _walk_json(data):
        if isinstance(value, str):
            for address in EVM_ADDRESS_RE.findall(value):
                network = next((normalize_network_label(part) for part in reversed(path) if normalize_network_label(part)), None)
                contract_name = path[-1] if path else None
                rows.append(
                    {
                        "network": network,
                        "address": address,
                        "contract_name": contract_name,
                        "role": infer_role(contract_name),
                        "line_number": None,
                        "raw_row": {"path": path, "value": value},
                        "column_name": contract_name,
                        "role_source": contract_name,
                    }
                )
    return deployment_table_from_rows(rows, source_url=source_url, source_input_type=source_input_type, evidence_type=evidence_type, text=text, table_name="json_deployment_registry")


def yaml_deployment_tables(text: str, *, source_url: str | None, source_input_type: str, evidence_type: str) -> list[dict]:
    rows: list[dict] = []
    stack: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key_match = re.match(r"^([A-Za-z0-9_. -]+)\s*:\s*(.*)$", stripped)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if key_match:
            key = key_match.group(1).strip()
            value = key_match.group(2).strip().strip("'\"")
            stack.append((indent, key))
        else:
            key = stack[-1][1] if stack else None
            value = stripped.strip("- ").strip("'\"")
        for address in EVM_ADDRESS_RE.findall(value):
            path = [item for _, item in stack]
            network = next((normalize_network_label(part) for part in reversed(path) if normalize_network_label(part)), None)
            rows.append(
                {
                    "network": network,
                    "address": address,
                    "contract_name": key,
                    "role": infer_role(key),
                    "line_number": line_number,
                    "raw_row": {"path": path, "raw_line": line.strip()},
                    "column_name": key,
                    "role_source": key,
                    "confidence": 90 if network else 75,
                }
            )
    return deployment_table_from_rows(rows, source_url=source_url, source_input_type=source_input_type, evidence_type=evidence_type, text=text, table_name="yaml_deployment_registry")


def table_metadata(tables: list[dict], *, source_input_type: str, text: str, source_url: str | None) -> dict:
    profile = infer_profile(source_url, text)
    roles = sorted({str(row.get("Role")) for table in tables for row in table.get("rows", []) if row.get("Role")})
    evidence_types = sorted(
        {
            str((table.get("metadata") or {}).get("evidence_type") or row.get("Evidence Type"))
            for table in tables
            for row in table.get("rows", [])
            if (table.get("metadata") or {}).get("evidence_type") or row.get("Evidence Type")
        }
    )
    return {
        "source_input_type": source_input_type,
        "entity_name": profile.get("entity_name"),
        "protocol_name": profile.get("protocol_name"),
        "category": profile.get("category"),
        "expected_roles": roles,
        "evidence_types": evidence_types,
        "table_count": len(tables),
    }


def _address_headers(headers: list[str]) -> list[str]:
    return [header for header in headers if "address" in _normalize(header) and "email" not in _normalize(header)]


def _looks_like_deployment_table(headers: list[str], address_headers: list[str], context: str | None) -> bool:
    normalized = {_normalize(header) for header in headers}
    if {"network", "chain", "blockchain"} & normalized:
        return True
    if {"contract", "contract_name", "name", "module"} & normalized and address_headers:
        return True
    if context and address_headers:
        return True
    return any("deployment" in header for header in normalized)


def _network_from_row(row: dict, headers: list[str]) -> str | None:
    for header in headers:
        if _normalize(header) in {"network", "chain", "blockchain"}:
            return normalize_network_label(str(row.get(header) or ""))
    return None


def _contract_name_from_row(row: dict, headers: list[str]) -> str | None:
    for header in headers:
        if _normalize(header) in {"contract", "contract_name", "name", "module"}:
            value = str(row.get(header) or "").strip()
            return value or None
    return None


def _contract_name_from_header(header: str) -> str:
    return re.sub(r"\s+", " ", str(header).replace("_", " ")).strip()


def _deployment_row(
    *,
    profile: dict,
    network: str | None,
    address: str,
    contract_name: str | None,
    role: str,
    evidence_type: str,
    confidence: int,
    source_url: str | None,
    source_row: object,
    raw_row: dict,
    column_name: str | None,
    role_source: str | None,
) -> dict:
    return {
        "Entity": profile.get("entity_name"),
        "Protocol": profile.get("protocol_name"),
        "Category": profile.get("category"),
        "Network": network,
        "Chain": network,
        "Address": address,
        "Contract Name": contract_name,
        "Role": role,
        "Evidence Type": evidence_type,
        "Confidence": str(confidence),
        "Source URL": source_url,
        "Source Row / Line": source_row,
        "Raw Row JSON": json.dumps({"raw_row": raw_row, "contract_name": contract_name, "role_source": role_source, "column_name": column_name, "source_url": source_url}, sort_keys=True),
        "_row_number": source_row,
    }


def _walk_json(value, path: list[str] | None = None):
    path = path or []
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_json(item, [*path, str(key)])
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_json(item, [*path, str(index)])


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
