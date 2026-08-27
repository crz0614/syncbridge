from __future__ import annotations

import json
from pathlib import Path


class FieldMap:
    def __init__(self, rules: dict[str, str] | None = None):
        self.rules = rules or {}

    @classmethod
    def from_file(cls, path: str | None):
        if not path:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            raise ValueError("field map must contain source:destination strings")
        return cls(data)

    def apply(self, payload: dict) -> dict:
        if not self.rules:
            return payload.copy()
        return {target: payload[source] for source, target in self.rules.items() if source in payload}
