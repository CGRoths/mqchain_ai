from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.ingestion.extraction_models import SourceDocument
from app.ingestion.github_source_resolver import github_blob_to_raw_url, resolve_github_directory, resolve_github_source
from app.ingestion.intake_models import SourceArtifact, SourceFingerprint


@dataclass
class ResolvedSourceSet:
    documents: list[SourceDocument] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fatal_errors: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class SourceResolver:
    def resolve(
        self,
        artifact: SourceArtifact,
        fingerprint: SourceFingerprint,
        raw_content: bytes,
    ) -> ResolvedSourceSet:
        warnings: list[str] = []
        final_url = artifact.source_url
        text = _decode(raw_content, fallback=artifact.pasted_text or "")
        content = raw_content

        if fingerprint.final_source_type == "github_directory" and not _github_directory_raw_file_input(artifact.source_url, text):
            directory = resolve_github_directory(artifact.source_url)
            if directory is not None:
                return _resolved_github_directory(artifact, fingerprint, directory)
            warnings.append("github_directory_fetch_failed")

        if fingerprint.final_source_type in {"github_blob", "github_raw", "official_github", "github_directory"}:
            resolved_text, resolved_url = resolve_github_source(artifact.source_url, raw_content)
            if resolved_text:
                text = resolved_text
                content = resolved_text.encode("utf-8")
            final_url = resolved_url or final_url
        elif artifact.source_url and github_blob_to_raw_url(artifact.source_url):
            final_url = github_blob_to_raw_url(artifact.source_url)

        if not text and content:
            text = _decode(content)
        if not text and artifact.source_url:
            text = f"Source: {artifact.source_url}"

        content_hash = hashlib.sha256(content or text.encode("utf-8")).hexdigest()
        source_file_path = _source_file_path(final_url, artifact.local_file_path)
        document = SourceDocument(
            source_document_key=_source_document_key(final_url, source_file_path, content_hash),
            document_id=None,
            source_url=final_url,
            source_file_path=source_file_path,
            filename=artifact.filename or (Path(source_file_path).name if source_file_path else None),
            content_type=artifact.content_type,
            final_source_type=fingerprint.final_source_type,
            adapter_name=fingerprint.parser_adapter,
            text=text,
            raw_bytes=content,
            content_hash=content_hash,
            metadata={
                "input_method": artifact.input_method,
                "requested_source_type": artifact.requested_source_type,
                "final_source_type": fingerprint.final_source_type,
                "adapter_name": fingerprint.parser_adapter,
                "source_evidence": artifact.source_evidence or {},
            },
        )
        return ResolvedSourceSet(
            documents=[document],
            warnings=warnings,
            metadata={
                "resolver_name": "single_document_resolver",
                "document_count": 1,
                "resolved_source_url": final_url,
                "source_file_path": source_file_path,
            },
        )


def _decode(content: bytes, fallback: str = "") -> str:
    if not content:
        return fallback
    return content.decode("utf-8-sig", errors="replace")


