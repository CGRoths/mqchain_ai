from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.core.config import settings


ALLOWED_GITHUB_DIRECTORY_EXTENSIONS = {".json", ".yaml", ".yml", ".ts", ".js", ".sol", ".md"}
PREFERRED_DEPLOYMENT_FILENAMES = [
    "configuration.json",
    "roots.json",
    "relations.ts",
    "deploy.ts",
    "deployments.json",
    "addresses.json",
    "deployment.json",
]
PREFERRED_DEPLOYMENT_PATTERNS = (
    re.compile(r".*\.deployment\.json$", re.IGNORECASE),
    re.compile(r".*\.deploy\.json$", re.IGNORECASE),
    re.compile(r".*\.addresses\.json$", re.IGNORECASE),
)
ALLOWED_GITHUB_DIRECTORY_FOLDERS = {
    "deployments",
    "addresses",
    "config",
    "configs",
    "markets",
    "networks",
    "chains",
    "artifacts",
    "migrations",
}


@dataclass(frozen=True)
class GitHubTreeUrl:
    owner: str
    repo: str
    branch: str
    path: str
    original_url: str


@dataclass
class GitHubFetchedFile:
    path: str
    name: str
    download_url: str | None
    api_url: str
    content: bytes
    content_type: str | None
    depth: int


