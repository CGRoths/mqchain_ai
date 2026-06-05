from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from app.ingestion.intake_models import SourceArtifact, SourceFingerprint
from app.ingestion.parser_router import ParserRouter


SOURCE_TYPES = {
    "official_website",
    "official_docs",
    "official_github",
    "github_blob",
    "github_raw",
    "github_directory",
    "por_pdf",
    "audit_report",
    "pdf_url",
    "pdf_upload",
    "excel_upload",
    "csv_upload",
    "plain_text",
    "markdown",
    "json",
    "yaml",
    "deployment_json",
    "manual_seed",
    "onchain_root",
}


class SourceFingerprintService:
    @classmethod
    def fingerprint(cls, artifact: SourceArtifact, raw_content: bytes | None = None) -> SourceFingerprint:
        content = raw_content if raw_content is not None else artifact.raw_content_sample or b""
        warnings: list[str] = []
        fatal_errors: list[str] = []
        extension = Path(artifact.filename or artifact.local_file_path or urlparse(artifact.source_url or "").path).suffix.lower() or None
        magic_signature = cls._magic_signature(content, extension)
        mime_type = (artifact.content_type or "").split(";")[0].strip().lower() or None
        url_kind = cls._url_kind(artifact.source_url)
        content_kind = cls._content_kind(content, artifact.pasted_text, mime_type)

        if artifact.input_method in {"upload", "paste", "onchain_root"} and not content and not artifact.pasted_text:
            fatal_errors.append("source_empty")

        detected, confidence = cls._detected_source_type(
            magic_signature=magic_signature,
            extension=extension,
            mime_type=mime_type,
            url_kind=url_kind,
            content_kind=content_kind,
            source_url=artifact.source_url,
            pasted_text=artifact.pasted_text,
            requested=artifact.requested_source_type,
        )
        requested = cls._normalize_source_type(artifact.requested_source_type)
        if artifact.requested_source_type and requested is None:
            warnings.append("requested_source_type_unrecognized")

        final_source_type = detected or requested
        overridden = bool(requested and detected and requested != detected)
        override_reason = "source_type_overridden_by_artifact_fingerprint" if overridden else None
        if overridden:
            warnings.append(override_reason)

        adapter = ParserRouter.adapter_name_for(final_source_type, magic_signature=magic_signature)
        if final_source_type and not adapter:
            fatal_errors.append("unsupported_source_type")
        if magic_signature == "legacy_xls":
            fatal_errors.append("legacy_xls_not_supported_use_xlsx")
        if magic_signature == "xlsx_zip" and adapter == "pdf_adapter":
            fatal_errors.append("xlsx_routed_to_pdf_adapter_blocked")
        if magic_signature == "pdf" and adapter == "excel_csv_adapter":
            fatal_errors.append("pdf_routed_to_excel_csv_adapter_blocked")

        return SourceFingerprint(
            file_extension=extension,
            magic_signature=magic_signature,
            mime_type=mime_type,
            url_kind=url_kind,
            content_kind=content_kind,
            detected_source_type=detected,
            final_source_type=final_source_type,
            parser_adapter=adapter,
            confidence=confidence if final_source_type else 0,
            warnings=warnings,
            fatal_errors=fatal_errors,
            requested_source_type=requested,
            source_type_overridden=overridden,
            override_reason=override_reason,
        )

    @staticmethod
    def _magic_signature(content: bytes, extension: str | None) -> str | None:
        if content.startswith(b"%PDF"):
            return "pdf"
        if content.startswith(b"\xd0\xcf\x11\xe0") and extension == ".xls":
            return "legacy_xls"
        if content.startswith(b"PK"):
            if extension == ".xlsx":
                return "xlsx_zip"
            try:
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    if "xl/workbook.xml" in archive.namelist():
                        return "xlsx_zip"
            except zipfile.BadZipFile:
                return "zip"
            return "zip"
        return None

    @staticmethod
    def _url_kind(source_url: str | None) -> str | None:
        if not source_url:
            return None
        parsed = urlparse(source_url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        if host == "raw.githubusercontent.com":
            return "github_raw"
        if host in {"github.com", "www.github.com"}:
            if "/blob/" in path:
                return "github_blob"
            if "/tree/" in path:
                return "github_directory"
            return "official_github"
        if host.startswith(("docs.", "developers.")) or ".docs." in host or ".developers." in host:
            return "official_docs"
        if source_url.startswith(("http://", "https://")):
            return "generic_url"
        return None

    @staticmethod
    def _content_kind(content: bytes, pasted_text: str | None, mime_type: str | None) -> str | None:
        text = pasted_text if pasted_text is not None else content[:4096].decode("utf-8", errors="ignore")
        stripped = text.strip()
        if mime_type == "text/csv":
            return "csv"
        if mime_type == "text/html" or re.match(r"(?is)^<!doctype\s+html|^<html\b", stripped):
            return "html"
        if stripped.startswith(("{", "[")):
            try:
                json.loads(stripped)
                return "json"
            except json.JSONDecodeError:
                pass
        if re.search(r"^[A-Za-z0-9_.-]+\s*:\s*", stripped, flags=re.MULTILINE):
            return "yaml"
        if re.search(r"^\s*\|.+\|\s*$\n^\s*\|[\s|:-]+\|\s*$", stripped, flags=re.MULTILINE):
            return "markdown"
        if stripped:
            return "plain_text"
        return None

    @classmethod
    def _detected_source_type(
        cls,
        *,
        magic_signature: str | None,
        extension: str | None,
        mime_type: str | None,
        url_kind: str | None,
        content_kind: str | None,
        source_url: str | None,
        pasted_text: str | None,
        requested: str | None,
    ) -> tuple[str | None, int]:
        haystack = f"{source_url or ''} {pasted_text or ''}".lower()
        if magic_signature == "pdf":
            return ("por_pdf" if cls._has_por_hint(haystack) else "pdf_upload"), 98
        if magic_signature == "xlsx_zip":
            return "excel_upload", 98
        if magic_signature == "legacy_xls":
            return "excel_upload", 80
        if extension in {".xlsx", ".xls"}:
            return "excel_upload", 90
        if extension == ".csv":
            return "csv_upload", 90
        if extension == ".pdf":
            return ("por_pdf" if cls._has_por_hint(haystack) else "pdf_upload"), 90
        if extension == ".json":
            return ("deployment_json" if cls._has_deployment_hint(haystack) else "json"), 88
        if extension in {".yaml", ".yml"}:
            return "yaml", 88
        if extension == ".md":
            return "markdown", 86
        if extension == ".txt":
            return "plain_text", 84
        if mime_type == "text/csv":
            return "csv_upload", 82
        if mime_type == "application/pdf":
            return "pdf_upload", 82
        if url_kind in {"github_raw", "github_blob", "github_directory", "official_github", "official_docs"}:
            return url_kind, 80
        if url_kind == "generic_url":
            if source_url and urlparse(source_url).path.lower().endswith(".pdf"):
                return "pdf_url", 78
            return "official_website", 60
        if content_kind in {"json", "yaml", "markdown", "plain_text"}:
            if content_kind == "json":
                return "deployment_json" if cls._has_deployment_hint(haystack) else "json", 75
            return content_kind, 72
        normalized_requested = cls._normalize_source_type(requested)
        return (normalized_requested, 45) if normalized_requested else (None, 0)

    @staticmethod
    def _normalize_source_type(value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip().lower().replace("-", "_")
        return normalized if normalized in SOURCE_TYPES else None

    @staticmethod
    def _has_por_hint(value: str) -> bool:
        return any(token in value for token in {"por", "proof", "reserve", "reserves", "audit", "audited"})

    @staticmethod
    def _has_deployment_hint(value: str) -> bool:
        return any(token in value for token in {"deployment", "deployments", "deployed", "address-book", "address_book"})
