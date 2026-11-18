from datetime import date

import pytest

from reforma_al_dia.applier import (
    EditError,
    apply_plan,
    apply_to_article,
    heading_label,
    new_note,
    update_note,
)
from reforma_al_dia.edits import DivisionEdit, Edit, Plan
from reforma_al_dia.lawdoc import LawDoc

DAY = date(2026, 11, 13)

LAW = """\
---
title: Ley de Prueba
abbreviation: LP
slug: lp
published_dof: 2000-01-01
last_reform_dof: 2020-01-01
articles: 5
source: https://example.invalid/LP.doc
source_sha256: abc
retrieved: 2026-11-17
---

# LEY DE PRUEBA

TEXTO VIGENTE

Última reforma publicada DOF 01-01-2020

### TÍTULO PRIMERO · Disposiciones Generales

#### Capítulo I · Del Objeto

##### Artículo 1.

Esta Ley es de orden público.

Para efectos de esta Ley se entiende por:

I. Autoridad: la Secretaría;

*Fracción reformada DOF 01-05-2019*

II. Persona: toda persona física o moral;

a) Las físicas;

b) Las morales, y

*Inciso reformado DOF 30-11-2018*

*Fracción adicionada DOF 02-01-2013*

III. Registro: el padrón.

*Artículo reformado DOF 01-01-2020*

##### Artículo 2.

Corresponde a la Secretaría aplicar esta Ley.

*Artículo adicionado DOF 30-12-1997*

##### Artículo 2 Bis.

Texto del dos bis.

#### Capítulo II · De las Sanciones

##### Artículo 3.

Las multas serán de 10 a 100 días.

*Párrafo reformado DOF 01-01-2020*

Las reincidencias se sancionan con el doble.

##### Artículo 4.

Texto del cuatro.

## TRANSITORIOS

**Primero.-** Esta Ley entrará en vigor al día siguiente.
"""


def E(article, op, unit_type, unit, paragraphs, a=None, b=None, after=None):
    return Edit(article, op, unit_type, unit, paragraphs, from_block=a, to_block=b, insert_after=after)


@pytest.fixture
def doc():
    return LawDoc.parse(LAW)


def content(doc, key):
    return doc.article_blocks(key)


def test_note_conventions():
    assert (
        update_note("Fracción reformada DOF 01-05-2019", "reform", DAY)
        == "Fracción reformada DOF 01-05-2019, 13-11-2026"
    )
    assert (
        update_note("Artículo adicionado DOF 30-12-1997", "reform", DAY)
        == "Artículo adicionado DOF 30-12-1997. Reformado DOF 13-11-2026"
    )
    assert (
        update_note("Fracción recorrida DOF 30-12-1983. Reformada DOF 28-12-1994", "reform", DAY)
        == "Fracción recorrida DOF 30-12-1983. Reformada DOF 28-12-1994, 13-11-2026"
    )
    assert (
        update_note("Artículo reformado DOF 28-05-1998", "repeal", DAY)
        == "Artículo reformado DOF 28-05-1998. Derogado DOF 13-11-2026"
    )
    assert new_note("add", "fraccion", DAY) == "Fracción adicionada DOF 13-11-2026"
    assert new_note("repeal", "inciso", DAY) == "Inciso derogado DOF 13-11-2026"


def test_reform_fraccion_appends_date_to_its_note(doc):
    blocks = content(doc, "1")  # [1] párrafo, [2] párrafo, [3] I., [4] note, ...
    out = apply_to_article(
        blocks,
        [E("Artículo 1.", "reform", "fraccion", "fracción I", ["I. Autoridad: la Secretaría de Economía;"], 3, 3)],
        DAY,
    )
    assert out[2] == "I. Autoridad: la Secretaría de Economía;"
    assert out[3] == "*Fracción reformada DOF 01-05-2019, 13-11-2026*"
    assert out[4:] == blocks[4:]


def test_reform_paragraph_without_note_gets_a_new_one_before_the_article_note(doc):
    blocks = content(doc, "1")
    out = apply_to_article(
        blocks,
        [E("Artículo 1.", "reform", "fraccion", "fracción III", ["III. Registro: el padrón nacional."], 10, 10)],
        DAY,
    )
    assert out[-3:] == [
        "III. Registro: el padrón nacional.",
        "*Fracción reformada DOF 13-11-2026*",
        "*Artículo reformado DOF 01-01-2020*",
    ]


