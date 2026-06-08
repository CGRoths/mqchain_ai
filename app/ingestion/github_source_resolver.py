from __future__ import annotations

import base64
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


class _GitHubDirectoryCrawler:
    def __init__(self, tree: GitHubTreeUrl) -> None:
        self.tree = tree
        self.files: list[GitHubFetchedFile] = []
        self.warnings: list[str] = []
        self.api_urls: list[str] = []
        self._file_limit_reached = False
        self._depth_limit_reached = False

    def resolve(self) -> GitHubDirectoryResult:
        with httpx.Client(timeout=settings.source_fetch_timeout_seconds, follow_redirects=True) as client:
            self._crawl(client, self.tree.path, depth=0)
        if not self.files:
            self.warnings.append("github_directory_no_supported_files")
        return GitHubDirectoryResult(
            tree=self.tree,
            files=self.files,
            warnings=_dedupe(self.warnings),
            api_urls=_dedupe(self.api_urls),
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
            response = client.get(api_url)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            self.warnings.append("github_directory_fetch_failed")
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

    def _fetch_file_item(self, client: httpx.Client, item: dict, *, depth: int) -> None:
        item_path = str(item.get("path") or "")
        name = str(item.get("name") or Path(item_path).name)
        if not self._allow_file(item_path):
            self.warnings.append("github_directory_unsupported_file_skipped")
            return
        size = int(item.get("size") or 0)
        if size > settings.github_crawl_max_bytes_per_file:
            self.warnings.append("github_directory_unsupported_file_skipped")
            return
        content = self._content_from_item(client, item)
        if content is None:
            self.warnings.append("github_directory_fetch_failed")
            return
        if len(content) > settings.github_crawl_max_bytes_per_file:
            self.warnings.append("github_directory_unsupported_file_skipped")
            return
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
        if not download_url:
            return None
        response = client.get(str(download_url))
        response.raise_for_status()
        return response.content

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


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
