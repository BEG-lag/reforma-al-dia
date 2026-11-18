"""Apply structured edits to a law, deterministically.

The model decides *what* a decree changes; this module decides *how* the file
changes, with the same conventions the Cámara de Diputados uses in its
consolidated texts:

* new text replaces exactly the numbered blocks the edit names, and nothing else;
* every unit that changes gets an editorial note in italics right after it, and
  an existing note for the same unit gets the new date appended
  (``Fracción reformada DOF 01-05-2019, 04-04-2024``) or a new clause
  (``Artículo adicionado DOF 30-12-1997. Reformado DOF 17-01-2024``);
* a repealed unit stays in place with the text the decree gives ("Se deroga.");
* new articles are inserted in numeric order, new division headings right
  before the article they open;
* the decree's transitorios are appended at the end of the file, under
  "ARTÍCULOS TRANSITORIOS DE DECRETOS DE REFORMA", as the Cámara does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .decree import Decree, Section, is_ellipsis
from .edits import DivisionEdit, Edit, Plan, Review
from .lawdoc import LawDoc, block_kind, note_text
from .legal import (
    ORDINAL_ARTICLE_RE,
    REFORM_TRANSITORIOS_RE,
    TRANSITORIOS_RE,
    article_key,
    escape_md,
    fold,
    key_order,
    long_date,
    match_article,
    normalize_space,
    render_table,
)

NOUNS = {
    "articulo": ("Artículo", "o"),
    "parrafo": ("Párrafo", "o"),
    "fraccion": ("Fracción", "a"),
    "inciso": ("Inciso", "o"),
    "subinciso": ("Subinciso", "o"),
    "numeral": ("Numeral", "o"),
    "apartado": ("Apartado", "o"),
    "base": ("Base", "a"),
    "epigrafe": ("Epígrafe", "o"),
    "otro": ("Párrafo", "o"),
    "libro": ("Libro", "o"),
    "titulo": ("Título", "o"),
    "capitulo": ("Capítulo", "o"),
    "seccion": ("Sección", "a"),
}
VERBS = {"reform": "reformad", "add": "adicionad", "repeal": "derogad"}
_SUBJECTS = {
    "articulo": "articulo", "articulos": "articulo", "parrafo": "parrafo", "parrafos": "parrafo",
    "fraccion": "fraccion", "fracciones": "fraccion", "inciso": "inciso", "incisos": "inciso",
    "subinciso": "subinciso", "sub-inciso": "subinciso", "numeral": "numeral", "numerales": "numeral",
    "apartado": "apartado", "apartados": "apartado", "base": "base", "bases": "base", "epigrafe": "epigrafe",
}  # fmt: skip
# How deep a unit sits inside an article: notes of deeper units die with the unit that contains them.
_DEPTH = {"articulo": 0, "apartado": 1, "base": 1, "fraccion": 2, "parrafo": 3, "inciso": 4, "subinciso": 5,
          "numeral": 5, "epigrafe": 3, "otro": 3}  # fmt: skip


# --------------------------------------------------------------------------
# Editorial notes
# --------------------------------------------------------------------------


def stamp(day: date) -> str:
    return day.strftime("%d-%m-%Y")


def note_subject(note: str) -> str | None:
    first = fold(note.split(" ", 1)[0].strip(".,;:"))
    return _SUBJECTS.get(first)


def new_note(op: str, unit_type: str, day: date) -> str:
    noun, gender = NOUNS.get(unit_type, NOUNS["otro"])
    return f"{noun} {VERBS[op]}{gender} DOF {stamp(day)}"


def update_note(note: str, op: str, day: date) -> str:
    """Add a date to an existing note, following the Cámara's style."""
    note = note.strip().rstrip(".")
    clauses = re.split(r"\.\s+(?=[A-ZÁÉÍÓÚ])", note)
    last = fold(clauses[-1])
    verb = VERBS[op]
    m = re.search(r"(reformad|adicionad|derogad|recorrid|reubicad)[oa]s?", last)
    if m and m.group(1) == verb:
        return f"{note}, {stamp(day)}"
    subject = note_subject(note) or "otro"
    gender = NOUNS.get(subject, NOUNS["otro"])[1]
    return f"{note}. {verb.capitalize()}{gender} DOF {stamp(day)}"


def _note_block(text: str) -> str:
    return f"*{escape_md(text)}*"


