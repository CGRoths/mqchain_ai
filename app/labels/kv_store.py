from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


LABEL_CURRENT_CF = "label_current"
LABEL_TIMELINE_CF = "label_timeline"


@dataclass(frozen=True)
class KVWrite:
    column_family: str
    key: bytes
    value: bytes


class KVStore(Protocol):
    def get(self, column_family: str, key: bytes) -> bytes | None:
        ...

    def put(self, column_family: str, key: bytes, value: bytes) -> None:
        ...

    def write_batch(self, writes: list[KVWrite]) -> None:
        ...

    def iter_prefix(self, column_family: str, prefix: bytes) -> list[tuple[bytes, bytes]]:
        ...

    def seek_timeline(self, column_family: str, key_prefix: bytes, block_or_slot: int) -> tuple[bytes, bytes] | None:
        ...

    def health(self) -> dict:
        ...

    def close(self) -> None:
        ...
