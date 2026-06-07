from __future__ import annotations

from app.ingestion.extractor_base import ExtractorRegistry
from app.ingestion.extractors.html_tables import HTMLHeadingTableExtractor
from app.ingestion.extractors.json_yaml import JsonYamlAddressExtractor
from app.ingestion.extractors.loose_address import LooseAddressExtractor
from app.ingestion.extractors.markdown_tables import MarkdownTableExtractor
from app.ingestion.extractors.solidity import SolidityConstantExtractor
from app.ingestion.extractors.typescript_javascript import TypeScriptJavascriptAddressExtractor


def default_extractor_registry() -> ExtractorRegistry:
    return ExtractorRegistry(
        [
            HTMLHeadingTableExtractor(),
            JsonYamlAddressExtractor(),
            SolidityConstantExtractor(),
            TypeScriptJavascriptAddressExtractor(),
            MarkdownTableExtractor(),
            LooseAddressExtractor(),
        ]
    )
