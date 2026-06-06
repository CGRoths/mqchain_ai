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
        "arbitrum nova": ("arbitrum-nova", 42170, "evm"),
        "base": ("base", 8453, "evm"),
        "bsc": ("bsc", 56, "evm"),
        "binance smart chain": ("bsc", 56, "evm"),
        "bnb chain": ("bsc", 56, "evm"),
        "optimism": ("optimism", 10, "evm"),
        "polygon": ("polygon", 137, "evm"),
        "avalanche": ("avalanche-c", 43114, "evm"),
        "avalanche-c": ("avalanche-c", 43114, "evm"),
        "avalanche c": ("avalanche-c", 43114, "evm"),
        "avalanche-x": ("avalanche-x", None, "avalanche"),
        "avalanche x": ("avalanche-x", None, "avalanche"),
        "celo": ("celo", 42220, "evm"),
        "codex": ("codex", None, "evm"),
        "corn": ("corn", None, "evm"),
        "fantom": ("fantom", 250, "evm"),
        "hedera": ("hedera", None, "hedera"),
        "hyperevm": ("hyperevm", None, "evm"),
        "kaia": ("kaia", None, "evm"),
        "kava evm": ("kava-evm", 2222, "evm"),
        "linea": ("linea", 59144, "evm"),
        "manta": ("manta", None, "evm"),
        "mantle": ("mantle", 5000, "evm"),
        "monad": ("monad", None, "evm"),
        "plasma": ("plasma", None, "evm"),
        "scroll": ("scroll", 534352, "evm"),
        "sei evm": ("sei-evm", None, "evm"),
        "sonic": ("sonic", None, "evm"),
        "xdc": ("xdc", 50, "evm"),
        "zksync era": ("zksync-era", 324, "evm"),
        "zksync lite": ("zksync-lite", None, "evm"),
        "tron": ("tron", None, "tron"),
        "trx": ("tron", None, "tron"),
        "bitcoin": ("bitcoin", None, "btc"),
        "btc": ("bitcoin", None, "btc"),
        "dogecoin": ("dogecoin", None, "dogecoin"),
        "doge": ("dogecoin", None, "dogecoin"),
        "litecoin": ("litecoin", None, "litecoin"),
        "ltc": ("litecoin", None, "litecoin"),
        "solana": ("solana", None, "solana"),
        "sol": ("solana", None, "solana"),
        "aptos": ("aptos", None, "aptos"),
        "sui": ("sui", None, "sui"),
        "cosmos": ("cosmos", None, "cosmos"),
        "dydx": ("dydx", None, "cosmos"),
        "polkadot ah": ("polkadot-asset-hub", None, "substrate"),
        "polkadot asset hub": ("polkadot-asset-hub", None, "substrate"),
        "xrp": ("xrp", None, "xrp"),
        "ripple": ("xrp", None, "xrp"),
        "xrp ledger": ("xrp", None, "xrp"),
        "ton": ("ton", None, "ton"),
        "bera": ("bera", None, "evm"),
        "vaulta": ("vaulta", None, "evm"),
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
