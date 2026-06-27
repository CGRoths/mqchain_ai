from pathlib import Path

p = Path("app/ingestion/extraction_normalizer.py")
s = p.read_text(encoding="utf-8")

old = '''        for value in (_network_from_path(raw_row.source_file_path), _network_from_path(urlparse(raw_row.source_url or "").path)):
            label = _known_network_heading(value)
            if label:
                return label
        return None
'''

new = '''        for value in (_network_from_path(raw_row.source_file_path), _network_from_path(urlparse(raw_row.source_url or "").path)):
            label = _known_network_heading(value)
            if label:
                return label

        fallback = _default_evm_network_for_raw_row(raw_row)
        if fallback:
            return fallback

        return None
'''

if old not in s:
    raise SystemExit("target _infer_network tail block not found")
s = s.replace(old, new)

helper = '''

def _default_evm_network_for_raw_row(raw_row: RawExtractedRow) -> str | None:
    address_family = infer_address_family(raw_row.extracted_address)
    if address_family != "evm":
        return None
    if raw_row.source_input_type not in {
        "github_solidity_address_book",
        "github_json_deployment_registry",
        "github_markdown_deployment_table",
        "official_github_deployment_table",
    }:
        return None

    haystack = " ".join(
        str(value)
        for value in [
            raw_row.source_url,
            raw_row.source_file_path,
            raw_row.raw_row.get("source_url") if isinstance(raw_row.raw_row, dict) else None,
            raw_row.raw_row.get("source_file_path") if isinstance(raw_row.raw_row, dict) else None,
            raw_row.raw_row.get("github_directory_path") if isinstance(raw_row.raw_row, dict) else None,
        ]
        if value
    ).lower()

    if "arbitrum" in haystack:
        return "Arbitrum"
    if "base" in haystack:
        return "Base"
    if "optimism" in haystack:
        return "Optimism"
    if "polygon" in haystack or "matic" in haystack:
        return "Polygon"
    if "bsc" in haystack or "bnb" in haystack:
        return "BSC"
    if "avalanche" in haystack or "avax" in haystack:
        return "Avalanche-C"
    if "ethereum" in haystack or "mainnet" in haystack or "eth" in haystack:
        return "Ethereum"

    return "Ethereum"
'''

if "def _default_evm_network_for_raw_row(" not in s:
    marker = "\ndef _network_from_path("
    s = s.replace(marker, helper + marker)

p.write_text(s, encoding="utf-8")
print("patched extraction_normalizer network fallback before confidence scoring")
