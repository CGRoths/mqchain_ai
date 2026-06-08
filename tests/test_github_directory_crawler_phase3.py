from __future__ import annotations

import json

from app.core.config import settings
from app.ingestion.extraction_pipeline import ExtractionPipeline
from app.ingestion.github_source_resolver import _github_headers, parse_github_tree_url
from app.ingestion.intake_models import SourceArtifact, SourceFingerprint
from app.ingestion.source_resolver import SourceResolver, _infer_network_market_from_path


TREE_URL = "https://github.com/compound-finance/comet/tree/main/deployments/base/usdc"
ROOT_TREE_URL = "https://github.com/compound-finance/comet/tree/main/deployments"


def _artifact() -> SourceArtifact:
    return SourceArtifact(
        input_method="github",
        filename="usdc",
        source_url=TREE_URL,
        content_type="text/html",
        raw_content_sample=b"",
        size_bytes=0,
    )


def _root_artifact() -> SourceArtifact:
    return SourceArtifact(
        input_method="github",
        filename="deployments",
        source_url=ROOT_TREE_URL,
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
    fake_client = _FakeClient(responses)
    monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: fake_client)

    resolved = SourceResolver().resolve(_artifact(), _fingerprint(), b"<html>github</html>")

    assert fake_client.requests
    assert all(request["headers"]["User-Agent"] == "mqchain-ai" for request in fake_client.requests)
    assert all(request["headers"]["Accept"] == "application/vnd.github+json" for request in fake_client.requests)
    assert all(request["headers"]["X-GitHub-Api-Version"] == "2022-11-28" for request in fake_client.requests)
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


def test_github_headers_include_optional_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "github_api_token", None)
    monkeypatch.setenv("MQCHAIN_GITHUB_API_TOKEN", "test-token")

    headers = _github_headers()

    assert headers["Authorization"] == "Bearer test-token"


def test_github_directory_rate_limit_failure_records_diagnostics(monkeypatch) -> None:
    root_api = "https://api.github.com/repos/compound-finance/comet/contents/deployments?ref=main"
    responses = {
        root_api: _FakeResponse(
            status_code=403,
            text="rate limit exceeded",
            headers={"x-ratelimit-limit": "60", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "123"},
        )
    }
    monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

    result = ExtractionPipeline().run(_root_artifact(), _fingerprint(), b"<html>github</html>")

    assert "github_directory_rate_limited" in result.warnings
    failure = result.metadata["fetch_failures"][0]
    assert failure["status_code"] == 403
    assert failure["response_text"] == "rate limit exceeded"
    assert failure["x_ratelimit_remaining"] == "0"
    assert result.metadata["github_rate_limit_remaining"] == "0"


def test_github_directory_not_found_failure_records_diagnostics(monkeypatch) -> None:
    root_api = "https://api.github.com/repos/compound-finance/comet/contents/deployments?ref=main"
    responses = {root_api: _FakeResponse(status_code=404, text="not found")}
    monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

    result = ExtractionPipeline().run(_root_artifact(), _fingerprint(), b"<html>github</html>")

    assert "github_directory_not_found" in result.warnings
    assert result.metadata["fetch_failures"][0]["status_code"] == 404


def test_github_directory_network_exception_records_diagnostics(monkeypatch) -> None:
    root_api = "https://api.github.com/repos/compound-finance/comet/contents/deployments?ref=main"
    responses = {root_api: _FakeResponse(error=True)}
    monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

    result = ExtractionPipeline().run(_root_artifact(), _fingerprint(), b"<html>github</html>")

    assert "github_directory_fetch_failed" in result.warnings
    failure = result.metadata["fetch_failures"][0]
    assert failure["exception_type"] == "RuntimeError"
    assert "fake fetch failure" in failure["exception_message"]


def test_root_deployment_scan_fetches_priority_files_across_networks(monkeypatch) -> None:
    responses = _root_github_responses(
        {
            "arbitrum": ["usdc", "weth"],
            "base": ["usdc"],
            "mainnet": ["usdc"],
            "optimism": ["usdc"],
            "polygon": ["usdc"],
        }
    )
    monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

    result = ExtractionPipeline().run(_root_artifact(), _fingerprint(), b"<html>github</html>")

    paths = {document.source_file_path for document in result.source_documents}
    assert "deployments/arbitrum/usdc/configuration.json" in paths
    assert "deployments/arbitrum/weth/configuration.json" in paths
    assert "deployments/base/usdc/configuration.json" in paths
    assert "deployments/mainnet/usdc/configuration.json" in paths
    assert "deployments/optimism/usdc/configuration.json" in paths
    assert "deployments/polygon/usdc/configuration.json" in paths
    assert {row.network for row in result.normalized_rows} >= {"Arbitrum", "Base", "Ethereum", "Optimism", "Polygon"}
    assert result.metadata["root_deployment_scan_mode"] is True
    assert result.metadata["discovered_network_count"] == 5
    assert result.metadata["discovered_market_count"] == 6
    assert result.metadata["fetched_file_count_by_network"]["base"] == 1
    assert result.metadata["fetched_file_count_by_market"]["base/usdc"] == 1
    assert all(candidate.raw_reference["root_deployment_scan_mode"] is True for candidate in result.candidates_preview)