def test_reform_inciso_keeps_the_note_of_the_enclosing_fraccion(doc):
    blocks = content(doc, "1")  # [7] b), [8] inciso note, [9] fracción note
    out = apply_to_article(
        blocks,
        [E("Artículo 1.", "reform", "inciso", "inciso b) de la fracción II", ["b) Las morales y colectivas, y"], 7, 7)],
        DAY,
    )
    i = out.index("b) Las morales y colectivas, y")
    assert out[i + 1 : i + 3] == [
        "*Inciso reformado DOF 30-11-2018, 13-11-2026*",
        "*Fracción adicionada DOF 02-01-2013*",
    ]


def test_reform_whole_fraccion_with_incisos_drops_their_notes(doc):
    blocks = content(doc, "1")
    edit = E(
        "Artículo 1.", "reform", "fraccion", "fracción II", ["II. Persona: toda persona física;", "a) Única."], 5, 9
    )
    out = apply_to_article(blocks, [edit], DAY)
    i = out.index("II. Persona: toda persona física;")
    assert out[i + 1 : i + 3] == ["a) Única.", "*Fracción adicionada DOF 02-01-2013. Reformada DOF 13-11-2026*"]
    assert "*Inciso reformado DOF 30-11-2018*" not in out


def test_add_fraccion_after_a_unit_with_notes(doc):
    blocks = content(doc, "1")
    out = apply_to_article(
        blocks, [E("Artículo 1.", "add", "fraccion", "fracción IV", ["IV. Sanción: la multa."], after=10)], DAY
    )
    assert out[-3:] == [
        "IV. Sanción: la multa.",
        "*Fracción adicionada DOF 13-11-2026*",
        "*Artículo reformado DOF 01-01-2020*",
    ]


def test_add_paragraph_at_the_start(doc):
    blocks = content(doc, "4")
    out = apply_to_article(
        blocks, [E("Artículo 4.", "add", "parrafo", "párrafo primero", ["Nuevo primer párrafo."], after=0)], DAY
    )
    assert out == ["Nuevo primer párrafo.", "*Párrafo adicionado DOF 13-11-2026*", "Texto del cuatro."]


def test_repeal_fraccion_keeps_it_as_se_deroga(doc):
    blocks = content(doc, "1")
    out = apply_to_article(blocks, [E("Artículo 1.", "repeal", "fraccion", "fracción I", ["I. Se deroga."], 3, 3)], DAY)
    assert out[2:4] == ["I. Se deroga.", "*Fracción reformada DOF 01-05-2019. Derogada DOF 13-11-2026*"]


def test_repeal_whole_article(doc):
    blocks = content(doc, "2")
    out = apply_to_article(blocks, [E("Artículo 2.", "repeal", "articulo", "artículo", ["Se deroga."], 1, 1)], DAY)
    assert out == ["Se deroga.", "*Artículo adicionado DOF 30-12-1997. Derogado DOF 13-11-2026*"]


def test_whole_article_reform_drops_paragraph_notes(doc):
    blocks = content(doc, "3")
    out = apply_to_article(
        blocks, [E("Artículo 3.", "reform", "articulo", "artículo", ["Las multas serán de 20 a 200 días."], 1, 3)], DAY
    )
    assert out == ["Las multas serán de 20 a 200 días.", "*Artículo reformado DOF 13-11-2026*"]


def test_markdown_is_escaped_like_leyes_mx():
    out = apply_to_article(
        ["Texto."],
        [E("Artículo 9.", "reform", "parrafo", "párrafo primero", ["Multa de $1,000 a *cien* UMA."], 1, 1)],
        DAY,
    )
    assert out[0] == "Multa de \\$1,000 a \\*cien\\* UMA."


def test_tables_are_rendered_like_leyes_mx():
    out = apply_to_article(
        ["Texto."], [E("Artículo 9.", "add", "otro", "tabla", ["| Año | Horas |\n| 2027 | 46 |"], after=1)], DAY
    )
    assert out[1] == "|   |   |\n|---|---|\n| Año | Horas |\n| 2027 | 46 |"


