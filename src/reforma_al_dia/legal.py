"""Vocabulary of Mexican legislative text, shared with leyes-mx.

The regular expressions and Markdown helpers below are vendored from
``leyes_mx.parse`` (MIT, same author) so that every paragraph this project
writes into a law is byte-compatible with what the leyes-mx parser writes when
it converts the official consolidated text. If they drifted, the audit would
count formatting noise as AI mistakes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Article labels
# --------------------------------------------------------------------------

SUFFIX_WORD = (
    r"(?:bis|ter|qu[áa]ter|quintus|quinquies|sextus|sexies|s[eé]ptimus|septies|octavus|octies|"
    r"nonus|nonies|novies|d[eé]cimus|decies|undecies|duodecies|terdecies|quaterdecies|quindecies)"
)
_SEP_AHEAD = r"(?=\s*(?:\.|[-–]|\s" + SUFFIX_WORD + r"\b|$))"
_LETTER_SUFFIX = r"(?:\.?-|\s+-\s*|\s+)[A-Za-zÑñ](?![A-Za-záéíóúñÑ])" + _SEP_AHEAD

ARTICLE_RE = re.compile(
    r"^(?P<kw>art[ií]culos?|art\.)\s*"
    r"(?P<num>\d+)(?P<ord>[oº°ª]|\s+[º°ª])?"
    r"(?P<suf>(?:" + _LETTER_SUFFIX + r"|(?:\.?\s+|\s*-\s*)" + SUFFIX_WORD + r"\b(?:\s+\d+(?=\s*[.\-–]))?)*)"
    r"\s*(?P<sep>\.\s*[-–]|[-–]|\.|:)"
    r"\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

ARTICLE_ALONE_RE = re.compile(
    r"^(?P<kw>art[ií]culo)\s+(?P<num>\d+)(?P<ord>[oº°ª])?"
    r"(?P<suf>(?:" + _LETTER_SUFFIX + r"|(?:\.?\s+|\s*-\s*)" + SUFFIX_WORD + r"\b)*)\s*\.?\s*$",
    re.IGNORECASE,
)

ARTICLE_RANGE_RE = re.compile(
    r"^(?P<kw>art[ií]culos)\s+(?P<a>\d+)\s*[oº°]?\.?\s*(?:a|al|y)\s+(?P<b>\d+)\s*[oº°]?"
    r"\s*(?P<sep>\.\s*[-–]|[-–]|\.)\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

ORDINAL_WORD = (
    r"(?:[uú]nic[oa]|primer[oa]?|segund[oa]|tercer[oa]?|cuart[oa]|quint[oa]|sext[oa]|"
    r"s[eé]ptim[oa]|octav[oa]|noven[oa]|d[eé]cim[oa]|und[eé]cim[oa]|duod[eé]cim[oa]|"
    r"vig[eé]sim[oa]|trig[eé]sim[oa]|cuadr[aá]g[eé]sim[oa]|quincuag[eé]sim[oa]|"
    r"sexag[eé]sim[oa]|septuag[eé]sim[oa]|octog[eé]sim[oa]|nonag[eé]sim[oa]|"
    r"cent[eé]sim[oa]|bis|ter)"
)

ORDINAL_ARTICLE_RE = re.compile(
    r"^(?P<label>(?:(?:art[ií]culos?|transitorios?)\s+)?"
    + ORDINAL_WORD
    + r"(?:[\s-]+"
    + ORDINAL_WORD
    + r")*(?:\s+transitorios?)?\s*(?:\.\s*[-–]|[-–]|\.|:))\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

_ROMAN = r"(?=[IVXLCDM])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_DIVISION_ORDINAL = (
    r"(?:" + _ROMAN + r"|\d+|" + ORDINAL_WORD + r"|preliminar|[uú]nico|[uú]nica|"
    r"primera|segunda|tercera|cuarta|quinta|sexta|s[eé]ptima|octava|novena|d[eé]cima"
    r"|vig[eé]sima|trig[eé]sima)"
)
DIVISION_RE = re.compile(
    r"^(?P<kind>libro|parte|t[ií]tulo|cap[ií]tulo|secci[oó]n)\s+"
    r"(?P<ord>" + _DIVISION_ORDINAL + r"(?:[\s-]+(?:" + _DIVISION_ORDINAL + r"))*"
    r"(?:\s+(?:bis|ter|qu[áa]ter|quinquies|sexies|[A-Z]))*)\b"
    r"\s*(?P<sep>\.\s*[-–]|[-–]|\.|:)?\s*(?P<name>.*)$",
    re.IGNORECASE | re.DOTALL,
)

TRANSITORIOS_RE = re.compile(
    r"^(?:art[ií]culos?\s+)?(?:transitorios?|disposiciones\s+transitorias)\s*[.:]?$",
    re.IGNORECASE,
)
REFORM_TRANSITORIOS_RE = re.compile(r"^art[ií]culos\s+transitorios\s+de\s+decretos\s+de\s+reforma", re.IGNORECASE)


def match_article(text: str) -> re.Match | None:
    """A body article label. It must be capitalized: "artículo 27 de..." is prose."""
    if not text[:1].isupper():
        return None
    return ARTICLE_RE.match(text) or ARTICLE_ALONE_RE.match(text)


# --------------------------------------------------------------------------
# Folding and keys
# --------------------------------------------------------------------------


def fold(text: str) -> str:
    """Lowercase and strip accents, for keyword comparisons."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def normalize_space(text: str) -> str:
    text = text.replace("\xa0", " ").replace("​", "")
    return re.sub(r"[ \t]+", " ", text).strip()