def test_root_deployment_scan_skips_migrations_by_default(monkeypatch) -> None:
    responses = _root_github_responses({"arbitrum": ["usdc"]}, include_migrations=True)
    monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

    result = ExtractionPipeline().run(_root_artifact(), _fingerprint(), b"<html>github</html>")

    assert "github_directory_migrations_skipped_for_root_scan" in result.warnings
    assert result.metadata["skipped_migrations_count"] == 1
    assert all("migrations" not in document.source_file_path for document in result.source_documents)
    assert "deployments/arbitrum/usdc/configuration.json" in {document.source_file_path for document in result.source_documents}


def test_root_deployment_scan_file_budget_is_fair_across_networks(monkeypatch) -> None:
    old_files = settings.github_crawl_max_files
    settings.github_crawl_max_files = 2
    try:
        responses = _root_github_responses({"arbitrum": ["usdc"], "base": ["usdc"], "mainnet": ["usdc"]})
        monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

        result = ExtractionPipeline().run(_root_artifact(), _fingerprint(), b"<html>github</html>")

        networks = {row.network for row in result.normalized_rows}
        assert len(networks) == 2
        assert "github_directory_file_limit_reached" in result.warnings
        assert len(result.source_documents) == 2
    finally:
        settings.github_crawl_max_files = old_files


def test_root_deployment_scan_partial_fetch_failure_keeps_other_networks(monkeypatch) -> None:
    responses = _root_github_responses({"arbitrum": ["usdc"], "base": ["usdc"]})
    failed_url = "https://api.github.com/repos/compound-finance/comet/contents/deployments/arbitrum?ref=main"
    responses[failed_url] = _FakeResponse(error=True)
    monkeypatch.setattr("app.ingestion.github_source_resolver.httpx.Client", lambda *args, **kwargs: _FakeClient(responses))

    result = ExtractionPipeline().run(_root_artifact(), _fingerprint(), b"<html>github</html>")

    assert {row.network for row in result.normalized_rows} == {"Base"}
    assert "github_directory_partial_fetch_failures" in result.warnings
    assert "github_directory_fetch_failed" not in result.warnings
    assert result.metadata["fetch_failed_count"] == 1


class _FakeClient:
    def __init__(self, responses: dict[str, "_FakeResponse"]) -> None:
        self.responses = responses
        self.requests: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, headers: dict | None = None):
        self.requests.append({"url": url, "headers": headers or {}})
        key = url if url in self.responses else url.replace("https://raw.example/", "download:")
        response = self.responses[key]
        response.url = url
        if response.error:
            raise RuntimeError("fake fetch failure")
        return response


class _FakeResponse:
    def __init__(
        self,
        *,
        json_data=None,
        content: bytes = b"",
        error: bool = False,
        status_code: int = 200,
        text: str | None = None,
        headers: dict | None = None,
    ) -> None:
        self._json_data = json_data
        self.content = content
        self.url = ""
        self.error = error
        self.status_code = status_code
        self.text = text if text is not None else content.decode("utf-8", errors="replace")
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.error:
            raise RuntimeError("fake fetch failure")
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


def _root_github_responses(network_markets: dict[str, list[str]], *, include_migrations: bool = False) -> dict[str, _FakeResponse]:
    responses: dict[str, _FakeResponse] = {}
    root_api = "https://api.github.com/repos/compound-finance/comet/contents/deployments?ref=main"
    responses[root_api] = _FakeResponse(json_data=[_root_directory_item(f"deployments/{network}") for network in network_markets])
    counter = 1
    for network, markets in network_markets.items():
        network_path = f"deployments/{network}"
        network_api = f"https://api.github.com/repos/compound-finance/comet/contents/{network_path}?ref=main"
        responses[network_api] = _FakeResponse(json_data=[_root_directory_item(f"{network_path}/{market}") for market in markets])
        for market in markets:
            market_path = f"{network_path}/{market}"
            market_api = f"https://api.github.com/repos/compound-finance/comet/contents/{market_path}?ref=main"
            items = [_root_file_item(f"{market_path}/configuration.json")]
            if include_migrations:
                items.append(_root_directory_item(f"{market_path}/migrations"))
            responses[market_api] = _FakeResponse(json_data=items)
            address = f"0x{counter:040x}".encode()
            responses[f"download:{market_path}/configuration.json"] = _FakeResponse(content=b'{"comet":"' + address + b'"}')
            counter += 1
    return responses


def _root_directory_item(path: str) -> dict:
    return {
        "type": "dir",
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "url": f"https://api.github.com/repos/compound-finance/comet/contents/{path}?ref=main",
    }


def _root_file_item(path: str, *, size: int = 64) -> dict:
    return {
        "type": "file",
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "size": size,
        "url": f"https://api.github.com/repos/compound-finance/comet/contents/{path}?ref=main",
        "download_url": f"https://raw.example/{path}",
    }
