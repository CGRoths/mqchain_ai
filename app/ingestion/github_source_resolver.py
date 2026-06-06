from __future__ import annotations

from urllib.parse import urlparse

import httpx

from app.core.config import settings


def github_blob_to_raw_url(source_url: str | None) -> str | None:
    if not source_url:
        return None
    parsed = urlparse(source_url)
    if parsed.netloc.lower() == "raw.githubusercontent.com":
        return source_url
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 5 or parts[2] != "blob":
        return None
    owner, repo, _, ref, *path_parts = parts
    if not path_parts:
        return None
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{'/'.join(path_parts)}"


def resolve_github_source(source_url: str | None, raw_content: bytes) -> tuple[str, str | None]:
    resolved_url = github_blob_to_raw_url(source_url) or source_url
    text = _decode(raw_content)
    if text and not (resolved_url and resolved_url != source_url and _looks_like_github_html(text)):
        return text, resolved_url
    if not resolved_url or resolved_url == source_url:
        return text, resolved_url
    try:
        with httpx.Client(timeout=settings.source_fetch_timeout_seconds, follow_redirects=True) as client:
            response = client.get(resolved_url)
            response.raise_for_status()
            return _decode(response.content), str(response.url)
    except Exception:
        return text, resolved_url


def _decode(content: bytes) -> str:
    if not content:
        return ""
    return content.decode("utf-8-sig", errors="replace")


def _looks_like_github_html(text: str) -> bool:
    sample = text[:4096].lower()
    return ("<html" in sample or "<!doctype html" in sample) and "github" in sample
