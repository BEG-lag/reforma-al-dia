"""Read and write the Markdown of a leyes-mx law without losing a byte.

A law file is YAML front matter plus a body of blocks separated by blank
lines. leyes-mx writes every paragraph, heading, table and editorial note as
one block, so splitting on blank lines and joining them back reproduces the
file exactly. On top of that list this module keeps an index of the articles
of the body (each one a heading) and of the division headings, which is all
the applier needs to edit one unit without touching anything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import yaml

from .legal import (
    ARTICLE_RANGE_RE,
    DIVISION_RE,
    REFORM_TRANSITORIOS_RE,
    TRANSITORIOS_RE,
    article_key,
    fold,
    is_note,
    unescape_md,
)

_HEADING = re.compile(r"^(#{1,6}) (.*)$", re.DOTALL)
_ARTICLE_HEADING = re.compile(r"^art[ií]culos?\s+\d", re.IGNORECASE)


@dataclass
class Article:
    label: str  # as in the heading, unescaped: "Artículo 64 Quáter."
    key: str  # canonical: "64 quater"
    heading: int  # block index of the heading
    start: int  # first content block
    end: int  # one past the last content block

    @property
    def span(self) -> range:
        return range(self.start, self.end)


@dataclass
class Heading:
    index: int
    level: int
    text: str  # unescaped
    kind: str | None  # libro, parte, titulo, capitulo, seccion (None if not a division)


@dataclass
class LawDoc:
    front: str  # YAML between the two "---" lines, with its final newline
    blocks: list[str]
    articles: list[Article] = field(default_factory=list)
    headings: list[Heading] = field(default_factory=list)
    trans_start: int = 0
    article_level: int = 6

    # ------------------------------------------------------------------ I/O

    @classmethod
    def parse(cls, text: str) -> LawDoc:
        if not text.startswith("---\n"):
            raise ValueError("law file without front matter")
        end = text.find("\n---\n", 4)
        if end < 0:
            raise ValueError("unterminated front matter")
        front = text[4 : end + 1]
        body = text[end + 5 :]
        if body.startswith("\n"):
            body = body[1:]
        blocks = body.rstrip("\n").split("\n\n") if body.strip() else []
        doc = cls(front=front, blocks=blocks)
        doc.reindex()
        return doc

    def render(self) -> str:
        return "---\n" + self.front + "---\n\n" + "\n\n".join(self.blocks) + "\n"

    def copy(self) -> LawDoc:
        doc = LawDoc(front=self.front, blocks=list(self.blocks))
        doc.reindex()
        return doc

    # ---------------------------------------------------------------- index

    def reindex(self) -> None:
        self.headings = []
        self.trans_start = len(self.blocks)
        for i, block in enumerate(self.blocks):
            m = _HEADING.match(block)
            if not m:
                continue
            level, text = len(m.group(1)), unescape_md(m.group(2))
            if level == 2 and (TRANSITORIOS_RE.match(text) or REFORM_TRANSITORIOS_RE.match(text)):
                self.trans_start = min(self.trans_start, i)
            div = DIVISION_RE.match(text.split(" · ")[0]) if not _ARTICLE_HEADING.match(text) else None
            kind = fold(div.group("kind")) if div else None
            self.headings.append(Heading(i, level, text, kind))

        self.articles = []
        levels: dict[int, int] = {}
        body_headings = [h for h in self.headings if h.index < self.trans_start]
        for h in body_headings:
            if _ARTICLE_HEADING.match(h.text) and h.level > 1:
                levels[h.level] = levels.get(h.level, 0) + 1
        self.article_level = max(levels, key=lambda lv: levels[lv]) if levels else 6
        for n, h in enumerate(body_headings):
            if h.level != self.article_level or not _ARTICLE_HEADING.match(h.text):
                continue
            nxt = body_headings[n + 1].index if n + 1 < len(body_headings) else self.trans_start
            key = article_key(h.text)
            if key is None and ARTICLE_RANGE_RE.match(h.text):
                key = "range " + h.text
            self.articles.append(Article(h.text, key or h.text, h.index, h.index + 1, nxt))

    # ------------------------------------------------------------- queries

    def article(self, key: str) -> Article | None:
        for a in self.articles:
            if a.key == key:
                return a
        return None

    def article_blocks(self, key: str) -> list[str]:
        a = self.article(key)
        return [] if a is None else self.blocks[a.start : a.end]

    def article_map(self) -> dict[str, list[str]]:
        return {a.key: self.blocks[a.start : a.end] for a in self.articles}

    def division_levels(self) -> dict[str, int]:
        levels: dict[str, int] = {}
        for h in self.headings:
            if h.kind and h.index < self.trans_start:
                levels.setdefault(h.kind, h.level)
        return levels

    @property
    def meta(self) -> dict:
        return yaml.safe_load(self.front) or {}

    # ------------------------------------------------------------ mutation

    def set_front(self, **values: object) -> None:
        """Update or add front matter keys while keeping the order and style of the rest."""
        lines = self.front.splitlines()
        for key, value in values.items():
            dumped = yaml.safe_dump({key: value}, allow_unicode=True, width=1000, sort_keys=False).rstrip("\n")
            for i, line in enumerate(lines):
                if line.startswith(f"{key}:"):
                    lines[i] = dumped
                    break
            else:
                lines.append(dumped)
        self.front = "\n".join(lines) + "\n"

    def set_last_reform(self, day: date) -> None:
        """Front matter and the "Última reforma publicada DOF dd-mm-yyyy" header line."""
        self.set_front(last_reform_dof=day)
        stamp = day.strftime("%d-%m-%Y")
        limit = self.articles[0].heading if self.articles else len(self.blocks)
        for i in range(min(limit, 40)):
            if re.match(r"^[ÚU]ltima reforma publicada", self.blocks[i], re.IGNORECASE):
                self.blocks[i] = re.sub(r"\d{1,2}-\d{1,2}-\d{4}", stamp, self.blocks[i])
                break


def block_kind(block: str) -> str:
    """text, note, heading, table or quote."""
    if block.startswith("#"):
        return "heading"
    if block.startswith("|"):
        return "table"
    if block.startswith(">"):
        return "quote"
    starred = block.startswith("*") and block.endswith("*") and not block.startswith("**") and len(block) > 2
    if starred and is_note(unescape_md(block[1:-1])):
        return "note"
    return "text"


def note_text(block: str) -> str:
    return unescape_md(block[1:-1])


def plain(block: str) -> str:
    """A block as a reader sees it: unescaped, notes without their italics."""
    if block_kind(block) == "note":
        return note_text(block)
    return unescape_md(block)
