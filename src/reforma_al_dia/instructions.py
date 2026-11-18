"""Parse the operative instruction of a decree into (operation, article, detail).

    Se reforman los artículos 3o., párrafo segundo; 27, fracción III; y 28 Bis;
    se adiciona una fracción IV Bis al artículo 30; y se deroga el artículo 41,
    de la Ley X, para quedar como sigue:

becomes ``reform 3 "párrafo segundo"``, ``reform 27 "fracción III"``,
``reform 28 bis``, ``add 30 "una fracción IV Bis"`` and ``repeal 41``.

This parser is deterministic and deliberately simple. It does not apply
anything: it tells the pipeline which articles a decree says it touches, so
that an article named in the instruction but left untouched by the model is
reported for human review, and so the audit can tell a real miss from noise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .catalog import Law, find_laws
from .decree import instruction_scope
from .legal import SUFFIX_WORD, fold, suffix_words

_VERB = re.compile(r"\bse\s+(reforma|adiciona|deroga|modifica|declara\w*\s+reformad\w*)n?\b", re.IGNORECASE)
_OPS = {"reforma": "reform", "modifica": "reform", "adiciona": "add", "deroga": "repeal"}
_NUM = re.compile(
    r"(?<![\w.,])(?P<num>\d{1,4})(?:\s*[oº°]\.?)?"
    r"(?P<suf>(?:\s*-\s*[A-ZÑ](?![a-záéíóúñ])|\s+" + SUFFIX_WORD + r"\b(?:\s+\d{1,2}\b)?)*)",
    re.IGNORECASE,
)
_NOT_ARTICLE_BEFORE = re.compile(r"(?:numeral(?:es)?|n[uú]mero|punto|anexo|apartado|base|inciso|tabla)\s*$", re.I)
_NOT_ARTICLE_AFTER = re.compile(r"^\s*(?:d[ií]as|por\s+ciento|%|a[nñ]os|meses|horas|de\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre))", re.I)  # fmt: skip


@dataclass
class Ref:
    op: str  # reform, add, repeal
    key: str  # article key, e.g. "28 bis"
    detail: str  # what the instruction says about the article, e.g. "fracción III"


def _strip_law_names(text: str, laws: list[Law]) -> str:
    out = text
    for m in sorted(find_laws(text, laws), key=lambda x: x.start, reverse=True):
        out = out[: m.start] + "LEY" + out[m.end :]
    # Names of laws outside the catalog: "de la Ley ...", "del Código ..." up to the next separator.
    out = re.sub(r"\b(?:Ley|C[oó]digo|Constituci[oó]n)\b[^;:]*", "LEY", out)
    return out


def parse_instruction(text: str, laws: list[Law]) -> list[Ref]:
    body = instruction_scope(text)
    body = _strip_law_names(body, laws)
    verbs = list(_VERB.finditer(body))
    refs: list[Ref] = []
    for i, v in enumerate(verbs):
        op = next((code for word, code in _OPS.items() if fold(v.group(1)).startswith(word)), "reform")
        if fold(v.group(1)).startswith("declara"):
            op = "reform"
        segment = body[v.end() : verbs[i + 1].start() if i + 1 < len(verbs) else len(body)]
        refs.extend(_segment_refs(op, segment))
    seen: set[tuple[str, str, str]] = set()
    unique = []
    for r in refs:
        if (r.op, r.key, r.detail) not in seen:
            seen.add((r.op, r.key, r.detail))
            unique.append(r)
    return unique


def _segment_refs(op: str, segment: str) -> list[Ref]:
    matches = []
    for m in _NUM.finditer(segment):
        if _NOT_ARTICLE_BEFORE.search(segment[: m.start()]) or _NOT_ARTICLE_AFTER.match(segment[m.end() :]):
            continue
        matches.append(m)
    refs: list[Ref] = []
    for j, m in enumerate(matches):
        key = " ".join([m.group("num"), *suffix_words(m.group("suf") or "")])
        before = segment[matches[j - 1].end() if j else 0 : m.start()]
        after = segment[m.end() : matches[j + 1].start() if j + 1 < len(matches) else len(segment)]
        detail = _detail(before, after)
        # "artículos 140 a 145": expand short ranges of plain numbers.
        if j and re.fullmatch(r"\s*(?:a|al)\s*", before) and not m.group("suf") and refs:
            first = int(refs[-1].key.split()[0])
            last = int(m.group("num"))
            if refs[-1].key.isdigit() and 0 < last - first < 200:
                refs.extend(Ref(op, str(n), "") for n in range(first + 1, last + 1))
                continue
        refs.append(Ref(op, key, detail))
    return refs


def _detail(before: str, after: str) -> str:
    """Unit words around an article number: "fracción III" in "27, fracción III;"."""
    after_part = re.split(r";|\by\s+(?=(?:los|el|la|las)?\s*(?:art[ií]culos?\s+)?\d)", after, maxsplit=1)[0]
    after_part = after_part.strip(" ,;y")
    before_part = re.split(r";", before)[-1]
    before_part = re.sub(r"\b(?:los|el|la|las|del|de|al|a)?\s*art[ií]culos?\s*$", "", before_part, flags=re.I)
    before_part = before_part.strip(" ,;y")
    detail = " ".join(x for x in (before_part, after_part) if x)
    detail = re.sub(r"\s+", " ", detail)
    detail = re.sub(r"^(?:y|e|así como)\s+", "", detail, flags=re.I)
    detail = re.sub(r"\s*\b(?:del|de\s+la|a\s+la|al|a)?\s*LEY\b.*$", "", detail)
    detail = re.sub(r"\s*\b(?:los|el|la|las)\s+art[ií]culos?\s*$", "", detail, flags=re.I)
    detail = re.sub(r"^(?:un|una|unos|unas|a|al|y)$", "", detail.strip(), flags=re.I)
    detail = re.sub(r"^(?:un|una)\s+(?=a\b)", "", detail, flags=re.I).strip(" ,;")
    return detail[:160]


def keys(refs: list[Ref]) -> list[str]:
    out: list[str] = []
    for r in refs:
        if r.key not in out:
            out.append(r.key)
    return out
