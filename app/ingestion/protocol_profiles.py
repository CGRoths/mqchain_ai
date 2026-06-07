from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProtocolProfile:
    entity_name: str | None
    protocol_name: str | None
    category: str
    source_patterns: list[str]
    role_keywords: dict[str, str]
    default_label_type: str
    default_confidence_source: str
    sub_category: str | None = None
    profile_key: str = "generic"


class ProtocolProfileRegistry:
    def __init__(self, profiles: list[ProtocolProfile] | None = None) -> None:
        self.profiles = profiles or _default_profiles()
        self.generic = next(profile for profile in self.profiles if profile.profile_key == "generic")

    def match(
        self,
        *,
        source_url: str | None = None,
        source_file_path: str | None = None,
        text_sample: str = "",
        entity_hint: str | None = None,
    ) -> ProtocolProfile:
        haystack = " ".join(value or "" for value in (source_url, source_file_path, text_sample[:2048], entity_hint)).lower()
        for profile in self.profiles:
            if profile.profile_key == "generic":
                continue
            if any(pattern.lower() in haystack for pattern in profile.source_patterns):
                return profile
        return self.generic

    def infer_role(self, profile: ProtocolProfile, values: Any) -> str | None:
        parts = _flatten_values(values)
        normalized_text = _role_text(parts)
        matches: list[tuple[int, str]] = []
        for keyword_spec, role in profile.role_keywords.items():
            for keyword in keyword_spec.split("/"):
                normalized_keyword = _normalize_role_token(keyword)
                if normalized_keyword and normalized_keyword in normalized_text:
                    matches.append((len(normalized_keyword), role))
        if matches:
            return sorted(matches, key=lambda item: item[0], reverse=True)[0][1]
        if profile.profile_key == "generic" and any(re.search(r"[A-Za-z]", str(value or "")) for value in parts):
            return "protocol_contract"
        return None

    def infer_label_type(self, profile: ProtocolProfile, role: str | None) -> str:
        return role or profile.default_label_type


def _default_profiles() -> list[ProtocolProfile]:
    return [
        ProtocolProfile(
            profile_key="aave",
            entity_name="Aave",
            protocol_name="Aave",
            category="lending",
            source_patterns=["aave-dao/aave-address-book", "@aave-dao/aave-address-book", "aave"],
            role_keywords={
                "pool_addresses_provider/address_provider/provider": "address_provider",
                "configurator": "protocol_configurator",
                "collector/treasury": "treasury",
                "oracle": "oracle",
                "token": "token_contract",
                "pool": "lending_pool",
            },
            default_label_type="protocol_contract",
            default_confidence_source="protocol_profile:aave",
        ),
        ProtocolProfile(
            profile_key="sablier",
            entity_name="Sablier",
            protocol_name="Sablier",
            category="yield",
            sub_category="streaming_payments",
            source_patterns=["docs.sablier.com", "sablier-labs", "sablier"],
            role_keywords={
                "descriptor": "nft_descriptor",
                "lockup": "protocol_contract",
                "batch": "batch_contract",
                "helper": "helper_contract",
                "math": "math_library",
                "flow": "protocol_contract",
            },
            default_label_type="protocol_contract",
            default_confidence_source="protocol_profile:sablier",
        ),
        ProtocolProfile(
            profile_key="compound",
            entity_name="Compound",
            protocol_name="Compound",
            category="lending",
            source_patterns=["compound-finance/comet", "compound.finance"],
            role_keywords={
                "configurator": "protocol_configurator",
                "governor/admin": "governance_contract",
                "pricefeed/price_feed/oracle": "oracle",
                "basetoken/ctoken/token": "token_contract",
                "rewards": "rewards_contract",
                "factory": "factory_contract",
                "bulker": "helper_contract",
                "comet": "lending_market",
            },
            default_label_type="protocol_contract",
            default_confidence_source="protocol_profile:compound",
        ),
        ProtocolProfile(
            profile_key="uniswap",
            entity_name="Uniswap",
            protocol_name="Uniswap",
            category="dex",
            source_patterns=["developers.uniswap.org", "uniswap"],
            role_keywords={
                "factory": "factory_contract",
                "router": "router_contract",
                "quoter": "quoter_contract",
                "pair": "liquidity_pool",
                "pool": "liquidity_pool",
            },
            default_label_type="protocol_contract",
            default_confidence_source="protocol_profile:uniswap",
        ),
        ProtocolProfile(
            profile_key="safe",
            entity_name="Safe",
            protocol_name="Safe",
            category="smart_account_infra",
            source_patterns=["safe-global/safe-deployments", "safe.global"],
            role_keywords={
                "proxy_factory/proxyfactory": "proxy_factory",
                "multisend": "multisend_contract",
                "fallback": "fallback_handler",
                "singleton": "singleton_contract",
            },
            default_label_type="protocol_contract",
            default_confidence_source="protocol_profile:safe",
        ),
        ProtocolProfile(
            profile_key="generic",
            entity_name=None,
            protocol_name=None,
            category="unknown",
            source_patterns=[],
            role_keywords={},
            default_label_type="protocol_contract",
            default_confidence_source="protocol_profile:generic",
        ),
    ]


def _flatten_values(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (list, tuple, set)):
        result: list[str] = []
        for value in values:
            result.extend(_flatten_values(value))
        return result
    if isinstance(values, dict):
        return _flatten_values(list(values.keys()) + list(values.values()))
    return [str(values)]


def _role_text(values: list[str]) -> str:
    return " ".join(_normalize_role_token(value) for value in values)


def _normalize_role_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())
