from pathlib import Path

p = Path("app/ingestion/candidate_builder.py")
s = p.read_text(encoding="utf-8")

old = '''    def _candidate(self, row: NormalizedExtractedRow) -> CandidatePreview:
        network = NetworkNormalizer.normalize(row.network)
        raw_reference = {
            **row.raw_reference,
            "source_document_key": row.source_document_key,
            "source_file_path": row.source_file_path,
            "original_value": row.address,
            "normalized_value": row.normalized_address,
            "confidence_source": row.confidence_source,
            "confidence_parser": row.confidence_parser,
            "confidence_role": row.confidence_role,
        }
        source_type = str(raw_reference.get("final_source_type") or raw_reference.get("source_type") or "deployment_source")
        return CandidatePreview(
            address=row.address,
            normalized_address=row.normalized_address,
            entity_name=row.entity_name,
            source_network=row.network,
            chain_guess=network.chain_guess or row.address_family,
            chain_slug=network.canonical_chain,
            chain_id=row.chain_id if row.chain_id is not None else network.chain_id,
'''

new = '''    def _candidate(self, row: NormalizedExtractedRow) -> CandidatePreview:
        effective_network = row.network or self._default_network_for_row(row)
        network = NetworkNormalizer.normalize(effective_network)
        raw_reference = {
            **row.raw_reference,
            "source_document_key": row.source_document_key,
            "source_file_path": row.source_file_path,
            "original_value": row.address,
            "normalized_value": row.normalized_address,
            "confidence_source": row.confidence_source,
            "confidence_parser": row.confidence_parser,
            "confidence_role": row.confidence_role,
        }
        if effective_network and not row.network:
            raw_reference.setdefault("network_inference_reason", "default_evm_network_for_pipeline_row")
        source_type = str(raw_reference.get("final_source_type") or raw_reference.get("source_type") or "deployment_source")
        return CandidatePreview(
            address=row.address,
            normalized_address=row.normalized_address,
            entity_name=row.entity_name,
            source_network=effective_network,
            chain_guess=network.chain_guess or row.address_family,
            chain_slug=network.canonical_chain,
            chain_id=row.chain_id if row.chain_id is not None else network.chain_id,
'''

if old not in s:
    raise SystemExit("target _candidate block not found")
s = s.replace(old, new)

helper = '''
    @staticmethod
    def _default_network_for_row(row: NormalizedExtractedRow) -> str | None:
        if row.address_family != "evm":
            return None
        if row.source_input_type not in {
            "github_solidity_address_book",
            "github_json_deployment_registry",
            "github_markdown_deployment_table",
            "official_github_deployment_table",
        }:
            return None

        haystack = " ".join(
            str(value)
            for value in [
                row.source_url,
                row.source_file_path,
                row.raw_reference.get("source_url") if isinstance(row.raw_reference, dict) else None,
                row.raw_reference.get("source_file_path") if isinstance(row.raw_reference, dict) else None,
                row.raw_reference.get("github_directory_path") if isinstance(row.raw_reference, dict) else None,
            ]
            if value
        ).lower()

        if "arbitrum" in haystack:
            return "arbitrum"
        if "base" in haystack:
            return "base"
        if "optimism" in haystack:
            return "optimism"
        if "polygon" in haystack or "matic" in haystack:
            return "polygon"
        if "bsc" in haystack or "bnb" in haystack:
            return "bsc"
        if "avalanche" in haystack or "avax" in haystack:
            return "avalanche-c"
        if "ethereum" in haystack or "mainnet" in haystack or "eth" in haystack:
            return "ethereum"

        return "ethereum"

'''

if "def _default_network_for_row(" not in s:
    marker = "\n    def _table_preview("
    s = s.replace(marker, "\n" + helper + marker)

s = s.replace('"Network": row.network,', '"Network": row.network or self._default_network_for_row(row),')
s = s.replace('"Chain": row.network,', '"Chain": row.network or self._default_network_for_row(row),')

p.write_text(s, encoding="utf-8")
print("patched candidate_builder pipeline EVM fallback")
