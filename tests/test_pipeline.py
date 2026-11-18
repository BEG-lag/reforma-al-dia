"""End to end on real decrees, with the edits Claude returned in the backtest replayed from fixtures."""

from datetime import date

import pytest

from reforma_al_dia.applier import apply_plan
from reforma_al_dia.audit import classify, compare_articles, decree_block, normalize, summarize, word_diff
from reforma_al_dia.decree import parse_decree
from reforma_al_dia.edits import Edit, Plan
from reforma_al_dia.lawdoc import LawDoc
from reforma_al_dia.pipeline import expected_articles, run_law
from reforma_al_dia.prompt import SYSTEM
from reforma_al_dia.validate import check_alignment, validate

CASES = [("2025-12-15-lgra", "lgra", 5776177), ("2026-03-03-cpeum", "cpeum", 5781417)]


def _run(fix, laws, sidof, recorded, case_id, slug, code):
    note = sidof.note(code)
    decree = parse_decree(note.code, note.day, note.title, note.html, laws)
    before = LawDoc.parse((fix / "leyes" / "leyes" / f"{slug}.md").read_text(encoding="utf-8"))
    official = LawDoc.parse((fix / "official" / f"{slug}.md").read_text(encoding="utf-8"))
    law = next(x for x in laws if x.slug == slug)
    claude = recorded(case_id)
    run = run_law(before, law, decree, claude, laws)
    return run, before, official, claude


@pytest.mark.parametrize(("case_id", "slug", "code"), CASES)
def test_replayed_decree_matches_the_official_text(fix, laws, sidof, recorded, case_id, slug, code):
    run, before, official, _claude = _run(fix, laws, sidof, recorded, case_id, slug, code)
    assert run.ok
    assert run.reviews == []
    ai = run.doc
    # Every article, and the whole body of the excerpt, equal the official consolidated text.
    assert ai.article_map() == official.article_map()
    articles = compare_articles(
        before, ai, official, touched=run.applied.touched, expected=expected_articles(run.items)
    )
    assert {a.status for a in articles} == {"exact"}
    assert summarize(articles)["exact"] == len(run.applied.touched)
    # The transitorios block the Cámara appends, generated deterministically from the decree.
    assert classify(decree_block(ai, run.decree.title), decree_block(official, run.decree.title)) in (
        "exact",
        "normalized",
    )
    assert ai.meta["last_reform_dof"] == run.decree.day


def test_prompt_carries_the_decree_and_numbered_blocks(fix, laws, sidof, recorded):
    *_, claude = _run(fix, laws, sidof, recorded, "2026-03-03-cpeum", "cpeum", 5781417)
    (prompt,) = claude.prompts
    assert "OPERATIVE INSTRUCTION FOR THIS LAW" in prompt
    assert "ARTICLE Artículo 123." in prompt
    assert "  IV. La jornada laboral será de cuarenta horas semanales en los términos que establezca la Ley." in prompt
    assert "NOTE (do not edit): Fracción reformada DOF" in prompt
    assert "[1] " in prompt
    assert "never guess" in SYSTEM


def test_new_article_prompt_says_where_it_goes(fix, laws, sidof, recorded):
    *_, claude = _run(fix, laws, sidof, recorded, "2025-12-15-lgra", "lgra", 5776177)
    (prompt,) = claude.prompts
    assert "does not exist yet in the consolidated law" in prompt
    assert "after Artículo 64 Ter." in prompt


def test_untouched_named_article_goes_to_review(fix, laws, sidof, recorded):
    note = sidof.note(5781417)
    decree = parse_decree(note.code, note.day, note.title, note.html, laws)
    before = LawDoc.parse((fix / "leyes" / "leyes" / "cpeum.md").read_text(encoding="utf-8"))
    law = next(x for x in laws if x.slug == "cpeum")

    class Empty:
        def run(self, system, prompt, schema):
            from reforma_al_dia.claude import Response

            return Response({"edits": [], "divisions": [], "needs_review": []}, True, 0, 0.0, "x", "k")

    run = run_law(before, law, decree, Empty(), laws)
    assert not run.ok
    assert any("no edit was applied" in r.reason for r in run.reviews)


