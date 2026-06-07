from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.ingestion.extraction_models import SourceDocument
from app.ingestion.github_source_resolver import github_blob_to_raw_url, resolve_github_source
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

        if fingerprint.final_source_type == "github_directory":
            warnings.append("github_directory_crawler_not_enabled")

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
