"""Deterministic checks between the law before and after applying a plan.

The model is trusted for nothing it can be checked on. Before an edit is
applied its alignment is checked (a reform of "fracción III" must replace a
block that starts with "III."). After the plan is applied the whole law is
compared with its previous version: no article outside the plan may change,
the article count may only grow by the articles the decree adds, no heading
may disappear, and numbering must stay valid as ``leyes validate`` checks it.
Every article the decree names must end up either edited or on the review list.
"""

from __future__ import annotations

import re

from .applier import Applied
from .edits import Edit, Review
from .lawdoc import LawDoc, block_kind, plain
from .legal import article_key, check_numbering

_SUFFIX = r"(?:\s+(?:Bis|Ter|Qu[aá]ter|Quintus|Quinquies|Sextus|Sexies|S[eé]ptimus|Octavus)(?:\s+\d+)?)?"
_ROMAN_LABEL = re.compile(r"^([IVXLC]+" + _SUFFIX + r")\s*(?:\.|-|\.-|\))(?:\s|$)")
_LETTER_LABEL = re.compile(r"^([a-zñ]{1,2}" + _SUFFIX + r")\)\s")
_ROMAN_IN_UNIT = re.compile(r"\b([IVXLC]+" + _SUFFIX + r")\b")
_LETTER_IN_UNIT = re.compile(r"\binciso\s+([a-zñ]{1,2}" + _SUFFIX + r")\)", re.IGNORECASE)


def unit_label(text: str) -> str | None:
    """The numbering label a paragraph starts with: ``III``, ``IV Bis``, ``a``."""
    text = text.strip()
    m = _ROMAN_LABEL.match(text) or _LETTER_LABEL.match(text)
    return re.sub(r"\s+", " ", m.group(1)).lower() if m else None


def _strip_label(text: str) -> str:
    m = _ROMAN_LABEL.match(text.strip()) or _LETTER_LABEL.match(text.strip())
    rest = text.strip()[m.end() :] if m else text
    return " ".join(re.sub(r"[^\w\s]", " ", rest).split())


def _renumbered(old: str, new: str) -> bool:
    """A unit that only moved ("recorriéndose"): same text, new number."""
    return _strip_label(old) == _strip_label(new)


def check_alignment(doc: LawDoc, edit: Edit) -> str | None:
    """Why an edit looks misaligned with the current text, or None if it looks right."""
    key = article_key(edit.article)
    art = doc.article(key) if key else None
    if art is None or (edit.operation == "add" and edit.unit_type == "articulo"):
        return None
    content = doc.blocks[art.start : art.end]
    new_label = unit_label(edit.paragraphs[0]) if edit.paragraphs else None
    if edit.operation in ("reform", "repeal") and edit.from_block is not None and 1 <= edit.from_block <= len(content):
        old = content[edit.from_block - 1]
        if block_kind(old) == "note":
            return f"{edit.unit}: the range starts on an editorial note"
        old_label = unit_label(plain(old))
        relabeled = (
            edit.unit_type in ("fraccion", "inciso", "subinciso") and old_label and new_label and old_label != new_label
        )
        if relabeled and not _renumbered(plain(old), edit.paragraphs[0]):
            return f"{edit.unit}: the decree text starts with «{new_label}» but block [{edit.from_block}] starts with «{old_label}»"
        if edit.unit_type == "parrafo" and old_label and not new_label and edit.operation == "reform":
            return f"{edit.unit}: block [{edit.from_block}] is a numbered unit («{old_label}»), not a párrafo"
    if edit.unit_type == "fraccion" and new_label and "antes" not in edit.unit.lower():
        tail = re.split(r"fracci[oó]n(?:es)?", edit.unit)[-1]
        found = _ROMAN_IN_UNIT.findall(tail)
        if found and found[-1].lower() != new_label and edit.operation != "repeal":
            return f"{edit.unit}: the new text is numbered «{new_label}»"
    if edit.unit_type == "inciso" and new_label:
        m = _LETTER_IN_UNIT.search(edit.unit)
        if m and m.group(1).lower() != new_label:
            return f"{edit.unit}: the new text is lettered «{new_label}»"
    return None


def validate(before: LawDoc, applied: Applied, expected: dict[str, str]) -> list[Review]:
    """Problems with an applied plan. ``expected`` maps article keys the decree names to their labels."""
    after = applied.doc
    reviews: list[Review] = []
    bmap, amap = before.article_map(), after.article_map()
    touched = set(applied.touched)

    for key, blocks in bmap.items():
        if key not in amap:
            reviews.append(Review(key, "an article disappeared from the law", "validator"))
        elif key not in touched and amap[key] != blocks:
            reviews.append(Review(key, "an article changed without an edit for it", "validator"))

    expected_count = len(before.articles) + len(applied.added)
    if len(after.articles) != expected_count:
        reviews.append(Review("ley", f"article count is {len(after.articles)}, expected {expected_count}", "validator"))

    def division_headings(doc: LawDoc) -> list[str]:
        return [h.text for h in doc.headings if h.kind and h.index < doc.trans_start]

    old_heads, new_heads = division_headings(before), division_headings(after)
    renamed = sum(1 for d in applied.divisions if d.operation == "rename")
    missing = [h for h in old_heads if h not in new_heads]
    if len(missing) > renamed:
        reviews.append(Review("ley", f"headings disappeared: {missing[:3]}", "validator"))

    nb = check_numbering([a.label for a in before.articles])
    na = check_numbering([a.label for a in after.articles])
    new_dupes = sorted(set(na.duplicates) - set(nb.duplicates))
    new_gaps = sorted(set(na.missing) - set(nb.missing))
    if new_dupes or new_gaps:
        reviews.append(
            Review("ley", f"article numbering broken: duplicates {new_dupes[:5]}, gaps {new_gaps[:5]}", "validator")
        )

    for key, label in expected.items():
        if key not in touched:
            reviews.append(Review(label, "the decree names this article but no edit was applied to it", "pipeline"))
    return reviews
