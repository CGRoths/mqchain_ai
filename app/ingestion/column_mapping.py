from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ColumnMapping:
    columns: dict[str, str] = field(default_factory=dict)
    unmapped_headers: list[str] = field(default_factory=list)

    def get(self, row: dict, field: str) -> str | None:
        header = self.columns.get(field)
        if not header:
            return None
        value = row.get(header)
        if value in {None, ""}:
            return None
        return str(value).strip()


class ColumnMappingService:
    SYNONYMS: dict[str, set[str]] = {
        "entity": {"entity", "exchange", "company", "auditee", "organization", "organisation", "cex", "platform"},
        "protocol": {"protocol", "project", "product", "application"},
        "category": {"category", "segment", "vertical"},
        "chain": {"chain", "network", "blockchain", "chain_name", "network_name", "ecosystem", "asset chain", "asset network"},
        "chain_id": {"chain_id", "chain id", "network id", "evm chain id"},
        "address": {
            "address",
            "wallet",
            "wallet_address",
            "wallet address",
            "public address",
            "contract",
            "contract_address",
            "contract address",
            "account",
            "account_address",
            "account address",
            "receiver",
            "holder",
            "reserve address",
            "reserve wallet",
            "wallets",
        },
        "deposit_address": {
            "deposit_address",
            "deposit address",
            "staking deposit address",
            "deposit wallet",
            "deposit wallet address",
        },
        "withdrawal_address": {
            "withdrawal_address",
            "withdrawal address",
            "withdrawal / cold address",
            "withdrawal cold address",
            "withdrawal/cold address",
            "withdrawal wallet",
            "withdrawal wallet address",
            "cold address",
            "cold_address",
            "cold wallet",
            "cold wallet address",
            "staking withdrawal address",
            "staking withdrawal wallet",
        },
        "validator_public_key": {
            "validator_public_key",
            "validator public key",
            "public key",
            "pubkey",
            "validator pubkey",
            "validator key",
            "withdrawal credentials",
        },
        "role": {
            "role",
            "wallet_type",
            "wallet type",
            "type",
            "label",
            "tag",
            "purpose",
            "contract_name",
            "contract name",
            "name",
            "wallet category",
            "wallet purpose",
            "wallet label / role",
            "wallet_label_role",
            "wallet label role",
            "wallet role",
            "wallet_role",
            "wallet label",
            "wallet_label",
        },
        "asset": {"asset", "token"},
        "source_url": {"source", "source_url", "url", "reference", "evidence", "source link", "evidence url"},
        "evidence_type": {"evidence_type", "evidence type", "proof type", "attestation type"},
        "source_row": {
            "source_row",
            "source row",
            "source page / line",
            "source_page_line",
            "source line",
            "source_line",
            "source page",
            "source_page",
            "row",
            "line",
            "record id",
        },
        "report_date": {"report_date", "report date", "reporting date", "date"},
        "audit_date": {"audit_date", "audit date", "attestation date"},
        "confidence": {"confidence", "confidence score", "score", "quality"},
        "notes": {"notes", "note", "comment", "comments", "remark", "remarks"},
    }

    @classmethod
    def map_headers(cls, headers: list[str]) -> ColumnMapping:
        normalized_to_header = {cls._normalize(header): header for header in headers if header is not None}
        columns: dict[str, str] = {}
        used: set[str] = set()
        for field, synonyms in cls.SYNONYMS.items():
            for synonym in synonyms:
                key = cls._normalize(synonym)
                if key in normalized_to_header:
                    header = normalized_to_header[key]
                    columns[field] = header
                    used.add(header)
                    break
        return ColumnMapping(columns=columns, unmapped_headers=[header for header in headers if header not in used])

    @classmethod
    def detected_columns(cls, headers: list[str]) -> dict[str, str]:
        return cls.map_headers(headers).columns

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
