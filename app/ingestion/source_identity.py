from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

from app.ingestion.source_signal_extractor import SourceSignals


GENERIC_TOKENS = {
    "address",
    "addresses",
    "api",
    "app",
    "audit",
    "audits",
    "audited",
    "chain",
    "contracts",
    "contract",
    "core",
    "developer",
    "developers",
    "development",
    "deployment",
    "deployments",
    "docs",
    "documentation",
    "github",
    "main",
    "official",
    "org",
    "por",
    "proof",
    "registry",
    "reserve",
    "reserves",
    "wallet",
    "wallets",
    "www",
}
SUFFIX_TOKENS = {
    "app",
    "dao",
    "developers",
    "docs",
    "exchange",
    "finance",
    "foundation",
    "lab",
    "labs",
    "official",
    "org",
    "protocol",
}


@dataclass(slots=True)
class SourceIdentityCandidate:
    entity_slug: str | None
    entity_name: str | None
    protocol_slug: str | None
    protocol_name: str | None
    identity_confidence: int
    identity_method: str
    matched_signals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def infer_source_identity(signals: SourceSignals) -> SourceIdentityCandidate:
    scores: dict[str, int] = {}
    matches: dict[str, list[str]] = {}

    def add(source_name: str, values: Iterable[str], weight: int) -> None:
        for value in values:
            for slug in _candidate_slugs(value):
                scores[slug] = scores.get(slug, 0) + weight
                matches.setdefault(slug, []).append(f"{source_name}:{value}")

    root_label = (signals.root_domain or "").split(".")[0]
    add("root_domain", [root_label], 45)
    add("subdomain", (signals.subdomain or "").split("."), 10)
    add("github_org", [signals.github_org] if signals.github_org else [], 35)
    add("github_repo", [signals.github_repo] if signals.github_repo else [], 25)
    add("package_scope", [signals.package_scope] if signals.package_scope else [], 30)
    add("package_name", [signals.package_name] if signals.package_name else [], 25)
    add("filename", signals.filename_tokens, 18)
    add("sheet_name", _flatten_token_groups(signals.sheet_names), 20)
    add("document_title", _tokenize(signals.document_title), 18)
    add("heading", signals.heading_tokens, 14)
    add("url_path", signals.url_path_tokens, 8)
    add("table_header", signals.table_header_tokens, 5)
    add("text", signals.text_tokens[:80], 3)

    if not scores:
        return SourceIdentityCandidate(None, None, None, None, 0, "no_identity_signals", warnings=["identity_unknown"])

    slug, score = sorted(scores.items(), key=lambda item: (item[1], len(item[0])), reverse=True)[0]
    confidence = max(0, min(100, score))
    if confidence < 15:
        return SourceIdentityCandidate(None, None, None, None, confidence, "weak_identity_signals", matched_signals=matches.get(slug, []), warnings=["identity_low_confidence"])
    method = "multi_signal_identity" if len(matches.get(slug, [])) >= 2 else "single_signal_identity"
    name = _display_name(slug)
    return SourceIdentityCandidate(
        entity_slug=slug,
        entity_name=name,
        protocol_slug=slug,
        protocol_name=name,
        identity_confidence=confidence,
        identity_method=method,
        matched_signals=_dedupe(matches.get(slug, [])),
        warnings=[] if confidence >= 35 else ["identity_low_confidence"],
    )


def identity_from_profile(entity_name: str | None, protocol_name: str | None) -> SourceIdentityCandidate | None:
    name = entity_name or protocol_name
    if not name:
        return None
    slug = _slug_from_tokens(_tokenize(name))
    if not slug:
        return None
    return SourceIdentityCandidate(
        entity_slug=slug,
        entity_name=entity_name or _display_name(slug),
        protocol_slug=_slug_from_tokens(_tokenize(protocol_name)) if protocol_name else slug,
        protocol_name=protocol_name or entity_name or _display_name(slug),
        identity_confidence=80,
        identity_method="protocol_profile_enrichment",
        matched_signals=[f"profile:{name}"],
    )


def _candidate_slugs(value: str | None) -> list[str]:
    tokens = [token for token in _tokenize(value) if token not in GENERIC_TOKENS]
    if not tokens:
        return []
    slugs: list[str] = []
    for token in tokens:
        slug = _slug_from_tokens([token])
        if slug:
            slugs.append(slug)
    compact = _slug_from_tokens(tokens)
    if compact:
        slugs.append(compact)
    return _dedupe(slugs)


def _slug_from_tokens(tokens: list[str]) -> str | None:
    cleaned = [token for token in tokens if token and token not in GENERIC_TOKENS]
    while len(cleaned) > 1 and cleaned[-1] in SUFFIX_TOKENS:
        cleaned.pop()
    if not cleaned:
        return None
    slug = "-".join(cleaned)
    return slug if len(slug) >= 2 else None


def _tokenize(value: str | None) -> list[str]:
    raw = str(value or "")
    compact_values = [token.lower() for token in re.split(r"[^A-Za-z0-9]+", raw) if token and not token.isdigit()]
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    split_values = [token.lower() for token in re.split(r"[^A-Za-z0-9]+", text) if token and not token.isdigit()]
    return _dedupe([*compact_values, *split_values])


def _flatten_token_groups(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(_tokenize(value))
    return result


def _display_name(slug: str) -> str:
    return " ".join(part.upper() if len(part) <= 3 and part not in {"aave"} else part.capitalize() for part in slug.split("-"))


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
