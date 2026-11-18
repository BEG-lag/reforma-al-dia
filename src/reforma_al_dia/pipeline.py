"""From one decree and one law to an amended law: plan, ask the model, apply, validate."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .applier import Applied, append_transitorios, apply_plan, heading_label, transitorios_blocks
from .catalog import Law
from .claude import Claude, ClaudeError
from .decree import Decree, DecreeArticle, Section
from .edits import SCHEMA, Plan, Review
from .instructions import Ref, parse_instruction
from .lawdoc import LawDoc
from .legal import article_key, key_order
from .prompt import PROMPT_VERSION, SYSTEM, WorkItem, build_prompt
from .sidof import dmy
from .validate import check_alignment, validate

log = logging.getLogger(__name__)

MAX_BATCH_CHARS = 36_000
MAX_BATCH_ITEMS = 12


@dataclass
class Call:
    batch: int
    articles: list[str]
    key: str
    cached: bool
    duration_ms: int
    cost_usd: float
    model: str
    error: str | None = None


@dataclass
class LawRun:
    law: Law
    decree: Decree
    sections: list[Section]
    refs: list[Ref]
    items: list[WorkItem]
    plan: Plan
    applied: Applied | None
    reviews: list[Review] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    @property
    def doc(self) -> LawDoc | None:
        return self.applied.doc if self.applied else None

    @property
    def ok(self) -> bool:
        return self.applied is not None and bool(self.applied.touched)


def work_items(doc: LawDoc, sections: list[Section], refs: list[Ref]) -> list[WorkItem]:
    blocks: dict[str, DecreeArticle] = {}
    order: list[str] = []
    for s in sections:
        for a in s.articles():
            if a.key is None:
                continue
            if a.key in blocks:
                blocks[a.key].lines += a.lines
                blocks[a.key].headings += a.headings
            else:
                blocks[a.key] = DecreeArticle(a.key, a.label, list(a.lines), list(a.headings))
                order.append(a.key)
    for r in refs:
        if r.key not in blocks and r.key not in order:
            order.append(r.key)
    items: list[WorkItem] = []
    labels = [a.key for a in doc.articles if not a.key.startswith("range ")]
    for key in order:
        art = doc.article(key)
        decree = blocks.get(key)
        item_refs = [r for r in refs if r.key == key]
        if art is not None:
            items.append(WorkItem(key, art.label, True, decree, item_refs, doc.blocks[art.start : art.end]))
            continue
        if decree is None:
            items.append(WorkItem(key, f"Artículo {key}", False, None, item_refs))
            continue
        before = [k for k in labels if key_order(k) < key_order(key)]
        after = [k for k in labels if key_order(k) > key_order(key)]
        prev = doc.article(before[-1]).label if before else None
        nxt = doc.article(after[0]).label if after else None
        items.append(WorkItem(key, heading_label(decree.lines[0]), False, decree, item_refs, [], (prev, nxt)))
    return items


def batches(items: list[WorkItem]) -> list[list[WorkItem]]:
    out: list[list[WorkItem]] = []
    cur: list[WorkItem] = []
    size = 0
    for item in items:
        if cur and (size + item.size() > MAX_BATCH_CHARS or len(cur) >= MAX_BATCH_ITEMS):
            out.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += item.size()
    if cur:
        out.append(cur)
    return out


def expected_articles(items: list[WorkItem]) -> dict[str, str]:
    """Articles the decree really changes: named in the instruction or printed with new text."""
    out = {}
    for item in items:
        if item.refs or (item.decree is not None and not item.decree.only_ellipsis):
            out[item.key] = item.label
    return out


def run_law(
    doc: LawDoc,
    law: Law,
    decree: Decree,
    claude: Claude,
    laws: list[Law],
    *,
    workers: int = 1,
) -> LawRun:
    sections = decree.sections_for(law.slug)
    refs = [r for s in sections for r in parse_instruction(s.instruction, laws)]
    items = work_items(doc, sections, refs)
    plan = Plan()
    run = LawRun(law, decree, sections, refs, items, plan, None)
    if not sections or not items:
        run.reviews.append(
            Review(law.abbreviation, "no amendment instructions found for this law in the decree", "pipeline")
        )
        return run

    instruction = "\n".join(s.instruction for s in sections)
    groups = batches(items)
    prompts = [
        build_prompt(
            law_title=law.title,
            abbreviation=law.abbreviation,
            decree_title=decree.title,
            decree_date=dmy(decree.day),
            instruction=instruction,
            items=group,
            part=i + 1,
            parts=len(groups),
        )
        for i, group in enumerate(groups)
    ]

    def call(i: int):
        try:
            return i, claude.run(SYSTEM, prompts[i], SCHEMA), None
        except ClaudeError as exc:
            return i, None, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(call, range(len(groups))))
    for i, response, error in sorted(results, key=lambda x: x[0]):
        labels = [it.label for it in groups[i]]
        if response is None:
            run.calls.append(Call(i + 1, labels, "", False, 0, 0.0, "", error))
            for it in groups[i]:
                run.reviews.append(Review(it.label, f"the model call failed: {error}", "pipeline"))
            continue
        run.calls.append(
            Call(i + 1, labels, response.key, response.cached, response.duration_ms, response.cost_usd, response.model)
        )
        plan.extend(response.data, i + 1)

    # Alignment checks before anything touches the file.
    kept = []
    for e in plan.edits:
        problem = check_alignment(doc, e)
        if problem:
            run.reviews.append(Review(e.article, problem, "validator"))
        else:
            kept.append(e)
    plan.edits = kept

    new_labels = {it.key: it.label for it in items if not it.exists}
    applied = apply_plan(doc, plan, decree.day, new_labels=new_labels)
    append_transitorios(applied.doc, transitorios_blocks(decree, sections))
    run.applied = applied
    run.reviews = plan.reviews + run.reviews + applied.reviews
    reviewed = {article_key(r.article) for r in run.reviews}
    for r in validate(doc, applied, expected_articles(items)):
        if r.source == "pipeline" and article_key(r.article) in reviewed:
            continue
        run.reviews.append(r)
    return run


__all__ = ["PROMPT_VERSION", "LawRun", "run_law", "work_items"]