def _source_file_path(source_url: str | None, local_file_path: str | None) -> str | None:
    if local_file_path:
        return local_file_path
    if not source_url:
        return None
    parsed = urlparse(source_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    host = parsed.netloc.lower()
    if host == "raw.githubusercontent.com" and len(parts) >= 4:
        return "/".join(parts[3:])
    if host in {"github.com", "www.github.com"} and len(parts) >= 5 and parts[2] in {"blob", "tree"}:
        return "/".join(parts[4:])
    return parsed.path.strip("/") or None


def _source_document_key(source_url: str | None, source_file_path: str | None, content_hash: str) -> str:
    if source_url or source_file_path:
        return f"{source_url or ''}#{source_file_path or ''}"
    return f"content:{content_hash}"


def _resolved_github_directory(artifact: SourceArtifact, fingerprint: SourceFingerprint, directory) -> ResolvedSourceSet:
    documents: list[SourceDocument] = []
    directory_network, directory_market = _infer_network_market_from_path(directory.tree.path)
    crawler_metadata = dict(getattr(directory, "metadata", {}) or {})
    for fetched in directory.files:
        content_hash = hashlib.sha256(fetched.content).hexdigest()
        inferred_network, inferred_market = _infer_network_market_from_path(fetched.path)
        document = SourceDocument(
            source_document_key=_github_directory_document_key(
                directory.tree.owner,
                directory.tree.repo,
                directory.tree.branch,
                fetched.path,
                content_hash,
            ),
            document_id=None,
            source_url=directory.tree.original_url,
            source_file_path=fetched.path,
            filename=fetched.name,
            content_type=fetched.content_type,
            final_source_type=fingerprint.final_source_type,
            adapter_name=fingerprint.parser_adapter,
            text=_decode(fetched.content),
            raw_bytes=fetched.content,
            content_hash=content_hash,
            metadata={
                "input_method": artifact.input_method,
                "requested_source_type": artifact.requested_source_type,
                "final_source_type": fingerprint.final_source_type,
                "adapter_name": fingerprint.parser_adapter,
                "owner": directory.tree.owner,
                "repo": directory.tree.repo,
                "branch": directory.tree.branch,
                "directory_path": directory.tree.path,
                "original_tree_url": directory.tree.original_url,
                "github_api_url": fetched.api_url,
                "crawler_depth": fetched.depth,
                "inferred_network": inferred_network,
                "inferred_market": inferred_market,
                "root_deployment_scan_mode": crawler_metadata.get("root_deployment_scan_mode", False),
                "source_evidence": artifact.source_evidence or {},
            },
        )
        documents.append(document)
    return ResolvedSourceSet(
        documents=documents,
        warnings=list(directory.warnings),
        metadata={
            "resolver_name": "github_directory_resolver",
            "document_count": len(documents),
            "resolved_source_url": directory.tree.original_url,
            "source_file_path": directory.tree.path,
            "github_owner": directory.tree.owner,
            "github_repo": directory.tree.repo,
            "github_branch": directory.tree.branch,
            "github_directory_path": directory.tree.path,
            "github_api_urls": directory.api_urls,
            "inferred_network": directory_network,
            "inferred_market": directory_market,
            **crawler_metadata,
        },
    )


def _github_directory_document_key(owner: str, repo: str, branch: str, path: str, content_hash: str) -> str:
    return f"github:{owner}/{repo}@{branch}:{path}#{content_hash}"


def _infer_network_market_from_path(path: str | None) -> tuple[str | None, str | None]:
    if not path:
        return None, None
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    lowered = [part.lower() for part in parts]
    for marker in ("deployments", "addresses", "networks", "chains"):
        if marker in lowered:
            index = lowered.index(marker)
            network = _network_label(parts[index + 1]) if index + 1 < len(parts) else None
            market = _market_label(parts[index + 2]) if marker == "deployments" and index + 2 < len(parts) and "." not in parts[index + 2] else None
            return network, market
    return None, None


def _network_label(value: str | None) -> str | None:
    if not value:
        return None
    aliases = {
        "base": "Base",
        "mainnet": "Ethereum",
        "ethereum": "Ethereum",
        "arbitrum": "Arbitrum",
        "polygon": "Polygon",
        "optimism": "Optimism",
    }
    key = value.strip().lower()
    return aliases.get(key) or _title_token(value)


def _market_label(value: str | None) -> str | None:
    if not value:
        return None
    token = Path(value).stem
    return token.upper() if re.fullmatch(r"[A-Za-z0-9]{2,12}", token) else _title_token(token)


def _title_token(value: str) -> str:
    return re.sub(r"[-_]+", " ", value).strip().title()


def _github_directory_raw_file_input(source_url: str | None, text: str) -> bool:
    if not source_url:
        return False
    path = urlparse(source_url).path.lower()
    if not Path(path).suffix.lower() in {".json", ".yaml", ".yml", ".ts", ".js", ".sol", ".md"}:
        return False
    sample = text[:4096].lower()
    return bool(text.strip()) and not (("<html" in sample or "<!doctype html" in sample) and "github" in sample)
