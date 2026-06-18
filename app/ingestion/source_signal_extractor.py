from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


URL_RE = re.compile(r"https?://[^\s\"'<>),]+", re.IGNORECASE)


@dataclass(slots=True)
class SourceSignals:
    source_url: str | None = None
    final_url: str | None = None
    host: str | None = None
    root_domain: str | None = None
    subdomain: str | None = None
    url_path_tokens: list[str] = field(default_factory=list)
    github_org: str | None = None
    github_repo: str | None = None
    github_branch: str | None = None
    github_path: str | None = None
    filename: str | None = None
    filename_tokens: list[str] = field(default_factory=list)
    file_extension: str | None = None
    content_type: str | None = None
    sheet_names: list[str] = field(default_factory=list)
    document_title: str | None = None
    heading_tokens: list[str] = field(default_factory=list)
    table_header_tokens: list[str] = field(default_factory=list)
    package_scope: str | None = None
    package_name: str | None = None
    text_tokens: list[str] = field(default_factory=list)
    outbound_hosts: list[str] = field(default_factory=list)
    outbound_urls: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_source_signals(
    *,
    source_url: str | None = None,
    final_url: str | None = None,
    source_file_path: str | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    metadata: dict[str, Any] | None = None,
    raw_row: dict[str, Any] | None = None,
    text_sample: str | None = None,
    document_title: str | None = None,
) -> SourceSignals:
    metadata = metadata or {}
    raw_row = raw_row or {}
    url = final_url or source_url or _first_text(metadata, "final_url", "resolved_source_url", "source_url")
    parsed = urlparse(url or "")
    host = parsed.netloc.lower().removeprefix("www.") or None
    root_domain, subdomain = _domain_parts(host)
    path = parsed.path or ""
    github_org, github_repo, github_branch, github_path = _github_parts(host, path)
    resolved_filename = filename or _first_text(metadata, "filename", "source_file_name") or _filename_from_path(source_file_path or path)
    text = text_sample or _first_text(metadata, "text_sample", "document_text") or ""
    sheet_names = _unique_texts(
        _list_value(metadata.get("sheet_names"))
        + _list_value(metadata.get("parsed_sheet_names"))
        + _list_value(metadata.get("skipped_sheet_names"))
        + [_first_text(raw_row, "source_sheet", "sheet_name")]
    )
    title = document_title or _first_text(metadata, "document_title", "title") or _first_text(raw_row, "document_title")
    headings = _list_value(raw_row.get("heading_path")) + [_first_text(raw_row, "section_heading")]
    table_headers = [str(key) for key in raw_row.get("raw_row_json", raw_row).keys()] if isinstance(raw_row, dict) else []
    package_scope, package_name = _package_name(text, raw_row)
    outbound_urls = _unique_texts(URL_RE.findall(text[:32_000]))
    outbound_hosts = _unique_texts(urlparse(item).netloc.lower().removeprefix("www.") for item in outbound_urls)
    return SourceSignals(
        source_url=source_url,
        final_url=final_url or url,
        host=host,
        root_domain=root_domain,
        subdomain=subdomain,
        url_path_tokens=_tokens(path),
        github_org=github_org,
        github_repo=github_repo,
        github_branch=github_branch,
        github_path=github_path,
        filename=resolved_filename,
        filename_tokens=_tokens(resolved_filename),
        file_extension=Path(resolved_filename).suffix.lower() if resolved_filename else None,
        content_type=content_type or _first_text(metadata, "content_type"),
        sheet_names=sheet_names,
        document_title=title,
        heading_tokens=_tokens(" ".join(item for item in headings if item)),
        table_header_tokens=_tokens(" ".join(table_headers)),
        package_scope=package_scope,
        package_name=package_name,
        text_tokens=_tokens(text[:4096]),
        outbound_hosts=outbound_hosts,
        outbound_urls=outbound_urls[:50],
        raw={
            "metadata": metadata,
            "raw_row": raw_row,
            "source_file_path": source_file_path,
        },
    )


def source_signals_from_document(document, *, raw_row: dict[str, Any] | None = None) -> SourceSignals:
    return extract_source_signals(
        source_url=document.source_url,
        final_url=(document.metadata or {}).get("resolved_source_url"),
        source_file_path=document.source_file_path,
        filename=document.filename,
        content_type=document.content_type,
        metadata=document.metadata or {},
        raw_row=raw_row,
        text_sample=document.text or "",
        document_title=(document.metadata or {}).get("document_title"),
    )


def source_signals_from_raw_row(raw_row, *, text_sample: str = "") -> SourceSignals:
    row = raw_row.raw_row if hasattr(raw_row, "raw_row") else {}
    return extract_source_signals(
        source_url=getattr(raw_row, "source_url", None),
        source_file_path=getattr(raw_row, "source_file_path", None),
        filename=Path(getattr(raw_row, "source_file_path", "") or "").name or None,
        metadata=row,
        raw_row=row | {
            "heading_path": getattr(raw_row, "heading_path", []),
            "section_heading": getattr(raw_row, "section_heading", None),
            "table_name": getattr(raw_row, "table_name", None),
        },
        text_sample=text_sample,
    )


def _github_parts(host: str | None, path: str) -> tuple[str | None, str | None, str | None, str | None]:
    parts = [part for part in path.strip("/").split("/") if part]
    if host == "raw.githubusercontent.com" and len(parts) >= 3:
        return parts[0], parts[1], parts[2], "/".join(parts[3:]) or None
    if host in {"github.com", "www.github.com"} and len(parts) >= 2:
        branch = parts[3] if len(parts) >= 4 and parts[2] in {"blob", "tree"} else None
        github_path = "/".join(parts[4:]) if branch else "/".join(parts[2:])
        return parts[0], parts[1], branch, github_path or None
    return None, None, None, None


def _domain_parts(host: str | None) -> tuple[str | None, str | None]:
    if not host:
        return None, None
    parts = [part for part in host.split(".") if part]
    if len(parts) < 2:
        return host, None
    root = ".".join(parts[-2:])
    subdomain = ".".join(parts[:-2]) or None
    return root, subdomain


def _tokens(value: Any) -> list[str]:
    raw = str(value or "")
    compact_values = [token.lower() for token in re.split(r"[^A-Za-z0-9]+", raw) if token and not token.isdigit()]
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    split_values = [token.lower() for token in re.split(r"[^A-Za-z0-9]+", text) if token and not token.isdigit()]
    return _unique_texts([*compact_values, *split_values])


def _package_name(text: str, raw_row: dict[str, Any]) -> tuple[str | None, str | None]:
    values = [raw_row.get("name"), raw_row.get("package"), raw_row.get("package_name")]
    stripped = (text or "").lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            values.append(data.get("name"))
    for value in values:
        if not value:
            continue
        name = str(value).strip()
        if name.startswith("@") and "/" in name:
            scope, package = name.split("/", 1)
            return scope.lstrip("@") or None, package or None
        return None, name
    return None, None


def _filename_from_path(value: str | None) -> str | None:
    if not value:
        return None
    return Path(urlparse(value).path or value).name or None


def _first_text(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if value not in {None, ""}:
            return str(value)
    return None


def _list_value(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item not in {None, ""}]
    return [str(value)]


def _unique_texts(values) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