def render_paragraph(text: str) -> str:
    """A paragraph the model returned, as leyes-mx would write it."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) > 1 and all(ln.strip().startswith("|") for ln in lines):
        rows = []
        for ln in lines:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            rows.append(cells)
        return render_table(rows)
    return escape_md(normalize_space(" ".join(lines)))


# --------------------------------------------------------------------------
# One article
# --------------------------------------------------------------------------


@dataclass
class _Seg:
    block_id: int | None  # 1-based id of the text block (None: notes before any text)
    block: str | None
    notes: list[str] = field(default_factory=list)  # raw note blocks that follow


def _segments(content: list[str]) -> tuple[list[_Seg], dict[int, int], set[int]]:
    segs: list[_Seg] = []
    seg_of: dict[int, int] = {}
    note_ids: set[int] = set()
    for i, block in enumerate(content, start=1):
        if block_kind(block) == "note":
            note_ids.add(i)
            if not segs:
                segs.append(_Seg(None, None))
            segs[-1].notes.append(block)
            seg_of[i] = len(segs) - 1
        else:
            segs.append(_Seg(i, block))
            seg_of[i] = len(segs) - 1
    return segs, seg_of, note_ids


class EditError(ValueError):
    pass


def apply_to_article(content: list[str], edits: list[Edit], day: date) -> list[str]:
    """New content blocks of one article. Raises :class:`EditError` on an invalid edit set."""
    segs, seg_of, note_ids = _segments(content)
    n = len(content)
    replaces: dict[int, tuple[int, Edit]] = {}
    covered: set[int] = set()
    inserts: dict[int, list[Edit]] = {}
    for e in edits:
        if e.operation in ("reform", "repeal"):
            a = e.from_block
            b = e.to_block if e.to_block is not None else a
            if a is None or not (1 <= a <= b <= n):
                raise EditError(f"{e.unit}: invalid block range {a}..{b} (article has {n} blocks)")
            if a in note_ids:
                raise EditError(f"{e.unit}: range starts on an editorial note [{a}]")
            while b in note_ids and b > a:
                b -= 1
            s, t = seg_of[a], seg_of[b]
            if any(k in covered for k in range(s, t + 1)):
                raise EditError(f"{e.unit}: overlaps another edit")
            covered.update(range(s, t + 1))
            replaces[s] = (t, e)
        elif e.operation == "add":
            k = e.insert_after if e.insert_after is not None else n
            if not (0 <= k <= n):
                raise EditError(f"{e.unit}: invalid insert position {k}")
            anchor = -1 if k == 0 else seg_of[k]
            if k == 0 and segs and segs[0].block_id is None:
                anchor = 0  # after the leading notes
            inserts.setdefault(anchor, []).append(e)
        else:
            raise EditError(f"unknown operation {e.operation}")
    for anchor in inserts:
        for s, (t, _) in replaces.items():
            if s <= anchor < t:
                raise EditError("an insertion falls inside a replaced range")

    whole = len(segs) > 0 and replaces.get(_first_text(segs), (None, None))[0] == len(segs) - 1
    out: list[str] = []

    def emit_inserts(anchor: int, tail_notes: list[str]) -> list[str]:
        """Emit insertions after ``anchor``; article-level notes stay after them."""
        keep_after = [x for x in tail_notes if note_subject(note_text(x)) == "articulo"]
        before = [x for x in tail_notes if x not in keep_after]
        out.extend(before)
        for e in inserts.get(anchor, []):
            out.extend(render_paragraph(p) for p in e.paragraphs)
            out.append(_note_block(new_note("add", e.unit_type, day)))
        return keep_after

    out.extend(emit_inserts(-1, []))
    s = 0
    while s < len(segs):
        seg = segs[s]
        if s in replaces:
            t, e = replaces[s]
            trailing = segs[t].notes
            depth = _DEPTH.get(e.unit_type, 3)
            target_i = None
            for i, x in enumerate(trailing):
                subj = note_subject(note_text(x))
                if subj == e.unit_type or (e.unit_type == "otro" and subj == "parrafo"):
                    target_i = i
                    if e.unit_type != "articulo":
                        break
            verb_op = "repeal" if e.operation == "repeal" else "reform"
            if target_i is not None:
                note = _note_block(update_note(note_text(trailing[target_i]), verb_op, day))
            else:
                note = _note_block(new_note(verb_op, e.unit_type, day))
            kept = []
            for i, x in enumerate(trailing):
                if i == target_i:
                    continue
                subj = note_subject(note_text(x))
                if whole and e.unit_type == "articulo":
                    continue
                if subj is not None and _DEPTH.get(subj, 3) > depth:
                    continue
                kept.append(x)
            out.extend(render_paragraph(p) for p in e.paragraphs)
            out.append(note)
            rest = emit_inserts(t, kept)
            out.extend(rest)
            s = t + 1
            continue
        if seg.block is not None:
            out.append(seg.block)
        rest = emit_inserts(s, seg.notes)
        out.extend(rest)
        s += 1
    return out


def _first_text(segs: list[_Seg]) -> int:
    for i, s in enumerate(segs):
        if s.block_id is not None:
            return i
    return 0


# --------------------------------------------------------------------------
# The whole law
# --------------------------------------------------------------------------


@dataclass
class Applied:
    doc: LawDoc
    applied: list[Edit] = field(default_factory=list)
    divisions: list[DivisionEdit] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)  # article keys changed or added
    added: list[str] = field(default_factory=list)


def heading_label(text: str) -> str:
    """``Artículo 64 Quáter.-`` (or a decree line) as a leyes-mx heading label: ``Artículo 64 Quáter.``"""
    text = normalize_space(text)
    m = match_article(text if text.endswith((".", "-", ":")) else text + ".")
    if not m:
        return text
    return normalize_space(text[: m.end("suf")].strip().rstrip(".")) + "."


def apply_plan(
    doc: LawDoc,
    plan: Plan,
    day: date,
    *,
    new_labels: dict[str, str] | None = None,
) -> Applied:
    """Apply a plan to a copy of ``doc``. Invalid edits are skipped and reported for review."""
    work = doc.copy()
    result = Applied(doc=work)
    new_labels = new_labels or {}

    by_key: dict[str, list[Edit]] = {}
    for e in plan.edits:
        key = article_key(e.article)
        if key is None:
            result.reviews.append(Review(e.article, f"edit for an unrecognized article label ({e.unit})", "validator"))
            continue
        by_key.setdefault(key, []).append(e)

    # Existing articles, bottom-up so block indices stay valid.
    existing = {a.key: a for a in work.articles}
    new_articles: dict[str, list[Edit]] = {}
    for key in sorted(by_key, key=lambda k: existing[k].start if k in existing else -1, reverse=True):
        edits = by_key[key]
        if key not in existing:
            if all(e.operation == "add" and e.unit_type == "articulo" for e in edits):
                new_articles[key] = edits
            else:
                result.reviews.append(
                    Review(edits[0].article, "the article does not exist in the current text", "validator")
                )
            continue
        art = existing[key]
        whole_adds = [e for e in edits if e.operation == "add" and e.unit_type == "articulo"]
        if whole_adds:
            result.reviews.append(Review(art.label, "the decree adds an article that already exists", "validator"))
            edits = [e for e in edits if e not in whole_adds]
        bad = [e for e in edits if any(is_ellipsis(p) for p in e.paragraphs) or not e.paragraphs]
        for e in bad:
            result.reviews.append(
                Review(art.label, f"{e.unit}: the new text is empty or only an ellipsis", "validator")
            )
        edits = [e for e in edits if e not in bad]
        if not edits:
            continue
        content = work.blocks[art.start : art.end]
        try:
            new_content = apply_to_article(content, edits, day)
        except EditError as exc:
            result.reviews.append(Review(art.label, f"edits not applied: {exc}", "validator"))
            continue
        work.blocks[art.start : art.end] = new_content
        result.applied += edits
        result.touched.append(key)
    work.reindex()

    # New articles, in numeric order, each after the last article that sorts before it.
    for key in sorted(new_articles, key=key_order):
        edits = new_articles[key]
        label = new_labels.get(key) or heading_label(edits[0].article)
        paragraphs = [p for e in edits for p in e.paragraphs]
        if not paragraphs or any(is_ellipsis(p) for p in paragraphs):
            result.reviews.append(Review(label, "new article without text", "validator"))
            continue
        before = [a for a in work.articles if not a.key.startswith("range ") and key_order(a.key) < key_order(key)]
        at = before[-1].end if before else (work.articles[0].heading if work.articles else work.trans_start)
        blocks = [f"{'#' * work.article_level} {escape_md(label)}"]
        blocks += [render_paragraph(p) for p in paragraphs]
        blocks.append(_note_block(new_note("add", "articulo", day)))
        work.blocks[at:at] = blocks
        work.reindex()
        result.applied += edits
        result.touched.append(key)
        result.added.append(key)

    _apply_divisions(work, plan.divisions, day, result)
    work.set_last_reform(day)
    work.set_front(articles=len(work.articles))
    work.reindex()
    return result


def _apply_divisions(work: LawDoc, divisions: list[DivisionEdit], day: date, result: Applied) -> None:
    levels = work.division_levels()
    for d in divisions:
        text = f"{normalize_space(d.label)} · {normalize_space(d.name)}" if d.name.strip() else normalize_space(d.label)
        if d.operation == "add":
            key = article_key(d.before_article or "")
            art = work.article(key) if key else None
            if art is None or d.kind not in levels:
                result.reviews.append(
                    Review(d.before_article or d.label, f"new heading not placed: {text}", "validator")
                )
                continue
            at = art.heading
            # After other headings already inserted right before this article (Título, then Capítulo...).
            heading = f"{'#' * levels[d.kind]} {escape_md(text)}"
            noun, gender = NOUNS[d.kind]
            work.blocks[at:at] = [heading, _note_block(f"{noun} adicionad{gender} DOF {stamp(day)}")]
            work.reindex()
            result.divisions.append(d)
        elif d.operation == "rename":
            target = fold(normalize_space(d.current_heading or d.label))
            found = [
                h for h in work.headings if h.kind and h.index < work.trans_start and fold(h.text).startswith(target)
            ]
            if len(found) != 1:
                result.reviews.append(
                    Review(d.current_heading or d.label, f"heading to rename not found uniquely: {text}", "validator")
                )
                continue
            h = found[0]
            work.blocks[h.index] = f"{'#' * h.level} {escape_md(text)}"
            nxt = h.index + 1
            if nxt < len(work.blocks) and block_kind(work.blocks[nxt]) == "note":
                old = note_text(work.blocks[nxt]).rstrip(".")
                if fold(old.split(". ")[-1]).startswith("denominacion reformada"):
                    work.blocks[nxt] = _note_block(f"{old}, {stamp(day)}")
                else:
                    work.blocks[nxt] = _note_block(f"{old}. Denominación reformada DOF {stamp(day)}")
            else:
                work.blocks[nxt:nxt] = [_note_block(f"Denominación reformada DOF {stamp(day)}")]
            work.reindex()
            result.divisions.append(d)


# --------------------------------------------------------------------------
# Transitorios
# --------------------------------------------------------------------------


def transitorios_blocks(decree: Decree, sections: list[Section]) -> list[str]:
    """The decree's block at the end of the law, the way the Cámara writes it."""
    title = decree.official_title
    blocks = [
        f"### {escape_md(title)}",
        _note_block_plain(f"Publicado en el Diario Oficial de la Federación el {long_date(decree.day)}"),
    ]
    for s in sections:
        if s.label:
            m = ORDINAL_ARTICLE_RE.match(s.instruction)
            rest = m.group("rest") if m else s.instruction
            blocks.append(f"**{escape_md(s.label)}** {escape_md(rest)}".rstrip())
        else:
            blocks.append(escape_md(s.instruction))
        blocks.append("………")
    for p in decree.transitorios:
        if p.is_table:
            blocks.append(render_table(p.rows or []))
            continue
        if TRANSITORIOS_RE.match(p.text):
            blocks.append(f"**{escape_md(p.text)}**")
            continue
        m = ORDINAL_ARTICLE_RE.match(p.text)
        if m:
            blocks.append(f"**{escape_md(m.group('label').strip())}** {escape_md(m.group('rest').strip())}".rstrip())
            continue
        blocks.append(escape_md(p.text))
    return blocks


def _note_block_plain(text: str) -> str:
    return f"*{escape_md(text)}*"


def append_transitorios(doc: LawDoc, blocks: list[str]) -> None:
    has_reform_heading = any(
        h.level == 2 and REFORM_TRANSITORIOS_RE.match(h.text) for h in doc.headings if h.index >= doc.trans_start
    )
    if not has_reform_heading:
        doc.blocks.append("## ARTÍCULOS TRANSITORIOS DE DECRETOS DE REFORMA")
    doc.blocks.extend(blocks)
    doc.reindex()
