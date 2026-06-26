from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ingestion.intake_models import CandidatePreview, IntakeProfile, IntakePreview, SourceArtifact, SourceFingerprint
from app.ingestion.parser_router import ParserRouter
from app.ingestion.source_adapters import adapter_by_name, fetch_url_bytes
from app.ingestion.source_fingerprint import SourceFingerprintService
from app.models.intake import (
    AddressCandidate,
    AddressEvidence,
    CandidateContext,
    IntakePreview as IntakePreviewModel,
    SourceDocument,
    SourceJob,
    StagedArtifact,
)


class IntakeError(ValueError):
    def __init__(self, message: str, fatal_errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.fatal_errors = fatal_errors or [message]


class IntakeOrchestrator:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def preview_input(
        self,
        *,
        input_method: str,
        source_url: str | None = None,
        pasted_text: str | None = None,
        requested_source_type: str | None = None,
        content_type: str | None = None,
        created_by: str | None = None,
        source_evidence: dict | None = None,
    ) -> dict:
        normalized_evidence, evidence_warnings = _normalize_source_evidence(
            source_evidence,
            primary_source_url=source_url,
            filename=None,
        )
        raw_content = (pasted_text or "").encode("utf-8")
        final_url = source_url
        fetch_errors: list[str] = []
        warnings: list[str] = list(evidence_warnings)
        if source_url:
            try:
                raw_content, final_url, fetched_type = await fetch_url_bytes(source_url)
                content_type = fetched_type or content_type
                sanitized_final_url, final_url_warnings = _sanitize_source_url(final_url or source_url)
                normalized_evidence["source_url"] = sanitized_final_url
                warnings.extend(final_url_warnings)
            except Exception:
                if _is_github_structured_url(source_url):
                    warnings.append("github_prefetch_failed")
                else:
                    fetch_errors.append("source_url_unreachable")
                raw_content = b""
        stored_source_url = normalized_evidence.get("source_url") or final_url
        artifact = SourceArtifact(
            input_method=input_method,
            filename=Path(stored_source_url or "").name or None,
            source_url=stored_source_url,
            pasted_text=pasted_text,
            content_type=content_type,
            raw_content_sample=raw_content[:4096],
            size_bytes=len(raw_content),
            requested_source_type=requested_source_type,
            created_by=created_by,
            source_evidence=normalized_evidence,
        )
        preview = self._build_preview(
            artifact,
            raw_content,
            staged_artifact_id=None,
            extra_warnings=warnings,
            extra_fatal_errors=fetch_errors,
        )
        self._persist_preview(preview)
        return preview.to_response()

    def preview_upload(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None,
        requested_source_type: str | None = None,
        created_by: str | None = None,
        source_evidence: dict | None = None,
    ) -> dict:
        normalized_evidence, evidence_warnings = _normalize_source_evidence(
            source_evidence,
            primary_source_url=None,
            filename=filename,
        )
        staged = self._stage_artifact(
            filename=filename,
            content=content,
            content_type=content_type,
            created_by=created_by,
        )
        artifact = SourceArtifact(
            input_method="upload",
            filename=filename,
            local_file_path=staged.staged_path,
            source_url=normalized_evidence.get("source_url"),
            content_type=content_type,
            raw_content_sample=content[:4096],
            size_bytes=len(content),
            requested_source_type=requested_source_type,
            created_by=created_by,
            source_evidence=normalized_evidence,
        )
        preview = self._build_preview(artifact, content, staged_artifact_id=staged.id, extra_warnings=evidence_warnings)
        self._persist_preview(preview)
        self.db.commit()
        return preview.to_response()

    def save_job_from_preview(self, *, preview_id: str | None, staged_artifact_id: str | None, created_by: str | None = None) -> SourceJob:
        preview = self._find_preview(preview_id=preview_id, staged_artifact_id=staged_artifact_id)
        if preview.fatal_errors:
            raise IntakeError("preview_has_fatal_errors", list(preview.fatal_errors))
        if staged_artifact_id and preview.staged_artifact_id and staged_artifact_id != preview.staged_artifact_id:
            raise IntakeError("staged_artifact_preview_mismatch")

        response = dict(preview.preview_json)
        artifact = dict(preview.source_artifact_json)
        fingerprint = dict(preview.fingerprint_json)
        job = SourceJob(
            preview_id=preview.id,
            staged_artifact_id=preview.staged_artifact_id,
            input_method=str(artifact.get("input_method") or "unknown"),
            source_url=artifact.get("source_url"),
            pasted_text=artifact.get("pasted_text"),
            requested_source_type=fingerprint.get("requested_source_type"),
            final_source_type=str(fingerprint["final_source_type"]),
            adapter_name=str(fingerprint["parser_adapter"]),
            fingerprint_json=fingerprint,
            source_artifact_json=artifact,
            profile_json=dict(preview.profile_json),
            preview_json=response,
            status="new",
            created_by=created_by or artifact.get("created_by"),
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def save_upload_job(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None,
        requested_source_type: str | None = None,
        created_by: str | None = None,
        source_evidence: dict | None = None,
    ) -> SourceJob:
        preview = self.preview_upload(
            filename=filename,
            content=content,
            content_type=content_type,
            requested_source_type=requested_source_type,
            created_by=created_by,
            source_evidence=source_evidence,
        )
        return self.save_job_from_preview(
            preview_id=preview["preview_id"],
            staged_artifact_id=preview["staged_artifact_id"],
            created_by=created_by,
        )

    async def run_extraction(self, source_job_id: int) -> tuple[SourceJob, int, bool]:
        job = self.db.get(SourceJob, source_job_id)
        if job is None:
            raise IntakeError("source_job_not_found")
        if not job.final_source_type or not job.adapter_name:
            raise IntakeError("source_job_missing_saved_parser_result")
        existing_count = self._candidate_count_for_job(job.id)
        if existing_count:
            if job.status not in {"needs_review", "extracted"}:
                job.status = "needs_review"
                self.db.commit()
                self.db.refresh(job)
            return job, existing_count, True

        content = await self._content_for_job(job)
        artifact = self._artifact_from_job(job, content)
        fingerprint = SourceFingerprint(**job.fingerprint_json)
        if fingerprint.final_source_type != job.final_source_type or fingerprint.parser_adapter != job.adapter_name:
            raise IntakeError("source_job_fingerprint_snapshot_mismatch")
        ParserRouter.validate(fingerprint)
        adapter = adapter_by_name(job.adapter_name)

        job.status = "running"
        self.db.commit()
        try:
            parsed = adapter.parse(artifact, fingerprint, content)
            candidates = parsed.candidates[: settings.max_extraction_candidates_per_job]
            source_document = SourceDocument(
                source_job_id=job.id,
                canonical_source_url=artifact.source_url,
                file_path=artifact.local_file_path,
                content_type=parsed.content_type or artifact.content_type,
                document_title=parsed.document_title,
                text_hash=hashlib.sha256(parsed.document_text.encode("utf-8")).hexdigest(),
                metadata_json={
                    **parsed.metadata,
                    "final_source_type": job.final_source_type,
                    "adapter_name": job.adapter_name,
                    "preview_id": job.preview_id,
                    "staged_artifact_id": job.staged_artifact_id,
                    "source_evidence": (artifact.source_evidence or {}),
                    "source_origin": (artifact.source_evidence or {}).get("source_origin"),
                    "official_referrer_url": (artifact.source_evidence or {}).get("official_referrer_url"),
                    "provenance_type": (artifact.source_evidence or {}).get("provenance_type"),
                    "evidence_shape": (artifact.source_evidence or {}).get("evidence_shape"),
                    "operator_note": (artifact.source_evidence or {}).get("operator_note"),
                },
            )
            self.db.add(source_document)
            self.db.flush()
            created = self._store_candidates(job, source_document, candidates)
            job.status = "needs_review" if created else "extracted"
            self.db.commit()
            self.db.refresh(job)
            return job, created, False
        except Exception:
            self.db.rollback()
            failed = self.db.get(SourceJob, source_job_id)
            if failed is not None:
                failed.status = "failed"
                self.db.commit()
                self.db.refresh(failed)
                job = failed
            raise

    def candidates_for_job(self, source_job_id: int) -> list[AddressCandidate]:
        stmt = select(AddressCandidate).where(AddressCandidate.source_job_id == source_job_id).order_by(AddressCandidate.id.asc())
        return list(self.db.scalars(stmt))

    def _candidate_count_for_job(self, source_job_id: int) -> int:
        return len(self.candidates_for_job(source_job_id))

    def evidence_for_job(self, source_job_id: int) -> list[AddressEvidence]:
        stmt = (
            select(AddressEvidence)
            .join(AddressCandidate, AddressCandidate.id == AddressEvidence.candidate_id)
            .where(AddressCandidate.source_job_id == source_job_id)
            .order_by(AddressEvidence.id.asc())
        )
        return list(self.db.scalars(stmt))

    def documents_for_job(self, source_job_id: int) -> list[SourceDocument]:
        stmt = select(SourceDocument).where(SourceDocument.source_job_id == source_job_id).order_by(SourceDocument.id.asc())
        return list(self.db.scalars(stmt))

    def _build_preview(
        self,
        artifact: SourceArtifact,
        raw_content: bytes,
        *,
        staged_artifact_id: str | None,
        extra_warnings: list[str] | None = None,
        extra_fatal_errors: list[str] | None = None,
    ) -> IntakePreview:
        preview_id = str(uuid4())
        fingerprint = SourceFingerprintService.fingerprint(artifact, raw_content)
        fatal_errors = [*fingerprint.fatal_errors, *(extra_fatal_errors or [])]
        warnings = [*fingerprint.warnings, *(extra_warnings or [])]
        table_preview: list[dict] = []
        candidates: list[CandidatePreview] = []
        evidence_preview: list[dict] = []
        profile = IntakeProfile(
            final_source_type=fingerprint.final_source_type,
            adapter_name=fingerprint.parser_adapter,
            warnings=warnings,
            confidence=fingerprint.confidence,
            recommended_action="fix_fatal_errors" if fatal_errors else "run_extraction",
        )
        if not fatal_errors and fingerprint.parser_adapter:
            try:
                ParserRouter.validate(fingerprint)
                parsed = adapter_by_name(fingerprint.parser_adapter).parse(artifact, fingerprint, raw_content)
                warnings.extend(parsed.warnings)
                fatal_errors.extend(parsed.fatal_errors)
                table_preview = parsed.table_preview
                candidates = parsed.candidates[: settings.preview_candidate_limit]
                evidence_preview = parsed.evidence_preview
                profile = self._profile_from_parsed(fingerprint, parsed, candidates, warnings, fatal_errors)
            except Exception:
                fatal_errors.append("parser_cannot_read_source")
        profile = _profile_with_source_evidence(profile, artifact.source_evidence)
        return IntakePreview(
            preview_id=preview_id,
            staged_artifact_id=staged_artifact_id,
            artifact=artifact,
            fingerprint=fingerprint,
            profile=profile,
            table_preview=table_preview,
            candidates_preview=candidates,
            evidence_preview=evidence_preview,
            warnings=_dedupe(warnings),
            fatal_errors=_dedupe(fatal_errors),
            can_save_job=not fatal_errors,
            can_run_extraction=not fatal_errors,
        )

    def _persist_preview(self, preview: IntakePreview) -> None:
        response = preview.to_response()
        model = IntakePreviewModel(
            id=preview.preview_id,
            staged_artifact_id=preview.staged_artifact_id,
            source_artifact_json=preview.artifact.to_json(),
            fingerprint_json=preview.fingerprint.to_json(),
            profile_json=preview.profile.to_json(),
            preview_json=response,
            warnings=preview.warnings,
            fatal_errors=preview.fatal_errors,
        )
        self.db.add(model)
        self.db.commit()

    def _stage_artifact(self, *, filename: str, content: bytes, content_type: str | None, created_by: str | None) -> StagedArtifact:
        if not content:
            raise IntakeError("source_empty")
        if len(content) > settings.source_upload_max_bytes:
            raise IntakeError("uploaded_file_exceeds_configured_size_limit")
        safe_name = _safe_filename(filename or "source-upload")
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".pdf", ".csv", ".xlsx", ".xls", ".txt", ".md", ".json", ".yaml", ".yml"}:
            raise IntakeError(f"unsupported_upload_extension:{suffix or '(none)'}")
        settings.ensure_data_dirs()
        artifact_id = str(uuid4())
        target = Path(settings.staged_artifact_dir) / f"{artifact_id}-{safe_name}"
        target.write_bytes(content)
        staged = StagedArtifact(
            id=artifact_id,
            original_filename=safe_name,
            staged_path=str(target),
            content_type=content_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            created_by=created_by,
        )
        self.db.add(staged)
        self.db.flush()
        return staged

    def _find_preview(self, *, preview_id: str | None, staged_artifact_id: str | None) -> IntakePreviewModel:
        if preview_id:
            preview = self.db.get(IntakePreviewModel, preview_id)
            if preview is None:
                raise IntakeError("preview_not_found")
            return preview
        if staged_artifact_id:
            stmt = (
                select(IntakePreviewModel)
                .where(IntakePreviewModel.staged_artifact_id == staged_artifact_id)
                .order_by(IntakePreviewModel.created_at.desc())
            )
            preview = self.db.scalars(stmt).first()
            if preview is None:
                raise IntakeError("preview_not_found_for_staged_artifact")
            return preview
        raise IntakeError("preview_id_or_staged_artifact_id_required")

    async def _content_for_job(self, job: SourceJob) -> bytes:
        if job.staged_artifact_id:
            staged = self.db.get(StagedArtifact, job.staged_artifact_id)
            if staged is None:
                raise IntakeError("staged_artifact_not_found")
            path = Path(staged.staged_path)
            if not path.exists():
                raise IntakeError("staged_artifact_file_missing")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != staged.sha256:
                raise IntakeError("staged_artifact_hash_mismatch")
            return content
        if job.pasted_text is not None:
            return job.pasted_text.encode("utf-8")
        if job.source_url:
            content, _final_url, _content_type = await fetch_url_bytes(job.source_url)
            return content
        return b""

    def _artifact_from_job(self, job: SourceJob, content: bytes) -> SourceArtifact:
        data = dict(job.source_artifact_json)
        staged = self.db.get(StagedArtifact, job.staged_artifact_id) if job.staged_artifact_id else None
        return SourceArtifact(
            input_method=str(data.get("input_method") or job.input_method),
            filename=data.get("filename") or (staged.original_filename if staged else None),
            local_file_path=data.get("local_file_path") or (staged.staged_path if staged else None),
            source_url=data.get("source_url") or job.source_url,
            pasted_text=data.get("pasted_text") or job.pasted_text,
            content_type=data.get("content_type") or (staged.content_type if staged else None),
            raw_content_sample=content[:4096],
            size_bytes=len(content),
            requested_source_type=job.requested_source_type,
            created_by=data.get("created_by") or job.created_by,
            source_evidence=dict(data.get("source_evidence") or {}),
        )

    @staticmethod
    def _profile_from_parsed(
        fingerprint: SourceFingerprint,
        parsed,
        candidates: list[CandidatePreview],
        warnings: list[str],
        fatal_errors: list[str],
    ) -> IntakeProfile:
        metadata = parsed.metadata
        entity = metadata.get("entity_name") or next((candidate.entity_name for candidate in candidates if candidate.entity_name), None)
        chain_scope = _dedupe([candidate.chain_slug for candidate in candidates if candidate.chain_slug])
        expected_roles = _dedupe([*(metadata.get("expected_roles") or []), *[candidate.suggested_role for candidate in candidates if candidate.suggested_role]])
        return IntakeProfile(
            final_source_type=fingerprint.final_source_type,
            adapter_name=fingerprint.parser_adapter,
            entity_name=entity,
            protocol_name=entity,
            category=metadata.get("category") or ("cex" if fingerprint.final_source_type in {"excel_upload", "csv_upload", "por_pdf"} else "unknown"),
            sub_category=metadata.get("sub_category") or ("reserve_boundary" if fingerprint.final_source_type in {"excel_upload", "csv_upload", "por_pdf"} else None),
            expected_roles=expected_roles,
            chain_scope=chain_scope,
            detected_columns=metadata.get("detected_columns", []),
            sheet_count=int(metadata.get("sheet_count") or 0),
            parsed_sheet_names=list(metadata.get("parsed_sheet_names") or []),
            skipped_sheet_names=list(metadata.get("skipped_sheet_names") or []),
            table_count=int(metadata.get("table_count") or len(parsed.table_preview)),
            warnings=_dedupe(warnings),
            confidence=max(0, min(100, fingerprint.confidence + (10 if candidates else 0) - len(fatal_errors) * 25)),
            recommended_action="fix_fatal_errors" if fatal_errors else "run_extraction",
            metadata=metadata,
        )

    def _store_candidates(self, job: SourceJob, source_document: SourceDocument, candidates: list[CandidatePreview]) -> int:
        created = 0
        source_evidence = dict((job.source_artifact_json or {}).get("source_evidence") or {})
        for item in candidates:
            raw_reference = dict(item.raw_reference or {})
            if source_evidence and "source_evidence" not in raw_reference:
                raw_reference["source_evidence"] = source_evidence
            candidate = AddressCandidate(
                source_job_id=job.id,
                source_document_id=source_document.id,
                address=item.address,
                normalized_address=item.normalized_address,
                entity_name=item.entity_name,
                source_network=item.source_network,
                chain_guess=item.chain_guess,
                chain_slug=item.chain_slug,
                chain_id=item.chain_id,
                address_family=item.address_family,
                suggested_role=item.suggested_role,
                confidence_initial=item.confidence_initial,
                status="needs_review",
                source_type=item.source_type,
                source_input_type=item.source_input_type,
                source_sheet=item.source_sheet,
                source_row=item.source_row,
                source_page=item.source_page,
                source_url=item.source_url,
                file_path=item.file_path,
                evidence_type=item.evidence_type,
                warnings=item.warnings,
                raw_reference=raw_reference,
            )
            self.db.add(candidate)
            self.db.flush()
            self._save_candidate_context(candidate, source_document, item)
            self._save_candidate_evidence(candidate, source_document, item, job)
            created += 1
        return created

    def _save_candidate_context(
        self,
        candidate: AddressCandidate,
        source_document: SourceDocument,
        item: CandidatePreview,
    ) -> None:
        context = CandidateContext(
            candidate_id=candidate.id,
            source_document_id=source_document.id,
            sheet_name=item.source_sheet,
            row_number=item.source_row,
            page_number=item.source_page,
            table_name=item.raw_reference.get("table_name"),
            raw_row_json=item.raw_reference.get("raw_row_json") or item.raw_reference,
            original_value=item.raw_reference.get("original_value") or item.address,
            normalized_value=item.raw_reference.get("normalized_value") or item.normalized_address,
            parser_warnings=item.warnings,
        )
        self.db.add(context)
        self.db.flush()

    def _save_candidate_evidence(
        self,
        candidate: AddressCandidate,
        source_document: SourceDocument,
        item: CandidatePreview,
        job: SourceJob,
    ) -> None:
        source_evidence = (
            item.raw_reference.get("source_evidence")
            if isinstance(item.raw_reference.get("source_evidence"), dict)
            else dict((job.source_artifact_json or {}).get("source_evidence") or {})
        )
        evidence = AddressEvidence(
            candidate_id=candidate.id,
            source_document_id=source_document.id,
            evidence_type=item.evidence_type or "source_extraction_context",
            source_type=item.source_type,
            final_source_type=job.final_source_type,
            adapter_name=job.adapter_name,
            source_url=item.source_url,
            file_path=item.file_path,
            payload={
                "source_job_id": job.id,
                "source_document_id": source_document.id,
                "preview_id": job.preview_id,
                "staged_artifact_id": job.staged_artifact_id,
                "source_input_type": item.source_input_type,
                "sheet_name": item.source_sheet,
                "row_number": item.source_row,
                "page_number": item.source_page,
                "raw_reference": item.raw_reference,
                "source_evidence": source_evidence,
                "source_sheet": item.source_sheet,
                "sheet_entity_hint": item.raw_reference.get("sheet_entity_hint"),
                "sheet_source_url": item.raw_reference.get("sheet_source_url"),
                "sheet_source_origin": item.raw_reference.get("sheet_source_origin"),
                "sheet_official_referrer_url": item.raw_reference.get("sheet_official_referrer_url"),
                "sheet_provenance_type": item.raw_reference.get("sheet_provenance_type"),
                "sheet_evidence_shape": item.raw_reference.get("sheet_evidence_shape"),
                "sheet_snapshot_date": item.raw_reference.get("sheet_snapshot_date"),
                "sheet_operator_note": item.raw_reference.get("sheet_operator_note"),
                "parser_warnings": item.warnings,
            },
            confidence_reason="structured_network_column" if item.source_network else "address_pattern_fallback",
        )
        self.db.add(evidence)
        self.db.flush()


def _safe_filename(value: str) -> str:
    name = Path(value).name or "source-upload"
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip() or "source-upload"


SAFE_SOURCE_URL_QUERY_PARAMS = {"address", "chain", "id", "network", "page", "slug", "tab"}
SENSITIVE_SOURCE_URL_QUERY_PARAMS = {"api_key", "apikey", "access_key", "access_token", "auth", "key", "secret", "signature", "token"}
KNOWN_ENTITY_TOKENS = {
    "aave",
    "binance",
    "bitfinex",
    "bitget",
    "bitmex",
    "bybit",
    "coinbase",
    "coinex",
    "deribit",
    "huobi",
    "htx",
    "indodax",
    "kraken",
    "okx",
}
SOURCE_ORIGIN_ALIASES = {
    "cmc": "coinmarketcap",
    "coinmarketcap": "coinmarketcap",
}


def _normalize_source_evidence(source_evidence: dict | None, *, primary_source_url: str | None, filename: str | None) -> tuple[dict, list[str]]:
    evidence = {str(key): value for key, value in (source_evidence or {}).items() if value is not None and value != ""}
    if "operator_notes" in evidence and "operator_note" not in evidence:
        evidence["operator_note"] = evidence["operator_notes"]
    source_url = evidence.get("source_url") or primary_source_url
    warnings: list[str] = []
    if source_url:
        sanitized, url_warnings = _sanitize_source_url(str(source_url))
        evidence["source_url"] = sanitized
        warnings.extend(url_warnings)
    warnings.extend(_provenance_warnings(evidence, filename=filename))
    warnings = _dedupe(warnings)
    if warnings:
        evidence["provenance_warnings"] = warnings
    return evidence, warnings


def _sanitize_source_url(value: str) -> tuple[str, list[str]]:
    parsed = urlparse(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return value.strip(), []
    kept: list[tuple[str, str]] = []
    removed = False
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.strip().lower()
        if normalized_key in SAFE_SOURCE_URL_QUERY_PARAMS and normalized_key not in SENSITIVE_SOURCE_URL_QUERY_PARAMS:
            kept.append((key, item))
        elif normalized_key:
            removed = True
    sanitized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(kept), ""))
    return sanitized, ["source_url_query_params_removed"] if removed else []


