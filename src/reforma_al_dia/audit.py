"""Compare what the AI applied with the official consolidated text.

For every article in play (the ones the AI edited and the ones the official
text changed) the result is one of:

* ``exact``: the legal text is identical, byte for byte, to the official one;
* ``normalized``: identical once whitespace, punctuation, quotes, dashes and
  Markdown escaping are normalized (e.g. "XV. Se deroga" vs "XV. Se deroga.");
* ``mismatch``: anything else, with a word-level diff to inspect;
* ``not_applied``: the decree changed the article but the AI did not apply it
  (it was sent to human review instead of guessed);
* ``outside_decree``: the official text changed an article the decree does not
  name (the Cámara's own corrections, or noise between two source files). It
  is reported and excluded from the AI's accuracy.

Editorial notes ("Fracción reformada DOF ...") are the Cámara's apparatus, not
law, so they are compared separately (``notes_match``).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import asdict, dataclass, field

from .lawdoc import LawDoc, block_kind, plain
from .legal import fold

STATUSES = ["exact", "normalized", "mismatch", "not_applied", "outside_decree"]

_QUOTES = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "«": '"',
        "»": '"',
        "„": '"',
        "‘": "'",
        "’": "'",
        "´": "'",
        "–": "-",
        "\u2014": "-",
        "‐": "-",
        "‑": "-",
        "\xa0": " ",
    }
)
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(_QUOTES).replace("…", "...")
    text = text.replace("**", "").replace("\\", "")
    text = _PUNCT.sub(" ", text)
    return " ".join(text.split())


# A note the source split in two lines leaves a fragment like "22-08-1996, 15-09-2024": apparatus, not law.
_NOTE_FRAGMENT = re.compile(r"^[\s,.;]*(?:DOF\s*)?\d{2}-\d{2}-\d{4}(?:[\s,.;y]+(?:DOF\s*)?\d{2}-\d{2}-\d{4})*[\s.]*$")


def _is_note(block: str) -> bool:
    return block_kind(block) == "note" or bool(_NOTE_FRAGMENT.match(plain(block)))


def legal_blocks(blocks: list[str]) -> list[str]:
    return [b for b in blocks if not _is_note(b)]


def note_blocks(blocks: list[str]) -> list[str]:
    return [plain(b) for b in blocks if _is_note(b)]


def word_diff(old: list[str], new: list[str]) -> list[list[str]]:
    """Word-level diff of two lists of paragraphs: ``[["=", "text"], ["-", "old"], ["+", "new"]]``."""
    a = " ¶ ".join(old).split()
    b = " ¶ ".join(new).split()
    ops: list[list[str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops.append(["=", " ".join(a[i1:i2])])
        else:
            if i2 > i1:
                ops.append(["-", " ".join(a[i1:i2])])
            if j2 > j1:
                ops.append(["+", " ".join(b[j1:j2])])
    return ops


def compress(ops: list[list[str]], context: int = 12) -> list[list[str]]:
    """Shorten long unchanged runs to their first and last words."""
    out = []
    for i, (op, text) in enumerate(ops):
        words = text.split()
        if op == "=" and len(words) > 2 * context + 3:
            head = " ".join(words[:context]) if i > 0 else ""
            tail = " ".join(words[-context:]) if i < len(ops) - 1 else ""
            text = " … ".join(x for x in (head, tail) if x) if head or tail else "…"
            if i == 0:
                text = "… " + text
            elif i == len(ops) - 1:
                text = text + " …"
        out.append([op, text])
    return out


# A repealed unit printed as a placeholder: "Se deroga.", "III. Se deroga", "Cuarto párrafo. Se deroga.", "Derogado".
_PLACEHOLDER = re.compile(
    r"^(?:[IVXLC]+(?:\s+(?:Bis|Ter|Qu[aá]ter))?\s*[.\-)]+\s*|[a-zñ]\)\s*|\w+\s+p[aá]rrafo\.?\s*)?"
    r"\(?(?:se\s+deroga|derogad[oa])\.?\)?\.?$",
    re.IGNORECASE,
)
_LABEL = re.compile(r"^(?:[IVXLC]+(?:\s+(?:Bis|Ter|Qu[aá]ter)(?:\s+\d+)?)?|[a-zñ])\s*[.\-)]+\s*")
_ANNOTATION = re.compile(
    r"DOF\s+\d{2}-\d{2}-\d{4}|SCJN|declarad[oa]s?\s+inv[aá]lid|actualizad[oa]s?\s+por", re.IGNORECASE
)
CAUSES = ["repeal_placeholder", "numbering", "official_annotation", "text"]


def mismatch_cause(ai: list[str], official: list[str]) -> str:
    """Why two legal texts differ, when it is one of the known, harmless patterns; "text" otherwise.

    * ``repeal_placeholder``: they differ only in "Se deroga" / "Derogado" placeholders, which
      the decree prints and the Cámara sometimes drops from the consolidated text;
    * ``numbering``: they differ only in unit labels ("X." vs "XIII."): a renumbering;
    * ``official_annotation``: the official text adds only Cámara annotations (SCJN invalidity,
      updated amounts) that are not law;
    * ``text``: anything else. These are the ones that matter.
    """
    a = [plain(x) for x in ai]
    o = [plain(x) for x in official]

    def same(x: list[str], y: list[str]) -> bool:
        return normalize(" ".join(x)) == normalize(" ".join(y))

    if same([p for p in a if not _PLACEHOLDER.match(p.strip())], [p for p in o if not _PLACEHOLDER.match(p.strip())]):
        return "repeal_placeholder"
    if same([_LABEL.sub("", p) for p in a], [_LABEL.sub("", p) for p in o]):
        return "numbering"
    extra = [p for p in o if p not in a]
    if extra and all(_ANNOTATION.search(p) for p in extra) and same(a, [p for p in o if p not in extra]):
        return "official_annotation"
    return "text"


@dataclass
class ArticleAudit:
    key: str
    label: str
    status: str
    notes_match: bool
    before: list[str]
    ai: list[str]
    official: list[str]
    diff: list[list[str]] = field(default_factory=list)
    cause: str | None = None  # for mismatches: one of CAUSES

    def to_json(self, *, texts: bool = True) -> dict:
        data = asdict(self)
        if not texts:
            for k in ("before", "ai", "official"):
                data.pop(k)
        return data


def classify(ai_blocks: list[str], official_blocks: list[str]) -> str:
    a, o = legal_blocks(ai_blocks), legal_blocks(official_blocks)
    if a == o:
        return "exact"
    if normalize(" ".join(plain(x) for x in a)) == normalize(" ".join(plain(x) for x in o)):
        return "normalized"
    return "mismatch"


def compare_articles(
    before: LawDoc,
    ai: LawDoc,
    official: LawDoc,
    *,
    touched: list[str],
    expected: dict[str, str],
) -> list[ArticleAudit]:
    bmap, amap, omap = before.article_map(), ai.article_map(), official.article_map()
    labels = {a.key: a.label for d in (before, official, ai) for a in d.articles}
    changed = [k for k in omap if bmap.get(k) != omap[k]]
    keys = list(dict.fromkeys([*touched, *changed]))
    order = {a.key: i for i, a in enumerate(official.articles)}
    keys.sort(key=lambda k: order.get(k, 10**6))
    out: list[ArticleAudit] = []
    for key in keys:
        b, a, o = bmap.get(key, []), amap.get(key, []), omap.get(key, [])
        texts = ([plain(x) for x in b], [plain(x) for x in a], [plain(x) for x in o])
        if key in touched:
            status = classify(a, o)
        elif key in expected:
            status = "not_applied"
        else:
            status = "outside_decree"
        notes_ok = note_blocks(a) == note_blocks(o)
        diff = []
        if status != "exact":
            source = a if key in touched else b
            diff = compress(word_diff([plain(x) for x in legal_blocks(source)], [plain(x) for x in legal_blocks(o)]))
        cause = mismatch_cause(legal_blocks(a), legal_blocks(o)) if status == "mismatch" else None
        out.append(ArticleAudit(key, labels.get(key, key), status, notes_ok, *texts, diff=diff, cause=cause))
    return out


def decree_block(doc: LawDoc, title: str) -> list[str]:
    """The blocks the Cámara appended for a decree at the end of a law (its title heading to the next one)."""
    want = fold(title).strip(" .")
    start = None
    for i in range(doc.trans_start, len(doc.blocks)):
        b = doc.blocks[i]
        if not b.startswith("### "):
            continue
        got = fold(plain(b[4:])).strip(" .")
        if got == want or difflib.SequenceMatcher(None, got, want).ratio() > 0.92:
            start = i
    if start is None:
        return []
    end = start + 1
    while end < len(doc.blocks) and not doc.blocks[end].startswith("### ") and not doc.blocks[end].startswith("## "):
        end += 1
    return doc.blocks[start:end]


def summarize(articles: list[ArticleAudit]) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for a in articles:
        counts[a.status] += 1
    for cause in CAUSES:
        counts[f"mismatch_{cause}"] = sum(1 for a in articles if a.status == "mismatch" and a.cause == cause)
    counts["notes_match"] = sum(
        1 for a in articles if a.status in ("exact", "normalized", "mismatch") and a.notes_match
    )
    return counts
