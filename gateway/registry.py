"""Destination registry.

resolve() maps (destination_system, field_path-as-the-agent-names-it) to a
RegistryEntry. None means DENY, not "unknown, proceed" -- registry completeness
is a security property, and a gap is a vulnerability, not a default.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import yaml

from schemas import RegistryEntry

_REGISTRY_PATH = Path(__file__).parent / "registry.yaml"


def _norm(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().lower()


class Registry:
    def __init__(self, path: Path = _REGISTRY_PATH):
        data = yaml.safe_load(path.read_text())
        self.version: str = str(data["version"])
        self.entries: list[RegistryEntry] = [RegistryEntry(**e) for e in data["entries"]]

        self._index: dict[tuple[str, str], RegistryEntry] = {}
        for entry in self.entries:
            for name in (entry.canonical_field, *entry.aliases):
                key = (entry.destination_system, _norm(name))
                if key in self._index and self._index[key] is not entry:
                    raise ValueError(f"registry alias collision on {key!r}")
                self._index[key] = entry

    def resolve(self, destination_system: str, field_path: str) -> RegistryEntry | None:
        return self._index.get((_norm(destination_system), _norm(field_path)))


_default: Registry | None = None


def get_registry() -> Registry:
    global _default
    if _default is None:
        _default = Registry()
    return _default


def resolve(destination_system: str, field_path: str) -> RegistryEntry | None:
    return get_registry().resolve(destination_system, field_path)
