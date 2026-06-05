from __future__ import annotations

from app.ingestion.intake_models import SourceFingerprint


class ParserRoutingError(ValueError):
    pass


class ParserRouter:
    ROUTES: dict[str, str] = {
        "excel_upload": "excel_csv_adapter",
        "csv_upload": "excel_csv_adapter",
        "pdf_upload": "pdf_adapter",
        "pdf_url": "pdf_adapter",
        "por_pdf": "pdf_adapter",
        "audit_report": "pdf_adapter",
        "official_github": "github_adapter",
        "github_blob": "github_adapter",
        "github_raw": "github_adapter",
        "github_directory": "github_adapter",
        "json": "json_yaml_adapter",
        "yaml": "json_yaml_adapter",
        "deployment_json": "json_yaml_adapter",
        "plain_text": "plain_text_adapter",
        "markdown": "plain_text_adapter",
        "manual_seed": "plain_text_adapter",
        "onchain_root": "plain_text_adapter",
    }

    @classmethod
    def adapter_name_for(cls, final_source_type: str | None, *, magic_signature: str | None = None) -> str | None:
        if not final_source_type:
            return None
        adapter = cls.ROUTES.get(final_source_type)
        if magic_signature == "xlsx_zip" and adapter == "pdf_adapter":
            return "excel_csv_adapter"
        if magic_signature == "pdf" and adapter == "excel_csv_adapter":
            return "pdf_adapter"
        return adapter

    @classmethod
    def validate(cls, fingerprint: SourceFingerprint) -> None:
        if not fingerprint.final_source_type or not fingerprint.parser_adapter:
            raise ParserRoutingError("unsupported_or_unroutable_source_type")
        if fingerprint.magic_signature == "xlsx_zip" and fingerprint.parser_adapter == "pdf_adapter":
            raise ParserRoutingError("xlsx_routed_to_pdf_adapter_blocked")
        if fingerprint.magic_signature == "pdf" and fingerprint.parser_adapter == "excel_csv_adapter":
            raise ParserRoutingError("pdf_routed_to_excel_csv_adapter_blocked")
