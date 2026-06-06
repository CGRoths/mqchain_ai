from __future__ import annotations

import re

from app.ingestion.deployment_extractor import deployment_table_from_rows, infer_role, network_from_source_url


SOLIDITY_CONSTANT_RE = re.compile(
    r"(?P<type>(?:address|I[A-Za-z0-9_]+))\s+"
    r"(?:(?:public|internal|private|external)\s+)?"
    r"constant\s+"
    r"(?P<name>[A-Z0-9_]+)\s*=\s*"
    r"(?:(?P<cast>I[A-Za-z0-9_]+)\s*\(\s*)?"
    r"(?P<address>0x[a-fA-F0-9]{40})",
)


def extract_solidity_deployment_table(text: str, *, source_url: str | None) -> list[dict]:
    default_network = network_from_source_url(source_url)
    rows = []
    lines = text.splitlines()
    for match in SOLIDITY_CONSTANT_RE.finditer(text):
        line_number = text.count("\n", 0, match.start()) + 1
        name = match.group("name")
        comment = _nearby_comment(lines, line_number)
        rows.append(
            {
                "network": default_network,
                "address": match.group("address"),
                "contract_name": name,
                "role": infer_role(name, match.group("type"), match.group("cast"), comment),
                "line_number": line_number,
                "raw_row": {"raw_line": _source_line(lines, line_number), "comment": comment, "constant_name": name},
                "column_name": "solidity_constant",
                "role_source": name,
                "confidence": 90,
            }
        )
    return deployment_table_from_rows(
        rows,
        source_url=source_url,
        source_input_type="github_solidity_address_book",
        evidence_type="official_github_deployment",
        text=text,
        table_name="solidity_address_constants",
    )


def _nearby_comment(lines: list[str], line_number: int) -> str | None:
    comments = []
    for index in range(line_number - 2, max(-1, line_number - 5), -1):
        stripped = lines[index].strip()
        if not stripped:
            if comments:
                break
            continue
        if stripped.startswith("//"):
            comments.insert(0, stripped.lstrip("/").strip())
            continue
        break
    return " ".join(comments) or None


def _source_line(lines: list[str], line_number: int) -> str | None:
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()
    return None