def test_alignment_checks(fix):
    doc = LawDoc.parse((fix / "leyes" / "leyes" / "cpeum.md").read_text(encoding="utf-8"))
    content = doc.article_blocks("123")
    i = next(n for n, b in enumerate(content, start=1) if b.startswith("IV. "))
    good = Edit(
        "Artículo 123.", "reform", "fraccion", "Apartado A, fracción IV", ["IV. Texto."], from_block=i, to_block=i
    )
    assert check_alignment(doc, good) is None
    j = next(n for n, b in enumerate(content, start=1) if b.startswith("V. "))
    off = Edit(
        "Artículo 123.", "reform", "fraccion", "Apartado A, fracción IV", ["IV. Texto."], from_block=j, to_block=j
    )
    assert "starts with" in (check_alignment(doc, off) or "")
    apartado_c = Edit("Artículo 123.", "reform", "fraccion", "C, I", ["I. Texto."], from_block=1, to_block=1)
    assert "numbered" not in (check_alignment(doc, apartado_c) or "")
    # A pure renumbering ("recorriéndose"): same text, next number.
    old = content[i - 1].split(" ", 1)[1]
    moved = Edit(
        "Artículo 123.", "reform", "fraccion", "fracción V (antes IV)", [f"V. {old}"], from_block=i, to_block=i
    )
    assert check_alignment(doc, moved) is None


def test_validator_catches_collateral_changes(fix):
    before = LawDoc.parse((fix / "leyes" / "leyes" / "lgra.md").read_text(encoding="utf-8"))
    result = apply_plan(
        before, Plan(edits=[Edit("Artículo 63.", "reform", "articulo", "artículo", ["Nuevo."], 1, 1)]), date(2026, 1, 1)
    )
    assert validate(before, result, {"63": "Artículo 63."}) == []
    a = result.doc.article("64")
    result.doc.blocks[a.start] = "Cambio que nadie pidió."
    result.doc.reindex()
    reasons = [r.reason for r in validate(before, result, {"63": "Artículo 63.", "65": "Artículo 65."})]
    assert "an article changed without an edit for it" in reasons
    assert "the decree names this article but no edit was applied to it" in reasons


def test_audit_levels():
    assert classify(["XV. Se deroga."], ["XV. Se deroga."]) == "exact"
    assert classify(["XV. Se deroga"], ["XV. Se deroga."]) == "normalized"
    assert classify(["“Comillas” y – guion"], ['"Comillas" y - guion']) == "normalized"
    assert classify(["Texto.", "*Párrafo reformado DOF 01-01-2020*"], ["Texto.", "22-08-1996, 15-09-2024"]) == "exact"
    assert classify(["Multa de 10 días."], ["Multa de 20 días."]) == "mismatch"
    assert normalize("a\\*b \u2014 c") == "a b c"
    assert word_diff(["Multa de 10 días."], ["Multa de 20 días."]) == [
        ["=", "Multa de"],
        ["-", "10"],
        ["+", "20"],
        ["=", "días."],
    ]


def test_audit_statuses():
    def doc(articles: dict[str, list[str]]) -> LawDoc:
        blocks = ["# LEY"]
        for key, content in articles.items():
            blocks += [f"##### Artículo {key}.", *content]
        return LawDoc.parse("---\ntitle: X\n---\n\n" + "\n\n".join(blocks) + "\n")

    before = doc({"1": ["A."], "2": ["B."], "3": ["C."], "4": ["D."]})
    ai = doc({"1": ["A2."], "2": ["B."], "3": ["C."], "4": ["D."]})
    official = doc({"1": ["A2."], "2": ["B2."], "3": ["C2."], "4": ["D."]})
    got = {a.key: a.status for a in compare_articles(before, ai, official, touched=["1"], expected={"1": "", "2": ""})}
    assert got == {"1": "exact", "2": "not_applied", "3": "outside_decree"}
