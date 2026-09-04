"""Versioning & Rollback — for prompts, policy documents, or any other text
artifact that changes over an agent's lifetime and might need a fast revert.
VersionStore is a Protocol; FileVersionStore is the zero-dependency reference
implementation — every publish() writes a new immutable version file plus moves
a "current" pointer, so rollback() is just moving the pointer back. Nothing is
ever overwritten, so full history is always recoverable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class VersionStore(Protocol):
    def publish(self, name: str, content: str, *, label: str = "") -> str: ...
    def get(self, name: str, *, version: str | None = None) -> str: ...
    def rollback(self, name: str, *, version: str) -> None: ...
    def history(self, name: str) -> list[dict[str, str]]: ...


@dataclass
class FileVersionStore:
    directory: Path

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _artifact_dir(self, name: str) -> Path:
        d = self.directory / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def publish(self, name: str, content: str, *, label: str = "") -> str:
        d = self._artifact_dir(name)
        version = str(time.time_ns())
        (d / f"{version}.txt").write_text(content)
        (d / f"{version}.label").write_text(label)
        (d / "current").write_text(version)
        return version

    def get(self, name: str, *, version: str | None = None) -> str:
        d = self._artifact_dir(name)
        current_file = d / "current"
        if version is None:
            if not current_file.exists():
                raise FileNotFoundError(f"no versions published for {name!r}")
            version = current_file.read_text().strip()
        return (d / f"{version}.txt").read_text()

    def rollback(self, name: str, *, version: str) -> None:
        d = self._artifact_dir(name)
        if not (d / f"{version}.txt").exists():
            raise ValueError(f"no such version {version!r} for {name!r}")
        (d / "current").write_text(version)

    def history(self, name: str) -> list[dict[str, str]]:
        d = self._artifact_dir(name)
        current = (d / "current").read_text().strip() if (d / "current").exists() else None
        versions = sorted(p.stem for p in d.glob("*.txt"))
        return [
            {
                "version": v,
                "label": (d / f"{v}.label").read_text() if (d / f"{v}.label").exists() else "",
                "current": v == current,
            }
            for v in versions
        ]
