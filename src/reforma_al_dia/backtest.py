"""Backtest: replay real past reforms and score the result against the official text.

For each pair of consecutive leyes-mx versions with exactly one decree in
between, the pipeline starts from the earlier version and the DOF text of the
decree, exactly as it would have on the morning of publication, and the
result is compared with the later version, the Cámara's official consolidation.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import __version__
from .audit import classify, compare_articles, decree_block, summarize
from .catalog import Law
from .claude import Claude
from .decree import Decree, parse_decree
from .history import Pair, law_at, match_note, single_decree_pairs
from .instructions import parse_instruction
from .lawdoc import LawDoc
from .pipeline import LawRun, expected_articles, run_law, work_items
from .prompt import PROMPT_VERSION
from .sidof import Sidof, note_url

log = logging.getLogger(__name__)

NOISE_LIMIT = 10  # official changes outside the decree tolerated in a backtest pair


@dataclass
class Selection:
    pair: Pair
    reason: str | None  # None when selected


def screen(root: Path, laws: list[Law], sidof: Sidof, since: date, until: date | None = None) -> list[Selection]:
    """Every single-decree pair since ``since``, with the reason it is skipped, if it is."""
    out: list[Selection] = []
    for law in laws:
        for pair in single_decree_pairs(root, law.slug):
            if pair.decree_date < since or (until and pair.decree_date > until):
                continue
            out.append(Selection(pair, _skip_reason(root, law, laws, sidof, pair)))
    return sorted(out, key=lambda s: (s.pair.decree_date, s.pair.slug))


def _skip_reason(root: Path, law: Law, laws: list[Law], sidof: Sidof, pair: Pair) -> str | None:
    note = match_note(sidof, pair.decree_date, pair.decree_title)
    if note is None:
        return "decree not found in the SIDOF edition of its date"
    pair.note_code = note.code
    full = sidof.note(note.code)
    decree = parse_decree(full.code, full.day, full.title, full.html, laws)
    sections = decree.sections_for(law.slug)
    if not sections:
        return "the decree does not amend the articles of this law (e.g. it amends transitorios of an earlier decree)"
    before = LawDoc.parse(law_at(root, pair.before, pair.slug))
    official = LawDoc.parse(law_at(root, pair.after, pair.slug))
    refs = [r for s in sections for r in parse_instruction(s.instruction, laws)]
    expected = expected_articles(work_items(before, sections, refs))
    bmap, omap = before.article_map(), official.article_map()
    changed = [k for k in omap if bmap.get(k) != omap[k]]
    if not changed:
        return "the official text does not change any article (only transitorios or headers)"
    outside = [k for k in changed if k not in expected]
    if len(outside) > NOISE_LIMIT:
        return f"the two official versions differ in {len(outside)} articles the decree does not name (source noise)"
    return None


def run_case(
    root: Path,
    law: Law,
    laws: list[Law],
    pair: Pair,
    sidof: Sidof,
    claude: Claude,
    *,
    workers: int = 4,
) -> dict:
    assert pair.note_code is not None
    note = sidof.note(pair.note_code)
    decree = parse_decree(note.code, note.day, note.title, note.html, laws)
    before = LawDoc.parse(law_at(root, pair.before, pair.slug))
    official = LawDoc.parse(law_at(root, pair.after, pair.slug))
    run = run_law(before, law, decree, claude, laws, workers=workers)
    return case_record(run, before, official, pair=pair, source="backtest")


def case_record(
    run: LawRun, before: LawDoc, official: LawDoc, *, pair: Pair | None, source: str, extra: dict | None = None
) -> dict:
    decree: Decree = run.decree
    ai = run.doc or before
    touched = run.applied.touched if run.applied else []
    expected = expected_articles(run.items)
    articles = compare_articles(before, ai, official, touched=touched, expected=expected)
    official_block = decree_block(official, decree.title)
    ai_block = decree_block(ai, decree.title)
    trans_status = classify(ai_block, official_block) if official_block and ai_block else "unavailable"
    body_identical = _filler(ai.blocks) == _filler(official.blocks)
    models = sorted({c.model for c in run.calls if c.model})
    record = {
        "id": pair.id if pair else f"{decree.day.isoformat()}-{run.law.slug}",
        "source": source,
        "law": {"slug": run.law.slug, "title": run.law.title, "abbreviation": run.law.abbreviation},
        "decree": {
            "code": decree.code,
            "title": decree.title,
            "date": decree.day.isoformat(),
            "url": note_url(decree.code, decree.day),
            "pdf": pair.decree_pdf if pair else None,
            "instruction": [s.instruction for s in run.sections],
        },
        "base": {"commit": pair.before if pair else None},
        "official": {
            "commit": pair.after if pair else None,
            "date": pair.version_date.isoformat() if pair else None,
            "same_day": pair.same_day if pair else None,
        },
        "pipeline": {"version": __version__, "prompt": PROMPT_VERSION, "models": models},
        "calls": [c.__dict__ for c in run.calls],
        "decree_text": {
            it.key: {"label": it.label, "headings": it.decree.headings, "lines": it.decree.lines}
            for it in run.items
            if it.decree is not None
        },
        "plan": run.plan.to_json(),
        "needs_review": [r.to_json() for r in run.reviews],
        "counts": summarize(articles),
        "transitorios": trans_status,
        "body_identical": body_identical,
        "articles": [a.to_json() for a in articles],
    }
    if extra:
        record.update(extra)
    return record


def _filler(blocks: list[str]) -> list[str]:
    """Blocks with the Cámara's dotted filler lines ("……..", "………") made equal; they vary at random."""
    return ["…" if re.fullmatch(r"[.…]{3,}", b) else b for b in blocks]


def write_case(data_dir: Path, record: dict) -> Path:
    path = data_dir / "casos" / f"{record['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def load_cases(data_dir: Path) -> list[dict]:
    folder = data_dir / "casos"
    if not folder.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


__all__ = [
    "NOISE_LIMIT",
    "Selection",
    "case_record",
    "load_cases",
    "run_case",
    "screen",
    "single_decree_pairs",
    "write_case",
]