_SUFFIX_SYNONYMS = {"quinquies": "quintus", "sexies": "sextus", "septies": "septimus", "octies": "octavus"}


def suffix_words(suffix: str) -> list[str]:
    folded = fold(suffix.replace("Ñ", "ñ").replace("ñ", "~")).replace("~", "ñ")
    words = folded.replace("-", " ").replace(".", " ").split()
    return [_SUFFIX_SYNONYMS.get(w, w) for w in words]


def article_key(label: str) -> str | None:
    """Canonical key of an article label: ``Artículo 64 Quáter.`` -> ``64 quater``.

    Ordinal marks and separators are dropped, so ``3o.``, ``3º`` and ``3`` share
    a key, and ``15-A``, ``15 A`` and ``15-a`` do too.
    """
    text = normalize_space(label.replace("\\", ""))
    if not text.endswith((".", "-", ":")):
        text += "."
    m = ARTICLE_RE.match(text) or ARTICLE_ALONE_RE.match(text)
    if not m:
        return None
    return " ".join([m.group("num"), *suffix_words(m.group("suf") or "")])


_SUFFIX_RANK = {
    "bis": 2,
    "ter": 3,
    "quater": 4,
    "quintus": 5,
    "sextus": 6,
    "septimus": 7,
    "octavus": 8,
    "nonus": 9,
    "nonies": 9,
    "novies": 9,
    "decimus": 10,
    "decies": 10,
}
_ALPHABET = "abcdefghijklmn~opqrstuvwxyz"


def key_order(key: str) -> tuple:
    """Sort key for an article key: 29 < 29 bis < 29 ter < 29 a < 29 a bis < 29 b < 30."""
    number, *words = key.split()
    rank: list[tuple[int, int]] = []
    for word in words:
        word = word.replace("ñ", "~")
        if word.isdigit():
            rank.append((2, int(word)))
        elif word in _SUFFIX_RANK:
            rank.append((0, _SUFFIX_RANK[word]))
        elif len(word) == 1:
            rank.append((1, _ALPHABET.find(word) + 1))
        else:
            rank.append((3, 0))
    return (int(number), tuple(rank))


# --------------------------------------------------------------------------
# Editorial notes ("Párrafo reformado DOF 15-01-2026")
# --------------------------------------------------------------------------

_NOTE_DATE = re.compile(r"DOF\s+\d{2}-\d{2}-\d{4}")
_NOTE_SUBJECTS = {
    "artículo", "artículos", "fracción", "fracciones", "párrafo", "párrafos", "inciso", "incisos",
    "subinciso", "sub-inciso", "numeral", "numerales", "apartado", "apartados", "base", "bases",
    "capítulo", "título", "sección", "libro", "denominación", "epígrafe", "fe", "reforma", "derogado",
    "derogada", "suspensión", "último", "penúltimo", "encabezado", "rubro", "tabla", "anexo", "nota",
    "declaratoria", "segmento", "parte", "reubicado", "recorrido", "adicionado", "reformado",
    "transitorio", "porción", "oración", "la", "el", "los", "las", "se",
}  # fmt: skip
_NOTE_VERBS = re.compile(
    r"reformad|adicionad|derogad|recorrid|reubicad|original|vinculad|publicad|erratas|"
    r"reforma\s+DOF|suspensi|declarad|invalid|abrogad|modificad|aclaraci",
    re.IGNORECASE,
)


