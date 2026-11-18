"""The prompt that turns a decree and the current text of a law into structured edits."""

from __future__ import annotations

from dataclasses import dataclass, field

from .decree import DecreeArticle
from .instructions import Ref
from .lawdoc import block_kind, plain

PROMPT_VERSION = "2026-11-17.1"

SYSTEM = """\
You apply reform decrees published in Mexico's Diario Oficial de la Federación (DOF) to the \
consolidated text of a federal law. A lawyer will review your work before it is merged, and \
an automatic audit will later compare it, word by word, with the official consolidated text \
published by the Cámara de Diputados. Precision matters more than coverage: never guess.

You do not rewrite the law. You return a list of EDITS, one per unit that the decree \
changes, and a program applies them.

INPUT
- The decree's operative instruction for this law ("Se reforman los artículos ...; se \
adicionan ...; se derogan ..., para quedar como sigue:").
- For each affected article: the DECREE TEXT, verbatim from the DOF, one line per paragraph; \
and the CURRENT TEXT of that article in the consolidated law, as numbered blocks [1], [2], ... \
(the article label itself is the heading and is not a block). Blocks marked NOTE are editorial \
notes of the Cámara (e.g. "Fracción reformada DOF 01-05-2019"); never edit them, never copy \
them, the program maintains them.

HOW TO READ THE DECREE TEXT
- Only the units named in the instruction change. The decree reproduces them with their new \
text and uses placeholders for everything that stays as it is.
- A line that is only "...", "…" or "……" stands for one unchanged paragraph (or unchanged \
unit) of the current text, in order. "I. a III. ..." stands for fracciones I to III \
unchanged; "a) a c) ..." for incisos a) to c) unchanged; "A. ..." for an unchanged apartado; \
"Artículo 27. ..." means the opening paragraph of article 27 is unchanged. Use the \
placeholders to align the decree text with the numbered blocks of the current text.
- Paragraph counting follows Mexican drafting practice: "párrafo segundo" is the second \
paragraph of the article, counting the paragraphs of the body of the article; fracciones \
and their incisos are not counted as párrafos of the article. When the instruction and the \
placeholders disagree, trust the alignment of the placeholders and report the doubt.

EDITS
Return one edit per unit (one per párrafo, fracción, inciso...), never one edit for several \
separate units:
- operation "reform": the unit exists and gets new text. Give from_block and to_block: the \
first and last numbered blocks of the unit in the CURRENT TEXT (a unit spans several blocks \
only when it has its own paragraphs or incisos that the decree also reproduces).
- operation "add": a new unit inside an existing article. Give insert_after: the number of the \
block after which the new unit goes (0 means before the first block). When several units are \
added at the same place, list them in reading order with the same insert_after.
- operation "repeal": the unit is repealed. Give from_block and to_block as for a reform and, \
as paragraphs, exactly the text the decree prints for it (usually "III. Se deroga." or \
"Se deroga."). If the decree prints nothing for it, use the label of the unit followed by \
"Se deroga." (for a whole article, just "Se deroga.").
- A whole new article: operation "add", unit_type "articulo", article = its label as in \
the decree (e.g. "Artículo 64 Quáter."), insert_after null, and all its paragraphs.
- A whole existing article rewritten by the decree (no placeholders at all): operation \
"reform", unit_type "articulo", from_block 1, to_block = the last block of the article.
- A whole article repealed: operation "repeal", unit_type "articulo", covering all its blocks, \
paragraphs ["Se deroga."] (or the exact text the decree prints).

TEXT OF EACH EDIT ("paragraphs")
- Copy the new text exactly as the decree prints it: same words, accents, punctuation, \
capitalization, numbering style ("III.", "III.-", "a)"), quotation marks and typos. Do not \
modernize, correct or complete anything.
- One string per paragraph. A fracción with its text is one paragraph ("IV. La jornada ..."); \
each inciso is its own paragraph.
- Never include the article label ("Artículo 27.-") in the paragraphs of an existing \
article: the first paragraph of an article is the text that follows the label. For a whole \
new article, likewise start with the text that follows the label.
- Never include placeholders ("...") or editorial notes in the paragraphs.
- A table is one paragraph with one line per row: "| cell | cell |".

RENUMBERING
When the instruction says that existing units move ("recorriéndose las subsecuentes", "se \
recorren en su orden", "pasando la actual X a ser XI"), the consolidated text renumbers them. \
If the decree text does not print the moved units, return one "reform" edit per moved unit \
whose only change is its new number, keeping the rest of its current text exactly as it is. \
Párrafos have no printed number, so moved párrafos need no edit.

DIVISIONS
If the decree adds a Libro, Título, Capítulo or Sección heading, return it in "divisions" \
with operation "add", its kind, label (e.g. "Capítulo III Bis") and name (e.g. "De las \
Sanciones"), and before_article: the label of the first article under it. If the decree \
renames an existing heading, use operation "rename" with current_heading (its current text \
as shown in the CURRENT TEXT context) and the new label and name. Headings that the decree \
only reproduces for orientation, unchanged, are not edits.

WHEN IN DOUBT
If you cannot align the decree text with the current text, the instruction names a unit you \
cannot find, the numbering of the current text does not match what the decree assumes, or \
anything else would require a guess: do not return an edit for that unit. Add an entry to \
"needs_review" with the article and a short, specific reason in Spanish that a lawyer can \
check (e.g. "El decreto reforma el párrafo tercero pero el texto vigente sólo tiene dos \
párrafos"). Skipping a unit and explaining why is a good outcome; a wrong edit is a bad one.

Return only the JSON object required by the schema: {"edits": [...], "divisions": [...], \
"needs_review": [...]}.
"""


