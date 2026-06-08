from __future__ import annotations

import json

from app.core.config import settings
from app.ingestion.extraction_pipeline import ExtractionPipeline
from app.ingestion.github_source_resolver import parse_github_tree_url
from app.ingestion.intake_models import SourceArtifact, SourceFingerprint
from app.ingestion.source_resolver import SourceResolver, _infer_network_market_from_path


TREE_URL = "https://github.com/compound-finance/comet/tree/main/deployments/base/usdc"


def _artifact() -> SourceArtifact:
    return SourceArtifact(
        input_method="github",
        filename="usdc",
        source_url=TREE_URL,
        content_type="text/html",
        raw_content_sample=b"",
        size_bytes=0,
    )


def _fingerprint() -> SourceFingerprint:
    return SourceFingerprint(
        file_extension=None,
        magic_signature=None,
        mime_type="text/html",
        url_kind="github_directory",
        content_kind="html",
        detected_source_type="github_directory",
        final_source_type="github_directory",
        parser_adapter="github_adapter",
        confidence=80,
    )


def test_github_tree_parser_extracts_owner_repo_branch_path() -> None:
    parsed = parse_github_tree_url(TREE_URL)

    assert parsed is not None
    assert parsed.owner == "compound-finance"
    assert parsed.repo == "comet"
    assert parsed.branch == "main"
    assert parsed.path == "deployments/base/usdc"
    assert parsed.original_url == TREE_URL


def test_github_directory_resolver_fetches_supported_files_and_skips_unsupported(monkeypatch) -> None:
    responses = _github_responses(
        {
            "configuration.json": b'{"comet":"0x1111111111111111111111111111111111111111"}',
            "roots.json": b'{"configurator":"0x2222222222222222222222222222222222222222"}',
            "relations.ts": b'export const rewards = "0x3333333333333333333333333333333333333333";',
            "README.md": b"| Contract | Address |\n|---|---|\n| Bulker | 0x4444444444444444444444444444444444444444 |",
            "ignored.png": b"png",
        }
    )
    monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

    resolved = SourceResolver().resolve(_artifact(), _fingerprint(), b"<html>github</html>")

    assert resolved.fatal_errors == []
    assert "github_directory_unsupported_file_skipped" in resolved.warnings
    assert [document.source_file_path for document in resolved.documents] == [
        "deployments/base/usdc/configuration.json",
        "deployments/base/usdc/roots.json",
        "deployments/base/usdc/relations.ts",
        "deployments/base/usdc/README.md",
    ]
    assert all(document.source_url == TREE_URL for document in resolved.documents)
    assert resolved.documents[0].metadata["owner"] == "compound-finance"
    assert resolved.documents[0].metadata["inferred_network"] == "Base"
    assert resolved.documents[0].metadata["inferred_market"] == "USDC"


def test_compound_directory_json_extraction_preserves_market_roles_and_json_path(monkeypatch) -> None:
    responses = _github_responses(
        {
            "configuration.json": json.dumps(
                {
                    "comet": "0x1111111111111111111111111111111111111111",
                    "configurator": "0x2222222222222222222222222222222222222222",
                    "rewards": "0x3333333333333333333333333333333333333333",
                }
            ).encode(),
        }
    )
    monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

    result = ExtractionPipeline().run(_artifact(), _fingerprint(), b"<html>github</html>")

    assert len(result.candidates_preview) == 3
    by_contract = {candidate.raw_reference["contract_name"]: candidate for candidate in result.candidates_preview}
    assert by_contract["comet"].suggested_role == "lending_market"
    assert by_contract["configurator"].suggested_role == "protocol_configurator"
    assert by_contract["rewards"].suggested_role == "rewards_contract"
    for candidate in result.candidates_preview:
        assert candidate.entity_name == "Compound"
        assert candidate.source_network == "Base"
        assert candidate.evidence_type == "official_github_deployment"
        assert candidate.file_path == "deployments/base/usdc/configuration.json"
        assert candidate.raw_reference["market"] == "USDC"
        assert candidate.raw_reference["github_owner"] == "compound-finance"
        assert candidate.raw_reference["github_repo"] == "comet"
        assert candidate.raw_reference["github_branch"] == "main"
        assert candidate.raw_reference["github_directory_path"] == "deployments/base/usdc"
        assert candidate.raw_reference["json_path"]