@pytest.mark.parametrize(
    "edits",
    [
        [
            E("Artículo 1.", "reform", "fraccion", "I", ["I. x"], 3, 3),
            E("Artículo 1.", "reform", "fraccion", "I", ["I. y"], 3, 5),
        ],
        [E("Artículo 1.", "reform", "parrafo", "x", ["x"], 0, 0)],
        [E("Artículo 1.", "reform", "parrafo", "x", ["x"], 4, 4)],  # starts on a note
        [
            E("Artículo 1.", "reform", "fraccion", "II", ["II. x"], 5, 9),
            E("Artículo 1.", "add", "inciso", "c", ["c) x"], after=6),
        ],
    ],
)
def test_invalid_edit_sets_are_rejected(doc, edits):
    with pytest.raises(EditError):
        apply_to_article(content(doc, "1"), edits, DAY)


def test_plan_adds_bis_and_ter_articles_in_order_with_notes(doc):
    plan = Plan(edits=[
        E("Artículo 2 Ter.", "add", "articulo", "artículo", ["Texto del dos ter."]),
        E("Artículo 3 Bis.", "add", "articulo", "artículo", ["Texto del tres bis.", "Segundo párrafo."]),
    ])  # fmt: skip
    result = apply_plan(doc, plan, DAY)
    keys = [a.key for a in result.doc.articles]
    assert keys == ["1", "2", "2 bis", "2 ter", "3", "3 bis", "4"]
    assert result.added == ["2 ter", "3 bis"]
    # The new article goes before the next division heading, not inside the next chapter.
    i = result.doc.blocks.index("##### Artículo 2 Ter.")
    assert result.doc.blocks[i : i + 4] == [
        "##### Artículo 2 Ter.",
        "Texto del dos ter.",
        "*Artículo adicionado DOF 13-11-2026*",
        "#### Capítulo II · De las Sanciones",
    ]
    assert result.doc.meta["articles"] == 7
    assert result.doc.meta["last_reform_dof"] == DAY
    assert "Última reforma publicada DOF 13-11-2026" in result.doc.blocks


def test_plan_reports_instead_of_guessing(doc):
    plan = Plan(edits=[
        E("Artículo 99.", "reform", "parrafo", "párrafo primero", ["x"], 1, 1),
        E("Artículo 2.", "add", "articulo", "artículo", ["Ya existe."]),
        E("Artículo 4.", "reform", "parrafo", "párrafo primero", ["..."], 1, 1),
    ])  # fmt: skip
    result = apply_plan(doc, plan, DAY)
    reasons = " | ".join(r.reason for r in result.reviews)
    assert "does not exist" in reasons
    assert "already exists" in reasons
    assert "ellipsis" in reasons
    assert result.touched == []
    assert result.doc.article_map() == doc.article_map()


def test_divisions_add_and_rename(doc):
    plan = Plan(
        edits=[E("Artículo 3 Bis.", "add", "articulo", "artículo", ["Texto."])],
        divisions=[
            DivisionEdit("add", "capitulo", "Capítulo II Bis", "De las Medidas", before_article="Artículo 3 Bis."),
            DivisionEdit(
                "rename", "capitulo", "Capítulo I", "Del Objeto y Ámbito", current_heading="Capítulo I · Del Objeto"
            ),
        ],
    )
    result = apply_plan(doc, plan, DAY)
    blocks = result.doc.blocks
    i = blocks.index("#### Capítulo II Bis · De las Medidas")
    assert blocks[i + 1] == "*Capítulo adicionado DOF 13-11-2026*"
    assert blocks[i + 2] == "##### Artículo 3 Bis."
    j = blocks.index("#### Capítulo I · Del Objeto y Ámbito")
    assert blocks[j + 1] == "*Denominación reformada DOF 13-11-2026*"


def test_heading_label():
    assert heading_label("Artículo 64 Quáter. Será responsable") == "Artículo 64 Quáter."
    assert heading_label("Artículo 15-A.- Texto") == "Artículo 15-A."
    assert heading_label("Artículo 76.- Las personas") == "Artículo 76."