def _provenance_warnings(evidence: dict, *, filename: str | None) -> list[str]:
    warnings: list[str] = []
    source_url = str(evidence.get("source_url") or "")
    entity_hint = str(evidence.get("entity_hint") or "")
    source_origin = str(evidence.get("source_origin") or "")
    entity_slug = _slug_token(entity_hint)
    origin_slug = _origin_slug(source_origin)
    host_root = _domain_root(source_url)

    filename_tokens = _text_tokens(filename)
    filename_entities = {token for token in filename_tokens if token in KNOWN_ENTITY_TOKENS}
    if entity_slug and filename_entities and entity_slug not in filename_entities:
        warnings.append("filename_entity_may_conflict_with_entity_hint")
    if source_origin and host_root and origin_slug and origin_slug not in _host_aliases(host_root):
        warnings.append("claimed_official_origin_does_not_match_source_url")
    if entity_slug and host_root in KNOWN_ENTITY_TOKENS and host_root != entity_slug and host_root != origin_slug:
        warnings.append("source_url_identity_may_conflict_with_entity_hint")
    return warnings


def _profile_with_source_evidence(profile: IntakeProfile, source_evidence: dict) -> IntakeProfile:
    metadata = {**(profile.metadata or {}), "source_evidence": source_evidence or {}}
    for key in ("source_url", "source_origin", "official_referrer_url", "provenance_type", "evidence_shape", "operator_note"):
        if key in (source_evidence or {}):
            metadata[key] = source_evidence[key]
    return replace(profile, metadata=metadata)


def _text_tokens(value: str | None) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", str(value or "").lower()) if token}


def _slug_token(value: str | None) -> str:
    return next((token for token in re.split(r"[^a-z0-9]+", str(value or "").lower()) if token), "")


def _origin_slug(value: str | None) -> str:
    slug = _slug_token(value)
    return SOURCE_ORIGIN_ALIASES.get(slug, slug)


def _domain_root(source_url: str | None) -> str:
    host = urlparse(source_url or "").netloc.lower().removeprefix("www.")
    parts = [part for part in host.split(".") if part]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")


def _host_aliases(host_root: str) -> set[str]:
    aliases = {host_root}
    for alias, canonical in SOURCE_ORIGIN_ALIASES.items():
        if canonical == host_root:
            aliases.add(alias)
    return aliases


def _is_github_structured_url(source_url: str | None) -> bool:
    if not source_url:
        return False
    parsed = urlparse(source_url)
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if host == "raw.githubusercontent.com":
        return len(parts) >= 4
    if host not in {"github.com", "www.github.com"}:
        return False
    return len(parts) >= 5 and parts[2] in {"blob", "tree"}


def _dedupe(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
