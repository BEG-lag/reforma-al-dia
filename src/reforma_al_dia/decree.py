"""Read a reform decree from its DOF HTML and split it by law and by article.

A reform decree has a fixed anatomy::

    DECRETO por el que se reforman ... de la Ley X.            <- title
    ... a sus habitantes sabed: ... DECRETA:                   <- enacting formula
    Artículo Único.- Se reforman los artículos 3, ...; se      <- operative article:
        adiciona ...; y se deroga ..., de la Ley X, para           the instruction
        quedar como sigue:
    Artículo 3.- ...                                           <- new text, where "..."
    I. a III. ...                                                  stands for text that
    IV. Nuevo texto;                                               does not change
    Transitorios
    Primero.- El presente Decreto entrará en vigor ...
    Ciudad de México, a ... Rúbricas."                         <- signatures

A decree can amend several laws, one operative article each ("Artículo
Primero.- Se reforma ... de la Ley A", "Artículo Segundo.- ... del Código B").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from bs4 import BeautifulSoup

from .catalog import Law, find_laws
from .legal import DIVISION_RE, ORDINAL_ARTICLE_RE, article_key, fold, match_article, normalize_space

_ENACTING = re.compile(r"d\s*e\s*c\s*r\s*e\s*t\s*a\s*:|\bdeclara\b", re.IGNORECASE)
_OPERATIVE = re.compile(
    r"\bse\s+(?:reforma|adiciona|deroga|modifica|declara[n]?\s+reformad|abroga|expide|recorre)", re.IGNORECASE
)
_TRANSITORIOS = re.compile(r"^(?:art[ií]culos?\s+)?(?:transitorios?|disposiciones\s+transitorias)\s*[.:]?$", re.I)
_SIGNATURE = re.compile(r"^(?:ciudad de m[ée]xico|m[ée]xico,\s*d\.\s*f\.|sal[oó]n de sesiones)", re.IGNORECASE)
_ELLIPSIS = re.compile(r"^(?:\.\s*){3,}$|^…+\.*$|^\.\.\.+$")


@dataclass
class Para:
    text: str
    css: str = ""
    rows: list[list[str]] | None = None

    @property
    def is_table(self) -> bool:
        return self.rows is not None

    def as_line(self) -> str:
        if self.rows is None:
            return self.text
        return "\n".join("| " + " | ".join(row) + " |" for row in self.rows)


@dataclass
class DecreeArticle:
    key: str | None
    label: str
    lines: list[str]
    headings: list[str] = field(default_factory=list)

    @property
    def only_ellipsis(self) -> bool:
        """True when the block shows no new text at all (just the label and "...")."""
        rest = [ln for ln in self.lines[1:] if not is_ellipsis(ln)]
        first = self.lines[0] if self.lines else ""
        m = match_article(first)
        head_rest = (m.group("rest") if m and "rest" in m.groupdict() else "") or ""
        return not rest and (not head_rest.strip() or is_ellipsis(head_rest.strip()))


@dataclass
class Section:
    label: str  # "Artículo Segundo.-", "" when the decree has a single unlabeled instruction
    instruction: str
    slug: str | None
    mentions: list[str]
    body: list[Para]

    def articles(self) -> list[DecreeArticle]:
        return split_articles(self.body)


@dataclass
class Decree:
    code: int
    day: date
    title: str
    paragraphs: list[Para]
    sections: list[Section]
    transitorios: list[Para]

    def sections_for(self, slug: str) -> list[Section]:
        return [s for s in self.sections if s.slug == slug]

    @property
    def official_title(self) -> str:
        """The title as printed in the DOF ("DECRETO por el que ..."), as the Cámara cites it."""
        head = next((p.text for p in self.paragraphs[:3] if p.text.upper().startswith("DECRETO")), None)
        if head and head.startswith("DECRETO"):
            return head
        return "DECRETO" + self.title[7:] if self.title.lower().startswith("decreto") else self.title

    @property
    def slugs(self) -> list[str]:
        out: list[str] = []
        for s in self.sections:
            if s.slug and s.slug not in out:
                out.append(s.slug)
        return out


def is_ellipsis(text: str) -> bool:
    """A line that stands for unchanged text: "...", "……..", "I. a III. ...", "a) a c) ..."."""
    t = text.strip()
    if _ELLIPSIS.match(t):
        return True
    return bool(re.match(r"^.{0,40}?(?:\.\.\.|…)+\.?$", t) and len(t) <= 60)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def html_paragraphs(html: str) -> list[Para]:
    """Paragraphs and tables of a DOF note, in reading order, with whitespace normalized."""
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with(" ")
    body = soup.body or soup
    out: list[Para] = []
    for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "div", "p", "table"]):
        if el.name == "table":
            if el.find_parent("table"):
                continue
            rows = []
            for tr in el.find_all("tr"):
                cells = [normalize_space(" ".join(td.get_text("").split())) for td in tr.find_all(["td", "th"])]
                if any(cells):
                    rows.append(cells)
            if rows:
                out.append(Para(text="", rows=rows))
            continue
        if el.find_parent("table") or el.find(["div", "p", "table", "h1", "h2", "h3", "h4", "h5"]):
            continue
        text = normalize_space(" ".join(el.get_text("").split()))
        if text:
            css = " ".join(el.get("class") or [])
            out.append(Para(text=text, css=css))
    return out


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def _is_operative(text: str) -> tuple[str, str] | None:
    """(label, rest) when ``text`` is an operative article of the decree."""
    m = ORDINAL_ARTICLE_RE.match(text)
    if m and _OPERATIVE.search(m.group("rest")[:400]):
        return m.group("label").strip(), m.group("rest").strip()
    if (
        re.match(r"^se\s+(?:reforma|adiciona|deroga|modifica)", text, re.IGNORECASE)
        and not text.isupper()
        and re.search(r"para\s+quedar", text, re.IGNORECASE)
    ):
        return "", text
    return None


def _unfinished(instruction: str) -> bool:
    return not re.search(r"para\s+quedar|:\s*$", instruction, re.I) and len(instruction) < 20000


def instruction_scope(instruction: str) -> str:
    """The instruction up to "para quedar como sigue" (not "para quedar como XIII", which renumbers)."""
    m = re.search(r"para\s+quedar\s+(?:como\s+sigue|en\s+los\s+siguientes\s+t[eé]rminos|redactad)", instruction, re.I)
    if m:
        return instruction[: m.start()]
    matches = list(re.finditer(r"para\s+quedar", instruction, re.I))
    return instruction[: matches[-1].start()] if matches else instruction


def _target(instruction: str, laws: list[Law]) -> tuple[str | None, list[str]]:
    """The law an operative article amends: the last catalog law named before "para quedar"."""
    cut = instruction_scope(instruction)
    mentions = find_laws(cut, laws)
    slugs = [m.slug for m in mentions]
    return (slugs[-1] if slugs else None), slugs


def parse_decree(code: int, day: date, title: str, html: str, laws: list[Law]) -> Decree:
    paras = html_paragraphs(html)
    start = 0
    for i, p in enumerate(paras[:40]):
        if not p.is_table and _ENACTING.search(p.text):
            start = i + 1
            break

    sections: list[Section] = []
    transitorios: list[Para] = []
    current: Section | None = None
    in_trans = False
    for p in paras[start:]:
        text = p.text
        if not p.is_table and _TRANSITORIOS.match(text):
            in_trans = True
            current = None
            transitorios.append(p)
            continue
        op = None if p.is_table else _is_operative(text)
        if op is not None and (not in_trans or not _SIGNATURE.match(text)):
            label, rest = op
            if in_trans and not re.search(r"para\s+quedar", rest, re.I):
                transitorios.append(p)  # a transitorio that happens to say "se deroga"
                continue
            in_trans = False
            slug, mentions = _target(text, laws)
            current = Section(label=label, instruction=text, slug=slug, mentions=mentions, body=[])
            sections.append(current)
            continue
        if in_trans:
            transitorios.append(p)
        elif current is not None:
            if not current.body and not p.is_table and _unfinished(current.instruction):
                # A long instruction continues in the next paragraph.
                current.instruction += " " + text
                current.slug, current.mentions = _target(current.instruction, laws)
                continue
            current.body.append(p)
    sections = _split_multi_law(sections, laws)
    return Decree(code=code, day=day, title=title, paragraphs=paras, sections=sections, transitorios=transitorios)


def _split_multi_law(sections: list[Section], laws: list[Law]) -> list[Section]:
    """One operative article that amends several laws, with a law-name line before each block."""
    out: list[Section] = []
    for s in sections:
        distinct = list(dict.fromkeys(s.mentions))
        if len(distinct) < 2:
            out.append(s)
            continue
        parts: list[Section] = []
        cur: Section | None = None
        for p in s.body:
            mentions = [] if p.is_table or len(p.text) > 220 else find_laws(p.text, laws)
            if (
                mentions
                and not match_article(p.text)
                and len(fold(p.text)) - (mentions[0].end - mentions[0].start) < 40
            ):
                cur = Section(s.label, s.instruction, mentions[0].slug, s.mentions, [])
                parts.append(cur)
            elif cur is not None:
                cur.body.append(p)
        if parts:
            out.extend(parts)
        else:
            out.append(s)
    return out


# --------------------------------------------------------------------------
# Articles inside a section
# --------------------------------------------------------------------------


def _division(text: str) -> bool:
    m = DIVISION_RE.match(text)
    return bool(m) and len(text) < 220 and not text.rstrip().endswith((",", ";"))


def split_articles(body: list[Para]) -> list[DecreeArticle]:
    """Cut the new text of one law into article blocks, keeping division headings with the next article."""
    out: list[DecreeArticle] = []
    pending: list[str] = []
    after_heading = False
    for p in body:
        line = p.as_line()
        if not p.is_table:
            m = match_article(line)
            if m:
                label = line[: m.start("rest")].strip() if "rest" in m.groupdict() and m.group("rest") else line
                out.append(DecreeArticle(article_key(label), label, [line], pending))
                pending, after_heading = [], False
                continue
            if _division(line):
                pending.append(line)
                after_heading = True
                continue
            if after_heading and len(line) < 220 and not is_ellipsis(line):
                pending.append(line)  # the name of a division on its own line
                after_heading = False
                continue
        after_heading = False
        if out:
            out[-1].lines.append(line)
        elif pending:
            pending.append(line)
    return out