def is_note(text: str) -> bool:
    """True for the Cámara's editorial notes, e.g. ``Párrafo reformado DOF 15-01-2026``."""
    if len(text) > 600 or "(sic" in text or not _NOTE_DATE.search(text):
        return False
    first = text.split(" ", 1)[0].strip(".,:;").lower()
    if first not in _NOTE_SUBJECTS:
        return False
    if ARTICLE_RE.match(text) or ARTICLE_RANGE_RE.match(text):
        return False
    return bool(_NOTE_VERBS.search(text))


# --------------------------------------------------------------------------
# Markdown escaping, exactly as leyes-mx writes it
# --------------------------------------------------------------------------

_MD_INLINE = re.compile(r"([\\`*_$])")
_MD_BLOCK_START = re.compile(r"^(#|>|[-+=]|\d+[.)](?=\s|$)|\|)")


def escape_md(text: str) -> str:
    """Escape the few characters that would change how a paragraph renders."""
    text = _MD_INLINE.sub(r"\\\1", text)
    text = re.sub(r"<(?=[A-Za-z/!])", "&lt;", text)
    m = _MD_BLOCK_START.match(text)
    if m:
        token = m.group(1)
        if token[0].isdigit():
            text = token[:-1] + "\\" + text[len(token) - 1 :]
        else:
            text = "\\" + text
    return text


_UNESCAPE = re.compile(r"\\([\\`*_$#>+=|.)\-])")


def unescape_md(text: str) -> str:
    """Inverse of :func:`escape_md` for display to a reader (or a model)."""
    return _UNESCAPE.sub(r"\1", text).replace("&lt;", "<")


# --------------------------------------------------------------------------
# Article numbering, as ``leyes validate`` checks it
# --------------------------------------------------------------------------


@dataclass
class Numbering:
    articles: int
    missing: list[int]
    duplicates: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.duplicates


def check_numbering(labels: list[str]) -> Numbering:
    """Body articles must run 1..N with no gaps and no repeats (repealed ones stay as "Se deroga.")."""
    seen: set[str] = set()
    numbers: set[int] = set()
    duplicates: list[str] = []
    for label in labels:
        m = ARTICLE_RANGE_RE.match(label)
        if m and label.lower().startswith("artículos"):
            numbers.update(range(int(m.group("a")), int(m.group("b")) + 1))
            continue
        key = article_key(label)
        if key is None:
            continue
        if key in seen:
            duplicates.append(label)
        seen.add(key)
        numbers.add(int(key.split()[0]))
    highest = max(numbers, default=0)
    missing = [n for n in range(1, highest + 1) if n not in numbers]
    return Numbering(len(labels), missing, duplicates)


def _escape_cell(text: str) -> str:
    parts = [escape_md(normalize_space(p)) for p in text.split("\n") if p.strip()]
    return "<br>".join(parts).replace("|", "\\|")


def render_table(rows: list[list[str]]) -> str:
    """A table as leyes-mx renders it: an empty header row, then every row of the source."""
    width = max((len(r) for r in rows), default=0)
    if width == 0:
        return ""
    if width == 1:
        lines = []
        for row in rows:
            for part in row[0].split("\n"):
                part = normalize_space(part)
                lines.append(f"> {escape_md(part)}" if part else ">")
        while lines and lines[-1] == ">":
            lines.pop()
        return "\n".join(lines)
    out = ["| " + " | ".join([" "] * width) + " |", "|" + "---|" * width]
    for row in rows:
        cells = [_escape_cell(c) for c in row] + [""] * (width - len(row))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre"]  # fmt: skip


def long_date(day) -> str:
    """``15 de diciembre de 2025``, the way the Cámara writes publication dates."""
    return f"{day.day} de {MONTHS[day.month - 1]} de {day.year}"
