from datetime import date

import pytest

from reforma_al_dia.decree import Para, html_paragraphs, is_ellipsis, parse_decree, split_articles
from reforma_al_dia.detect import detect, title_candidates
from reforma_al_dia.instructions import parse_instruction


def _decree(sidof, laws, code):
    note = sidof.note(code)
    return parse_decree(note.code, note.day, note.title, note.html, laws)


def test_html_paragraphs(sidof):
    paras = html_paragraphs(sidof.note(5781417).html)
    assert paras[0].text.startswith("DECRETO por el que se reforman las fracciones IV y XI")
    assert any(p.is_table for p in paras)
    assert all("  " not in p.text for p in paras)


def test_one_law_section_with_placeholders(sidof, laws):
    d = _decree(sidof, laws, 5781417)
    assert d.slugs == ["cpeum"]
    (section,) = d.sections_for("cpeum")
    assert section.label == "Artículo Único.-"
    (art,) = section.articles()
    assert art.key == "123"
    assert "IV. La jornada laboral será de cuarenta horas semanales en los términos que establezca la Ley." in art.lines
    assert sum(is_ellipsis(x) for x in art.lines) >= 5
    assert d.transitorios[0].text == "Transitorios"
    assert any(p.is_table for p in d.transitorios)


def test_two_laws_one_in_catalog(sidof, laws):
    d = _decree(sidof, laws, 5776177)
    assert d.slugs == ["lgra"]
    assert [s.slug for s in d.sections] == [None, "lgra"]
    (art,) = d.sections_for("lgra")[0].articles()
    assert art.key == "64 quater"
    assert not art.only_ellipsis


@pytest.mark.parametrize("line", ["...", "……..", "………", "I. a III. ...", "a) a c) ...", "A. ...", ". . ."])
def test_ellipsis(line):
    assert is_ellipsis(line)


@pytest.mark.parametrize(
    "line", ["III. Se deroga.", "Las personas menores de dieciocho años no podrán laborar tiempo extraordinario."]
)
def test_not_ellipsis(line):
    assert not is_ellipsis(line)


def test_split_articles_keeps_headings_with_the_next_article():
    body = [Para(t) for t in [
        "Artículo 10.- Texto nuevo.", "...", "CAPÍTULO III BIS", "De las Sanciones", "Artículo 10 Bis.- Primer párrafo.", "I. Una;", "II. Otra.",
    ]]  # fmt: skip
    arts = split_articles(body)
    assert [a.key for a in arts] == ["10", "10 bis"]
    assert arts[1].headings == ["CAPÍTULO III BIS", "De las Sanciones"]
    assert arts[1].lines[1:] == ["I. Una;", "II. Otra."]


def test_multi_paragraph_instruction(laws):
    html = """<html><body>
    <div>DECRETO por el que se reforman diversas disposiciones de la Ley General de Responsabilidades Administrativas.</div>
    <div>"EL CONGRESO GENERAL DE LOS ESTADOS UNIDOS MEXICANOS, D E C R E T A:</div>
    <div>SE REFORMAN DIVERSAS DISPOSICIONES DE LA LEY GENERAL DE RESPONSABILIDADES ADMINISTRATIVAS.</div>
    <div>Artículo Único.- Se reforman los artículos 63, fracción I; 64, y se adicionan los artículos 64 Bis, con una</div>
    <div>fracción III, de la Ley General de Responsabilidades Administrativas, para quedar como sigue:</div>
    <div>Artículo 63.- ...</div><div>I. Nuevo texto;</div>
    <div>Transitorio</div><div>Único.- El presente Decreto entrará en vigor al día siguiente.</div>
    </body></html>"""
    d = parse_decree(1, date(2026, 1, 1), "Decreto", html, laws)
    assert [s.slug for s in d.sections] == ["lgra"]
    assert d.sections[0].instruction.endswith("para quedar como sigue:")
    assert [a.key for a in d.sections[0].articles()] == ["63"]
    assert next(p.text for p in d.transitorios) == "Transitorio"


def test_instruction_parser(laws):
    text = (
        "Artículo Único.- Se reforman los artículos 3o., párrafo segundo; 27, fracción III; y 28 Bis; se adiciona una "
        "fracción IV Bis al artículo 30 y los artículos 15-A y 15-B; y se derogan los artículos 41 a 44 de la Ley "
        "General de Responsabilidades Administrativas, para quedar como sigue:"
    )
    refs = parse_instruction(text, laws)
    got = [(r.op, r.key) for r in refs]
    assert got == [
        ("reform", "3"), ("reform", "27"), ("reform", "28 bis"), ("add", "30"), ("add", "15 a"), ("add", "15 b"),
        ("repeal", "41"), ("repeal", "42"), ("repeal", "43"), ("repeal", "44"),
    ]  # fmt: skip
    assert refs[0].detail == "párrafo segundo"
    assert refs[1].detail == "fracción III"


def test_instruction_ignores_numbers_inside_law_names_and_numerals(laws):
    text = (
        "ARTÍCULO ÚNICO.- Se reforman los artículos 41, Base V, Apartado C, numeral 3; y 116, fracción IV de la "
        "Constitución Política de los Estados Unidos Mexicanos, para quedar como sigue:"
    )
    assert [r.key for r in parse_instruction(text, laws)] == ["41", "116"]
    text = "Artículo Segundo.- Se adiciona un artículo 64 Quáter a la Ley General de Responsabilidades Administrativas, para quedar como sigue:"
    assert [(r.op, r.key) for r in parse_instruction(text, laws)] == [("add", "64 quater")]


def test_detect_finds_the_decree_and_its_law(sidof, laws):
    (found,) = detect(sidof, date(2025, 12, 15), laws)
    assert found.note.code == 5776177
    assert found.slugs == ["lgra"]
    assert any("outside the catalog" in n for n in found.notes)


def test_title_filter_skips_regulations(sidof, laws):
    notes = sidof.day_index(date(2025, 12, 15))
    codes = [n.code for n in title_candidates(notes, laws)]
    assert 5776177 in codes
    # "Decreto por el que se reforman ... del Reglamento Interior de la Secretaría de la Defensa Nacional."
    assert 5776178 not in codes
