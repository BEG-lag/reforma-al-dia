"""Read the reform history that leyes-mx keeps in git.

Every version of a law in leyes-mx is a commit by the bot, dated on the DOF
publication it reflects, whose message lists the decrees between the previous
version and this one. A commit whose subject reads ``LFT: reforma DOF
27-12-2022`` and lists exactly one decree is a clean natural experiment: the
previous version is the law the morning before that decree, and this version is
the Cámara's official consolidation of it.
"""

from __future__ import annotations

import difflib
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from .legal import fold
from .sidof import NoteRef, Sidof

_SUBJECT = re.compile(
    r"^(?P<abbr>\S+): (?P<kind>reforma|reformas|\d+ decretos de reforma,) DOF (?P<d>\d{2}-\d{2}-\d{4})"
)
_DECREE = re.compile(
    r"^- (?P<title>.+?) \(DOF (?P<d>\d{2}-\d{2}-\d{4})\)\s*(?:\n\s+(?P<url>https?://\S+))?", re.MULTILINE
)


def _date(dmy: str) -> date:
    d, m, y = (int(x) for x in dmy.split("-"))
    return date(y, m, d)


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


@dataclass
class Pair:
    """Two consecutive versions of a law with exactly one decree in between."""

    slug: str
    abbreviation: str
    before: str  # commit of the previous version
    after: str  # commit of the official version that reflects the decree
    version_date: date  # DOF date of the official version (commit date)
    decree_date: date
    decree_title: str
    decree_pdf: str | None
    note_code: int | None = None

    @property
    def id(self) -> str:
        return f"{self.decree_date.isoformat()}-{self.slug}"

    @property
    def same_day(self) -> bool:
        """The official version is dated on the decree itself (nothing else can be mixed in)."""
        return self.version_date == self.decree_date

    def to_json(self) -> dict:
        data = asdict(self)
        data["version_date"] = self.version_date.isoformat()
        data["decree_date"] = self.decree_date.isoformat()
        data["id"] = self.id
        data["same_day"] = self.same_day
        return data


def single_decree_pairs(root: Path, slug: str) -> list[Pair]:
    """Versions of ``leyes/<slug>.md`` that reflect exactly one reform decree."""
    path = f"leyes/{slug}.md"
    log = git(root, "log", "--format=%H%x1f%B%x1e", "--", path)
    commits = []
    for entry in log.split("\x1e"):
        entry = entry.strip("\n")
        if not entry:
            continue
        sha, _, body = entry.partition("\x1f")
        commits.append((sha.strip(), body))
    pairs: list[Pair] = []
    for i, (sha, body) in enumerate(commits[:-1]):
        subject = body.split("\n", 1)[0]
        m = _SUBJECT.match(subject)
        if not m or m.group("kind") != "reforma":
            continue
        decrees = list(_DECREE.finditer(body))
        if len(decrees) != 1:
            continue
        d = decrees[0]
        pairs.append(
            Pair(
                slug=slug,
                abbreviation=m.group("abbr"),
                before=commits[i + 1][0],
                after=sha,
                version_date=_date(m.group("d")),
                decree_date=_date(d.group("d")),
                decree_title=d.group("title").strip(),
                decree_pdf=d.group("url"),
            )
        )
    return pairs


def law_at(root: Path, commit: str, slug: str) -> str:
    return git(root, "show", f"{commit}:leyes/{slug}.md")


def match_note(sidof: Sidof, day: date, title: str) -> NoteRef | None:
    """The DOF note of a decree, found by title in the edition of its publication date."""
    want = fold(re.sub(r"\s+", " ", title)).strip(" .")
    best: tuple[float, NoteRef | None] = (0.0, None)
    for note in sidof.day_index(day):
        if not fold(note.title).startswith("decreto"):
            continue
        got = fold(note.title).strip(" .")
        ratio = difflib.SequenceMatcher(None, want, got).ratio()
        if ratio > best[0]:
            best = (ratio, note)
    return best[1] if best[0] >= 0.72 else None
