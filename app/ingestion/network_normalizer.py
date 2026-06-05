from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedNetwork:
    raw_network: str | None
    canonical_chain: str | None
    chain_id: int | None
    chain_guess: str | None


class NetworkNormalizer:
    NETWORKS: dict[str, tuple[str, int | None, str]] = {
        "ethereum": ("ethereum", 1, "evm"),
        "eth": ("ethereum", 1, "evm"),
        "mainnet": ("ethereum", 1, "evm"),
        "ethereum mainnet": ("ethereum", 1, "evm"),
        "arbitrum": ("arbitrum", 42161, "evm"),
        "arbitrum one": ("arbitrum", 42161, "evm"),
        "base": ("base", 8453, "evm"),
        "bsc": ("bsc", 56, "evm"),
        "binance smart chain": ("bsc", 56, "evm"),
        "bnb chain": ("bsc", 56, "evm"),
        "optimism": ("optimism", 10, "evm"),
        "polygon": ("polygon", 137, "evm"),
        "avalanche": ("avalanche-c", 43114, "evm"),
        "tron": ("tron", None, "tron"),
        "trx": ("tron", None, "tron"),
        "bitcoin": ("bitcoin", None, "btc"),
        "btc": ("bitcoin", None, "btc"),
        "solana": ("solana", None, "solana"),
        "sol": ("solana", None, "solana"),
        "aptos": ("aptos", None, "aptos"),
        "sui": ("sui", None, "sui"),
        "xrp": ("xrp", None, "xrp"),
        "ripple": ("xrp", None, "xrp"),
        "ton": ("ton", None, "ton"),
        "coinex smart chain": ("coinex-smart-chain", None, "evm"),
        "coinex": ("coinex-smart-chain", None, "evm"),
        "cet": ("coinex-smart-chain", None, "evm"),
    }

    @classmethod
    def normalize(cls, value: str | int | None) -> NormalizedNetwork:
        if value is None:
            return NormalizedNetwork(None, None, None, None)
        raw = str(value).strip()
        if not raw:
            return NormalizedNetwork(raw, None, None, None)
        if raw.isdigit():
            chain_id = int(raw)
            for canonical, mapped_id, chain_guess in cls.NETWORKS.values():
                if mapped_id == chain_id:
                    return NormalizedNetwork(raw, canonical, chain_id, chain_guess)
            return NormalizedNetwork(raw, None, chain_id, "evm")
        key = cls._key(raw)
        mapped = cls.NETWORKS.get(key)
        if mapped is None:
            return NormalizedNetwork(raw, None, None, None)
        canonical, chain_id, chain_guess = mapped
        return NormalizedNetwork(raw, canonical, chain_id, chain_guess)

    @staticmethod
    def _key(value: str) -> str:
        key = value.strip().lower()
        key = key.replace("zk-sync", "zksync").replace("zk sync", "zksync")
        key = re.sub(r"[\s_/]+", " ", key.replace("-", " "))
        key = re.sub(r"\s+", " ", key).strip()
        return key
