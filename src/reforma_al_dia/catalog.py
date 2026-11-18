"""The laws kept in leyes-mx and how to recognize their names in DOF text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .lawdoc import LawDoc
from .legal import fold


@dataclass
class Law:
    slug: str
    title: str
    abbreviation: str
    names: list[str] = field(default_factory=list)  # folded, longest first

    @property
    def path(self) -> str:
        return f"leyes/{self.slug}.md"


def _variants(title: str, former: list[str]) -> list[str]:
    names = {fold(title).rstrip(".")}
    short = title.split(",")[0].strip()
    if short != title and len(short) > 12:
        names.add(fold(short))
    for name in former:
        names.add(fold(name))
    return sorted(names, key=len, reverse=True)


def load_catalog(leyes_root: Path) -> list[Law]:
    """Laws listed in ``catalog.yml``, with titles read from each file's front matter."""
    data = yaml.safe_load((leyes_root / "catalog.yml").read_text(encoding="utf-8"))
    laws: list[Law] = []
    for row in data["laws"]:
        slug = row["slug"]
        path = leyes_root / "leyes" / f"{slug}.md"
        if not path.exists():
            continue
        meta = LawDoc.parse(path.read_text(encoding="utf-8")).meta
        title = str(meta.get("title") or slug)
        laws.append(Law(slug, title, str(meta.get("abbreviation") or slug.upper()),
                        _variants(title, row.get("former_names") or [])))  # fmt: skip
    return laws


_REGULATION_BEFORE = re.compile(r"reglamento(?:\s+interior)?\s+(?:de\s+la|del|de)\s*$")


@dataclass
class Mention:
    start: int
    end: int
    slug: str


def find_laws(text: str, laws: list[Law]) -> list[Mention]:
    """Mentions of catalog laws in ``text``, longest name first, without overlaps.

    Mentions inside the name of a regulation ("Reglamento de la Ley ...") are
    ignored, and so is the Constitution when it is only part of a law's name
    ("Ley de Amparo, Reglamentaria de los artículos 103 y 107 de la Constitución ...").
    """
    folded = fold(text)
    found: list[Mention] = []
    taken = [False] * len(folded)
    names = sorted(((name, law.slug) for law in laws for name in law.names), key=lambda x: len(x[0]), reverse=True)
    for name, slug in names:
        for m in re.finditer(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", folded):
            if any(taken[m.start() : m.end()]):
                continue
            before = folded[max(0, m.start() - 160) : m.start()]
            if _REGULATION_BEFORE.search(before):
                continue
            if slug == "cpeum":
                clause = re.split(r"[;:]", before)[-1]
                if "reglamentari" in clause:
                    continue
            for i in range(m.start(), m.end()):
                taken[i] = True
            found.append(Mention(m.start(), m.end(), slug))
    return sorted(found, key=lambda x: x.start)