@dataclass
class WorkItem:
    key: str
    label: str
    exists: bool
    decree: DecreeArticle | None
    refs: list[Ref] = field(default_factory=list)
    current: list[str] = field(default_factory=list)  # raw Markdown blocks of the article
    neighbours: tuple[str | None, str | None] = (None, None)

    def size(self) -> int:
        return sum(len(b) for b in self.current) + sum(len(x) for x in (self.decree.lines if self.decree else []))


def render_current(blocks: list[str]) -> list[str]:
    lines = []
    for i, block in enumerate(blocks, start=1):
        kind = block_kind(block)
        text = plain(block)
        if kind == "note":
            lines.append(f"[{i}] NOTE (do not edit): {text}")
        elif kind == "heading":
            lines.append(f"[{i}] HEADING: {text.lstrip('#').strip()}")
        else:
            lines.append(f"[{i}] {text}")
    return lines


def build_prompt(
    *,
    law_title: str,
    abbreviation: str,
    decree_title: str,
    decree_date: str,
    instruction: str,
    items: list[WorkItem],
    part: int,
    parts: int,
) -> str:
    out = [
        f"LAW: {law_title} ({abbreviation})",
        f"DECREE: {decree_title}",
        f"PUBLISHED IN THE DOF: {decree_date}",
        "",
        "OPERATIVE INSTRUCTION FOR THIS LAW (verbatim):",
        instruction,
        "",
        f"This request covers {len(items)} article(s) (part {part} of {parts}). Return edits only for these articles.",
    ]
    for item in items:
        out += ["", "=" * 72, f"ARTICLE {item.label}", "=" * 72]
        mentioned = [f"{r.op} {r.detail}".strip() for r in item.refs]
        if mentioned:
            out.append("Named in the instruction as: " + "; ".join(mentioned))
        if item.decree is not None:
            if item.decree.headings:
                out.append("Headings printed by the decree right before this article:")
                out += [f"  {h}" for h in item.decree.headings]
            out.append("DECREE TEXT:")
            out += [f"  {line}" for line in item.decree.lines]
        else:
            out.append("DECREE TEXT: the decree does not reproduce this article; apply only what the instruction says.")
        if item.exists:
            out.append(f"CURRENT TEXT of {item.label} (numbered blocks):")
            out += [f"  {line}" for line in render_current(item.current)]
        else:
            prev, nxt = item.neighbours
            out.append("CURRENT TEXT: this article does not exist yet in the consolidated law (it is new).")
            out.append(
                f"It will be placed after {prev or 'the beginning'} and before {nxt or 'the end of the articles'}."
            )
    return "\n".join(out) + "\n"
