from datetime import date

import pytest

from reforma_al_dia.lawdoc import LawDoc, block_kind, plain
from reforma_al_dia.legal import article_key, check_numbering, escape_md, is_note, key_order, long_date, unescape_md


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("Artículo 3o.", "3"),
        ("Artículo 3º.-", "3"),
        ("Artículo 64 Quáter.", "64 quater"),
        ("Artículo 15-A.", "15 a"),
        ("Artículo 8o.-A", "8 a"),
        ("Artículo 2o. Bis", "2 bis"),
        ("Artículo 228 a.", "228 a"),
        ("Artículo 353-Ñ.", "353 ñ"),
        ("Artículo 353-N.", "353 n"),
        ("Artículo 77 Bis 1.", "77 bis 1"),
        ("Artículo 427 Quinquies.", "427 quintus"),
        ("ARTÍCULO 76.- Las personas trabajadoras", "76"),
    ],
)
def test_article_key(label, key):
    assert article_key(label) == key


def test_key_order_follows_the_codes():
    keys = ["30", "29 b", "29 a bis", "29 a", "29 ter", "29 bis", "29"]
    assert sorted(keys, key=key_order) == ["29", "29 bis", "29 ter", "29 a", "29 a bis", "29 b", "30"]


@pytest.mark.parametrize(
    "text",
    ["Costará $100.00 por *día*", "1. Primero", "# no es encabezado", "- no es lista", "a_b \\ c", "<b>"],
)
def test_escape_round_trip(text):
    assert unescape_md(escape_md(text)) == text


def test_is_note():
    assert is_note("Fracción reformada DOF 01-05-2019, 04-04-2024")
    assert is_note("Artículo adicionado DOF 30-12-1997. Reformado DOF 17-01-2024")
    assert not is_note("Artículo 27. La propiedad de las tierras")
    assert not is_note("Se deroga.")


def test_long_date():
    assert long_date(date(2026, 5, 1)) == "1 de mayo de 2026"


def test_round_trip_and_index(fix):
    text = (fix / "leyes" / "leyes" / "lgra.md").read_text(encoding="utf-8")
    doc = LawDoc.parse(text)
    assert doc.render() == text
    assert [a.key for a in doc.articles] == ["63", "63 bis", "64", "64 bis", "64 ter", "65"]
    art = doc.article("64 ter")
    assert art.label == "Artículo 64 Ter."
    assert doc.blocks[art.heading].startswith("###### ")
    assert doc.article_level == 6
    assert "capitulo" in doc.division_levels()
    assert doc.trans_start > doc.articles[-1].end - 1


def test_block_kinds(fix):
    doc = LawDoc.parse((fix / "leyes" / "leyes" / "cpeum.md").read_text(encoding="utf-8"))
    content = doc.article_blocks("123")
    kinds = {block_kind(b) for b in content}
    assert {"text", "note"} <= kinds
    notes = [plain(b) for b in content if block_kind(b) == "note"]
    assert any(n.startswith("Fracción reformada DOF") for n in notes)


def test_front_matter_and_header_update(fix):
    doc = LawDoc.parse((fix / "leyes" / "leyes" / "lgra.md").read_text(encoding="utf-8"))
    doc.set_last_reform(date(2025, 12, 15))
    doc.set_front(articles=7, provisional="sí")
    assert doc.meta["last_reform_dof"] == date(2025, 12, 15)
    assert doc.meta["articles"] == 7
    assert doc.meta["provisional"] == "sí"
    assert "Última reforma publicada DOF 15-12-2025" in doc.blocks
    assert list(doc.meta)[:3] == ["title", "abbreviation", "slug"]


def test_numbering_check():
    ok = check_numbering(["Artículo 1.", "Artículo 2.", "Artículo 2 Bis.", "Artículo 3."])
    assert ok.ok
    bad = check_numbering(["Artículo 1.", "Artículo 3.", "Artículo 3."])
    assert bad.missing == [2]
    assert bad.duplicates == ["Artículo 3."]
