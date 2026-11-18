"""The structured edits the model returns, and the JSON schema that constrains them.

The model never rewrites a file. For each unit a decree touches it returns one
edit: which article, which operation (reform, add, repeal), which unit
(párrafo, fracción, inciso...), which numbered blocks of the current text it
replaces or follows, and the exact new text. Everything else, from editorial
notes to headings and front matter, is done by deterministic code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

UNIT_TYPES = [
    "articulo",
    "parrafo",
    "fraccion",
    "inciso",
    "subinciso",
    "numeral",
    "apartado",
    "base",
    "epigrafe",
    "otro",
]
DIVISION_KINDS = ["libro", "titulo", "capitulo", "seccion"]

SCHEMA: dict = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "article": {"type": "string"},
                    "operation": {"type": "string", "enum": ["reform", "add", "repeal"]},
                    "unit_type": {"type": "string", "enum": UNIT_TYPES},
                    "unit": {"type": "string"},
                    "from_block": {"type": ["integer", "null"]},
                    "to_block": {"type": ["integer", "null"]},
                    "insert_after": {"type": ["integer", "null"]},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["article", "operation", "unit_type", "unit", "paragraphs"],
            },
        },
        "divisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["add", "rename"]},
                    "kind": {"type": "string", "enum": DIVISION_KINDS},
                    "label": {"type": "string"},
                    "name": {"type": "string"},
                    "before_article": {"type": ["string", "null"]},
                    "current_heading": {"type": ["string", "null"]},
                },
                "required": ["operation", "kind", "label", "name"],
            },
        },
        "needs_review": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"article": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["article", "reason"],
            },
        },
    },
    "required": ["edits", "divisions", "needs_review"],
}


@dataclass
class Edit:
    article: str
    operation: str
    unit_type: str
    unit: str
    paragraphs: list[str]
    from_block: int | None = None
    to_block: int | None = None
    insert_after: int | None = None
    batch: int = 0

    @classmethod
    def from_json(cls, row: dict, batch: int = 0) -> Edit:
        return cls(
            article=str(row.get("article") or ""),
            operation=str(row.get("operation") or "reform"),
            unit_type=str(row.get("unit_type") or "otro"),
            unit=str(row.get("unit") or ""),
            paragraphs=[str(p) for p in row.get("paragraphs") or []],
            from_block=_int(row.get("from_block")),
            to_block=_int(row.get("to_block")),
            insert_after=_int(row.get("insert_after")),
            batch=batch,
        )

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class DivisionEdit:
    operation: str
    kind: str
    label: str
    name: str
    before_article: str | None = None
    current_heading: str | None = None
    batch: int = 0

    @classmethod
    def from_json(cls, row: dict, batch: int = 0) -> DivisionEdit:
        return cls(
            operation=str(row.get("operation") or "add"),
            kind=str(row.get("kind") or "capitulo"),
            label=str(row.get("label") or ""),
            name=str(row.get("name") or ""),
            before_article=row.get("before_article") or None,
            current_heading=row.get("current_heading") or None,
            batch=batch,
        )

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Review:
    article: str
    reason: str
    source: str = "model"  # model, validator, pipeline

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Plan:
    edits: list[Edit] = field(default_factory=list)
    divisions: list[DivisionEdit] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)

    def extend(self, data: dict, batch: int) -> None:
        self.edits += [Edit.from_json(r, batch) for r in data.get("edits") or []]
        self.divisions += [DivisionEdit.from_json(r, batch) for r in data.get("divisions") or []]
        self.reviews += [
            Review(str(r.get("article") or ""), str(r.get("reason") or "")) for r in data.get("needs_review") or []
        ]

    def to_json(self) -> dict:
        return {
            "edits": [e.to_json() for e in self.edits],
            "divisions": [d.to_json() for d in self.divisions],
            "needs_review": [r.to_json() for r in self.reviews],
        }


def _int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
