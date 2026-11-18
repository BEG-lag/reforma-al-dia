"""Paths and settings, from command-line flags and ``REFORMA_*`` environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

LEYES_REPO = "BEG-lag/leyes-mx"
LEYES_URL = f"https://github.com/{LEYES_REPO}"


@dataclass
class Config:
    root: Path  # this repository: data/ and docs/ live here
    leyes: Path  # a clone of leyes-mx that the runner may branch and commit in
    work: Path  # local state: logs, HTTP cache, run state (git-ignored)
    model: str | None = None
    effort: str | None = None
    workers: int = 4
    claude_binary: str = "claude"
    leyes_repo: str = LEYES_REPO

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def docs(self) -> Path:
        return self.root / "docs"

    @property
    def claude_cache(self) -> Path:
        return self.data / "cache" / "claude"

    @property
    def sidof_cache(self) -> Path:
        return self.work / "cache" / "sidof"

    @property
    def log_file(self) -> Path:
        return self.work / "reforma-al-dia.log"

    @classmethod
    def load(cls, root: str | None = None, leyes: str | None = None, work: str | None = None) -> Config:
        here = Path(root or os.environ.get("REFORMA_ROOT") or Path.cwd()).resolve()
        default_leyes = Path.home() / ".cache" / "reforma-al-dia" / "leyes-mx"
        return cls(
            root=here,
            leyes=Path(leyes or os.environ.get("REFORMA_LEYES") or default_leyes).expanduser().resolve(),
            work=Path(work or os.environ.get("REFORMA_WORK") or here / ".work").expanduser().resolve(),
            model=os.environ.get("REFORMA_MODEL") or None,
            effort=os.environ.get("REFORMA_EFFORT") or None,
            workers=int(os.environ.get("REFORMA_WORKERS") or 4),
            claude_binary=os.environ.get("REFORMA_CLAUDE") or "claude",
        )


def setup_logging(cfg: Config, verbose: bool = False) -> None:
    cfg.work.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    file_handler = RotatingFileHandler(cfg.log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root.handlers = [file_handler, console]
    logging.getLogger("httpx").setLevel(logging.WARNING)