def test_compound_directory_typescript_extraction_preserves_line_numbers(monkeypatch) -> None:
    responses = _github_responses(
        {
            "relations.ts": b"""
export const bulker = "0x4444444444444444444444444444444444444444";
export const bridgeReceiver = "0x5555555555555555555555555555555555555555";
""",
        }
    )
    monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

    result = ExtractionPipeline().run(_artifact(), _fingerprint(), b"<html>github</html>")

    by_contract = {candidate.raw_reference["contract_name"]: candidate for candidate in result.candidates_preview}
    assert by_contract["bulker"].suggested_role == "helper_contract"
    assert by_contract["bridgeReceiver"].suggested_role == "bridge_receiver"
    assert by_contract["bulker"].source_row == 2
    assert by_contract["bridgeReceiver"].source_row == 3


def test_compound_path_inference() -> None:
    assert _infer_network_market_from_path("deployments/base/usdc") == ("Base", "USDC")
    assert _infer_network_market_from_path("deployments/mainnet/usdc") == ("Ethereum", "USDC")
    assert _infer_network_market_from_path("deployments/arbitrum/usdc") == ("Arbitrum", "USDC")


def test_github_directory_crawler_safety_warnings(monkeypatch) -> None:
    old_depth = settings.github_crawl_max_depth
    old_files = settings.github_crawl_max_files
    old_bytes = settings.github_crawl_max_bytes_per_file
    settings.github_crawl_max_depth = 0
    settings.github_crawl_max_files = 1
    settings.github_crawl_max_bytes_per_file = 10
    try:
        root_api = "https://api.github.com/repos/compound-finance/comet/contents/deployments/base/usdc?ref=main"
        responses = {
            root_api: _FakeResponse(
                json_data=[
                    _directory_item("migrations"),
                    _file_item("configuration.json", size=64),
                    _file_item("ignored.png", size=3),
                    _file_item("roots.json", size=2),
                ]
            ),
            "download:deployments/base/usdc/roots.json": _FakeResponse(content=b"{}"),
        }
        monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

        resolved = SourceResolver().resolve(_artifact(), _fingerprint(), b"<html>github</html>")

        assert "github_directory_depth_limit_reached" in resolved.warnings
        assert "github_directory_unsupported_file_skipped" in resolved.warnings
        assert "github_directory_file_limit_reached" in resolved.warnings
        assert len(resolved.documents) == 1
    finally:
        settings.github_crawl_max_depth = old_depth
        settings.github_crawl_max_files = old_files
        settings.github_crawl_max_bytes_per_file = old_bytes


def test_github_directory_no_supported_files_warns_without_crashing(monkeypatch) -> None:
    root_api = "https://api.github.com/repos/compound-finance/comet/contents/deployments/base/usdc?ref=main"
    responses = {root_api: _FakeResponse(json_data=[_file_item("ignored.png", size=3)])}
    monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

    resolved = SourceResolver().resolve(_artifact(), _fingerprint(), b"<html>github</html>")

    assert resolved.documents == []
    assert "github_directory_no_supported_files" in resolved.warnings
    assert "github_directory_unsupported_file_skipped" in resolved.warnings


class _FakeClient:
    def __init__(self, responses: dict[str, "_FakeResponse"]) -> None:
        self.responses = responses

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str):
        key = url if url in self.responses else url.replace("https://raw.example/", "download:")
        response = self.responses[key]
        response.url = url
        return response


class _FakeResponse:
    def __init__(self, *, json_data=None, content: bytes = b"") -> None:
        self._json_data = json_data
        self.content = content
        self.url = ""

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json_data


def _github_responses(files: dict[str, bytes]) -> dict[str, _FakeResponse]:
    root_api = "https://api.github.com/repos/compound-finance/comet/contents/deployments/base/usdc?ref=main"
    responses: dict[str, _FakeResponse] = {
        root_api: _FakeResponse(json_data=[_file_item(name, size=len(content)) for name, content in files.items()])
    }
    for name, content in files.items():
        path = f"deployments/base/usdc/{name}"
        responses[f"download:{path}"] = _FakeResponse(content=content)
    return responses


def _file_item(name: str, *, size: int) -> dict:
    path = f"deployments/base/usdc/{name}"
    return {
        "type": "file",
        "name": name,
        "path": path,
        "size": size,
        "url": f"https://api.github.com/repos/compound-finance/comet/contents/{path}?ref=main",
        "download_url": f"https://raw.example/{path}",
    }


def _directory_item(name: str) -> dict:
    path = f"deployments/base/usdc/{name}"
    return {
        "type": "dir",
        "name": name,
        "path": path,
        "url": f"https://api.github.com/repos/compound-finance/comet/contents/{path}?ref=main",
    }
