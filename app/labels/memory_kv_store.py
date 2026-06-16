from __future__ import annotations

from collections import defaultdict

from app.labels.kv_store import KVWrite


class MemoryKVStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[bytes, bytes]] = defaultdict(dict)
        self._closed = False

    def get(self, column_family: str, key: bytes) -> bytes | None:
        self._ensure_open()
        return self._data[column_family].get(bytes(key))

    def put(self, column_family: str, key: bytes, value: bytes) -> None:
        self._ensure_open()
        self._data[column_family][bytes(key)] = bytes(value)

    def write_batch(self, writes: list[KVWrite]) -> None:
        self._ensure_open()
        for write in writes:
            self.put(write.column_family, write.key, write.value)

    def iter_prefix(self, column_family: str, prefix: bytes) -> list[tuple[bytes, bytes]]:
        self._ensure_open()
        prefix = bytes(prefix)
        return sorted((key, value) for key, value in self._data[column_family].items() if key.startswith(prefix))

    def seek_timeline(self, column_family: str, key_prefix: bytes, block_or_slot: int) -> tuple[bytes, bytes] | None:
        self._ensure_open()
        key_prefix = bytes(key_prefix)
        best: tuple[bytes, bytes] | None = None
        for key, value in self.iter_prefix(column_family, key_prefix):
            if len(key) < len(key_prefix) + 8:
                continue
            valid_from = int.from_bytes(key[len(key_prefix) : len(key_prefix) + 8], "big")
            if valid_from <= block_or_slot:
                best = (key, value)
            else:
                break
        return best

    def health(self) -> dict:
        self._ensure_open()
        return {
            "backend": "memory",
            "status": "ok",
            "column_families": {name: len(values) for name, values in sorted(self._data.items())},
        }

    def close(self) -> None:
        self._closed = True

    def clear(self) -> None:
        self._ensure_open()
        self._data.clear()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("memory_kv_store_closed")


DEFAULT_MEMORY_KV_STORE = MemoryKVStore()