@dataclass
class GitHubDirectoryResult:
    tree: GitHubTreeUrl
    files: list[GitHubFetchedFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    api_urls: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


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


def parse_github_blob_url(source_url: str | None) -> tuple[str, str, str, str] | None:
    if not source_url:
        return None
    parsed = urlparse(source_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.netloc.lower() == "raw.githubusercontent.com" and len(parts) >= 4:
        owner, repo, ref, *path_parts = parts
        return owner, repo, ref, "/".join(path_parts)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    if len(parts) < 5 or parts[2] != "blob":
        return None
    owner, repo, _blob, ref, *path_parts = parts
    return owner, repo, ref, "/".join(path_parts)


def parse_github_tree_url(source_url: str | None) -> GitHubTreeUrl | None:
    if not source_url:
        return None
    parsed = urlparse(source_url)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 5 or parts[2] != "tree":
        return None
    owner, repo, _tree, branch, *path_parts = parts
    path = "/".join(path_parts).strip("/")
    if not path:
        return None
    return GitHubTreeUrl(owner=owner, repo=repo, branch=branch, path=path, original_url=source_url)


def resolve_github_directory(source_url: str | None) -> GitHubDirectoryResult | None:
    tree = parse_github_tree_url(source_url)
    if tree is None:
        return None
    crawler = _GitHubDirectoryCrawler(tree)
    return crawler.resolve()


def resolve_github_source(source_url: str | None, raw_content: bytes) -> tuple[str, str | None]:
    resolved_url = github_blob_to_raw_url(source_url) or source_url
    text = _decode(raw_content)
    if text and not (resolved_url and resolved_url != source_url and _looks_like_github_html(text)):
        return text, resolved_url
    if not resolved_url or resolved_url == source_url:
        return text, resolved_url
    try:
        with httpx.Client(timeout=settings.source_fetch_timeout_seconds, follow_redirects=True) as client:
            response = client.get(resolved_url, headers=_github_headers())
            if getattr(response, "status_code", 200) < 400:
                return _decode(response.content), str(response.url)
            api_text = _fetch_github_blob_via_contents_api(client, source_url)
            if api_text:
                return api_text, resolved_url
    except Exception:
        pass
    try:
        with httpx.Client(timeout=settings.source_fetch_timeout_seconds, follow_redirects=True) as client:
            api_text = _fetch_github_blob_via_contents_api(client, source_url)
            if api_text:
                return api_text, resolved_url
    except Exception:
        pass
    return text, resolved_url


def _fetch_github_blob_via_contents_api(client: httpx.Client, source_url: str | None) -> str:
    parsed = parse_github_blob_url(source_url)
    if parsed is None:
        return ""
    owner, repo, ref, path = parsed
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    response = client.get(api_url, headers=_github_headers())
    if getattr(response, "status_code", 200) >= 400:
        return ""
    payload = response.json()
    encoded = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(encoded, str):
        return ""
    try:
        return base64.b64decode(encoded).decode("utf-8-sig", errors="replace")
    except Exception:
        return ""


def _decode(content: bytes) -> str:
    if not content:
        return ""
    return content.decode("utf-8-sig", errors="replace")


def _looks_like_github_html(text: str) -> bool:
    sample = text[:4096].lower()
    return ("<html" in sample or "<!doctype html" in sample) and "github" in sample


class _GitHubDirectoryCrawler:
    def __init__(self, tree: GitHubTreeUrl) -> None:
        self.tree = tree
        self.files: list[GitHubFetchedFile] = []
        self.warnings: list[str] = []
        self.api_urls: list[str] = []
        self.fetch_failed_urls: list[str] = []
        self.fetch_failures: list[dict] = []
        self.github_rate_limit_limit: str | None = None
        self.github_rate_limit_remaining: str | None = None
        self.github_rate_limit_reset: str | None = None
        self.root_deployment_scan_mode = _is_root_deployment_path(tree.path)
        self.discovered_networks: set[str] = set()
        self.discovered_markets: set[tuple[str, str | None]] = set()
        self.fetched_networks: set[str] = set()
        self.fetched_markets: set[tuple[str, str | None]] = set()
        self.network_market_counts: dict[str, int] = {}
        self.fetched_file_count_by_network: dict[str, int] = {}
        self.fetched_file_count_by_market: dict[str, int] = {}
        self.skipped_migrations_count = 0
        self.skipped_unsupported_file_count = 0
        self.priority_file_seen = False
        self.root_migration_paths: list[str] = []
        self._file_limit_reached = False
        self._depth_limit_reached = False

    def resolve(self) -> GitHubDirectoryResult:
        with httpx.Client(timeout=settings.source_fetch_timeout_seconds, follow_redirects=True) as client:
            if self.root_deployment_scan_mode:
                self._crawl_root_deployment(client)
            else:
                self._crawl(client, self.tree.path, depth=0)
        if not self.files:
            self.warnings.append("github_directory_no_supported_files")
        if self.root_deployment_scan_mode and not self.priority_file_seen:
            self.warnings.append("github_directory_no_priority_files_found")
        if self.fetch_failed_urls and self.files:
            self.warnings.append("github_directory_partial_fetch_failures")
            self.warnings = [warning for warning in self.warnings if warning != "github_directory_fetch_failed"]
        return GitHubDirectoryResult(
            tree=self.tree,
            files=self.files,
            warnings=_dedupe(self.warnings),
            api_urls=_dedupe(self.api_urls),
            metadata=self._metadata(),
        )

    def _crawl(self, client: httpx.Client, path: str, *, depth: int) -> None:
        if depth > settings.github_crawl_max_depth:
            self._warn_once("github_directory_depth_limit_reached")
            return
        if len(self.files) >= settings.github_crawl_max_files:
            self._warn_once("github_directory_file_limit_reached")
            return

        api_url = self._api_url(path)
        self.api_urls.append(api_url)
        try:
            response = client.get(api_url, headers=_github_headers())
            self._record_rate_limit(response)
            if getattr(response, "status_code", 200) >= 400:
                self._record_fetch_failure(api_url, response=response)
                return
            payload = response.json()
        except Exception as exc:
            self._record_fetch_failure(api_url, response=locals().get("response"), exception=exc)
            return

        if isinstance(payload, list):
            if not payload:
                self.warnings.append("github_directory_empty")
                return
            for item in self._sort_items(payload):
                if len(self.files) >= settings.github_crawl_max_files:
                    self._warn_once("github_directory_file_limit_reached")
                    return
                item_type = item.get("type")
                item_path = str(item.get("path") or "")
                if item_type == "dir":
                    if self._allow_directory(item_path):
                        self._crawl(client, item_path, depth=depth + 1)
                    continue
                if item_type == "file":
                    self._fetch_file_item(client, item, depth=depth)
            return

        if isinstance(payload, dict) and payload.get("type") == "file":
            self._fetch_file_item(client, payload, depth=depth)

    def _crawl_root_deployment(self, client: httpx.Client) -> None:
        root_items = self._list_dir(client, self.tree.path)
        if root_items is None:
            return
        if not root_items:
            self.warnings.append("github_directory_empty")
            return

        network_dirs = [item for item in self._sort_items(root_items) if item.get("type") == "dir"]
        if len(network_dirs) > settings.github_crawl_max_networks:
            self.warnings.append("github_directory_network_limit_reached")
            network_dirs = network_dirs[: settings.github_crawl_max_networks]

        targets: list[tuple[str, str, str | None, list[dict]]] = []
        for network_item in network_dirs:
            network_path = str(network_item.get("path") or "")
            network_name = str(network_item.get("name") or Path(network_path).name)
            self.discovered_networks.add(network_name)
            network_items = self._list_dir(client, network_path)
            if network_items is None:
                continue
            direct_files = [item for item in network_items if item.get("type") == "file"]
            if direct_files:
                targets.append((network_path, network_name, None, direct_files))

            market_dirs = [item for item in self._sort_items(network_items) if item.get("type") == "dir" and _dir_name(item).lower() != "migrations"]
            if len(market_dirs) > settings.github_crawl_max_markets_per_network:
                self.warnings.append("github_directory_market_limit_reached")
                market_dirs = market_dirs[: settings.github_crawl_max_markets_per_network]
            self.network_market_counts[network_name] = len(market_dirs)
            for market_item in market_dirs:
                market_path = str(market_item.get("path") or "")
                market_name = str(market_item.get("name") or Path(market_path).name)
                self.discovered_markets.add((network_name, market_name))
                market_items = self._list_dir(client, market_path)
                if market_items is None:
                    continue
                for item in market_items:
                    if item.get("type") == "dir" and _dir_name(item).lower() == "migrations":
                        self._handle_root_migrations(str(item.get("path") or ""))
                targets.append((market_path, network_name, market_name, [item for item in market_items if item.get("type") == "file"]))

        self._fetch_priority_targets(client, targets)
        if settings.github_crawl_include_migrations:
            for migration_path in self.root_migration_paths:
                if len(self.files) >= settings.github_crawl_max_files:
                    self._warn_once("github_directory_file_limit_reached")
                    return
                self._crawl(client, migration_path, depth=3)

    def _fetch_priority_targets(self, client: httpx.Client, targets: list[tuple[str, str, str | None, list[dict]]]) -> None:
        max_priority = max(0, settings.github_crawl_max_priority_files_per_market)
        ranked_targets: list[tuple[str, str, str | None, list[dict]]] = []
        for target_path, network_name, market_name, items in targets:
            priority_items = [item for item in self._sort_items(items) if _priority_rank(str(item.get("name") or Path(str(item.get("path") or "")).name)) is not None]
            unsupported = [item for item in items if item.get("type") == "file" and not self._allow_file(str(item.get("path") or ""))]
            self.skipped_unsupported_file_count += len(unsupported)
            if unsupported:
                self.warnings.append("github_directory_unsupported_file_skipped")
            ranked_targets.append((target_path, network_name, market_name, priority_items[:max_priority]))

        for priority_index in range(max_priority):
            for _target_path, network_name, market_name, items in ranked_targets:
                if len(self.files) >= settings.github_crawl_max_files:
                    self._warn_once("github_directory_file_limit_reached")
                    return
                if priority_index >= len(items):
                    continue
                fetched_before = len(self.files)
                self._fetch_file_item(client, items[priority_index], depth=2)
                if len(self.files) > fetched_before:
                    self.priority_file_seen = True
                    self.fetched_networks.add(network_name)
                    self.fetched_markets.add((network_name, market_name))

    def _fetch_file_item(self, client: httpx.Client, item: dict, *, depth: int) -> None:
        item_path = str(item.get("path") or "")
        name = str(item.get("name") or Path(item_path).name)
        if not self._allow_file(item_path):
            self.skipped_unsupported_file_count += 1
            self.warnings.append("github_directory_unsupported_file_skipped")
            return
        size = int(item.get("size") or 0)
        if size > settings.github_crawl_max_bytes_per_file:
            self.skipped_unsupported_file_count += 1
            self.warnings.append("github_directory_unsupported_file_skipped")
            return
        content = self._content_from_item(client, item)
        if content is None:
            return
        if len(content) > settings.github_crawl_max_bytes_per_file:
            self.skipped_unsupported_file_count += 1
            self.warnings.append("github_directory_unsupported_file_skipped")
            return
        network, market = _network_market_tokens_from_path(item_path)
        if network:
            self.fetched_file_count_by_network[network] = self.fetched_file_count_by_network.get(network, 0) + 1
        if network:
            market_key = f"{network}/{market}" if market else network
            self.fetched_file_count_by_market[market_key] = self.fetched_file_count_by_market.get(market_key, 0) + 1
        self.files.append(
            GitHubFetchedFile(
                path=item_path,
                name=name,
                download_url=item.get("download_url"),
                api_url=str(item.get("url") or self._api_url(item_path)),
                content=content,
                content_type=_content_type_for_path(item_path),
                depth=depth,
            )
        )

    def _content_from_item(self, client: httpx.Client, item: dict) -> bytes | None:
        encoded = item.get("content")
        if isinstance(encoded, str):
            try:
                return base64.b64decode(encoded)
            except Exception:
                pass
        download_url = item.get("download_url")
        url = str(download_url or item.get("url") or "")
        if not download_url:
            self._record_fetch_failure(url or str(item.get("path") or ""), exception=RuntimeError("missing download_url"))
            return None
        try:
            response = client.get(str(download_url), headers=_github_headers())
            self._record_rate_limit(response)
            if getattr(response, "status_code", 200) >= 400:
                self._record_fetch_failure(str(download_url), response=response)
                return None
            return response.content
        except Exception as exc:
            self._record_fetch_failure(str(download_url), response=locals().get("response"), exception=exc)
            return None

    def _list_dir(self, client: httpx.Client, path: str) -> list[dict] | None:
        api_url = self._api_url(path)
        self.api_urls.append(api_url)
        try:
            response = client.get(api_url, headers=_github_headers())
            self._record_rate_limit(response)
            if getattr(response, "status_code", 200) >= 400:
                self._record_fetch_failure(api_url, response=response)
                return None
            payload = response.json()
        except Exception as exc:
            self._record_fetch_failure(api_url, response=locals().get("response"), exception=exc)
            return None
        return payload if isinstance(payload, list) else None

    def _api_url(self, path: str) -> str:
        return f"https://api.github.com/repos/{self.tree.owner}/{self.tree.repo}/contents/{path}?ref={self.tree.branch}"

    def _allow_directory(self, path: str) -> bool:
        parts = [part.lower() for part in path.split("/") if part]
        return any(part in ALLOWED_GITHUB_DIRECTORY_FOLDERS for part in parts)

    def _allow_file(self, path: str) -> bool:
        suffix = Path(path).suffix.lower()
        return suffix in ALLOWED_GITHUB_DIRECTORY_EXTENSIONS

    def _sort_items(self, items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda item: (_item_rank(item), str(item.get("path") or item.get("name") or "")))

    def _warn_once(self, warning: str) -> None:
        if warning == "github_directory_file_limit_reached" and self._file_limit_reached:
            return
        if warning == "github_directory_depth_limit_reached" and self._depth_limit_reached:
            return
        if warning == "github_directory_file_limit_reached":
            self._file_limit_reached = True
        if warning == "github_directory_depth_limit_reached":
            self._depth_limit_reached = True
        self.warnings.append(warning)

    def _record_rate_limit(self, response) -> None:
        headers = getattr(response, "headers", {}) or {}
        self.github_rate_limit_limit = headers.get("x-ratelimit-limit") or self.github_rate_limit_limit
        self.github_rate_limit_remaining = headers.get("x-ratelimit-remaining") or self.github_rate_limit_remaining
        self.github_rate_limit_reset = headers.get("x-ratelimit-reset") or self.github_rate_limit_reset

    def _record_fetch_failure(self, url: str, *, response=None, exception: Exception | None = None) -> None:
        self.fetch_failed_urls.append(url)
        record = _fetch_failure_record(url, response=response, exception=exception)
        self.fetch_failures.append(record)
        self._record_rate_limit(response)
        warning = _warning_for_failure(response=response, exception=exception)
        self.warnings.append(warning)

    def _handle_root_migrations(self, path: str) -> None:
        if settings.github_crawl_include_migrations:
            self.root_migration_paths.append(path)
            return
        self.skipped_migrations_count += 1
        self.warnings.append("github_directory_migrations_skipped_for_root_scan")

    def _metadata(self) -> dict:
        return {
            "root_deployment_scan_mode": self.root_deployment_scan_mode,
            "discovered_network_count": len(self.discovered_networks),
            "discovered_market_count": len(self.discovered_markets),
            "fetched_network_count": len(self.fetched_networks) if self.root_deployment_scan_mode else len(self.fetched_file_count_by_network),
            "fetched_market_count": len(self.fetched_markets) if self.root_deployment_scan_mode else len(self.fetched_file_count_by_market),
            "network_market_counts": dict(sorted(self.network_market_counts.items())),
            "fetched_file_count_by_network": dict(sorted(self.fetched_file_count_by_network.items())),
            "fetched_file_count_by_market": dict(sorted(self.fetched_file_count_by_market.items())),
            "skipped_migrations_count": self.skipped_migrations_count,
            "skipped_unsupported_file_count": self.skipped_unsupported_file_count,
            "fetch_failed_count": len(self.fetch_failed_urls),
            "fetch_failed_urls": _dedupe(self.fetch_failed_urls),
            "fetch_failures": self.fetch_failures,
            "github_rate_limit_limit": self.github_rate_limit_limit,
            "github_rate_limit_remaining": self.github_rate_limit_remaining,
            "github_rate_limit_reset": self.github_rate_limit_reset,
        }


def _item_rank(item: dict) -> tuple[int, int]:
    if item.get("type") == "dir":
        return (0, 0)
    name = str(item.get("name") or Path(str(item.get("path") or "")).name)
    lower_name = name.lower()
    preferred_index = PREFERRED_DEPLOYMENT_FILENAMES.index(lower_name) if lower_name in PREFERRED_DEPLOYMENT_FILENAMES else None
    if preferred_index is not None:
        return (1, preferred_index)
    if any(pattern.match(name) for pattern in PREFERRED_DEPLOYMENT_PATTERNS):
        return (1, len(PREFERRED_DEPLOYMENT_FILENAMES))
    return (2, 0)


def _priority_rank(name: str) -> int | None:
    lower_name = name.lower()
    if lower_name in PREFERRED_DEPLOYMENT_FILENAMES:
        return PREFERRED_DEPLOYMENT_FILENAMES.index(lower_name)
    if any(pattern.match(name) for pattern in PREFERRED_DEPLOYMENT_PATTERNS):
        return len(PREFERRED_DEPLOYMENT_FILENAMES)
    return None


def _dir_name(item: dict) -> str:
    return str(item.get("name") or Path(str(item.get("path") or "")).name)


def _is_root_deployment_path(path: str) -> bool:
    normalized = path.strip("/").lower()
    return normalized in {"deployments", "addresses", "networks", "chains"}


def _network_market_tokens_from_path(path: str) -> tuple[str | None, str | None]:
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    lowered = [part.lower() for part in parts]
    for marker in ("deployments", "addresses", "networks", "chains"):
        if marker in lowered:
            index = lowered.index(marker)
            network = parts[index + 1] if index + 1 < len(parts) else None
            market = parts[index + 2] if marker == "deployments" and index + 2 < len(parts) and "." not in parts[index + 2] else None
            return network, market
    return None, None


def _content_type_for_path(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return {
        ".json": "application/json",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".ts": "text/typescript",
        ".js": "text/javascript",
        ".sol": "text/plain",
        ".md": "text/markdown",
    }.get(suffix)


def _github_headers() -> dict:
    headers = {
        "User-Agent": "mqchain-ai",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = settings.github_api_token or os.getenv("MQCHAIN_GITHUB_API_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_failure_record(url: str, *, response=None, exception: Exception | None = None) -> dict:
    headers = getattr(response, "headers", {}) or {}
    record = {
        "url": url,
        "status_code": getattr(response, "status_code", None),
        "response_text": _response_text(response),
        "exception_type": type(exception).__name__ if exception else None,
        "exception_message": str(exception) if exception else None,
        "x_ratelimit_limit": headers.get("x-ratelimit-limit"),
        "x_ratelimit_remaining": headers.get("x-ratelimit-remaining"),
        "x_ratelimit_reset": headers.get("x-ratelimit-reset"),
    }
    return {key: value for key, value in record.items() if value not in {None, ""}}


def _response_text(response) -> str | None:
    if response is None:
        return None
    text = getattr(response, "text", None)
    if text is None:
        content = getattr(response, "content", b"")
        try:
            text = content.decode("utf-8", errors="replace")
        except AttributeError:
            text = str(content)
    return str(text)[:500] if text not in {None, ""} else None


def _warning_for_failure(*, response=None, exception: Exception | None = None) -> str:
    status_code = getattr(response, "status_code", None)
    headers = getattr(response, "headers", {}) or {}
    if status_code == 401:
        return "github_directory_auth_failed"
    if status_code in {403, 429} and headers.get("x-ratelimit-remaining") == "0":
        return "github_directory_rate_limited"
    if status_code == 404:
        return "github_directory_not_found"
    return "github_directory_fetch_failed"


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
