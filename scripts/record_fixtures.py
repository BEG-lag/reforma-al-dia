"""Record the test fixtures from real data (run by hand; tests never touch the network).

    uv run python scripts/record_fixtures.py

* SIDOF responses for two real decrees (from the local HTTP cache);
* trimmed copies of the laws before and after each decree, from leyes-mx;
* the structured output Claude returned for each one in the backtest.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from reforma_al_dia.config import Config
from reforma_al_dia.history import law_at
from reforma_al_dia.lawdoc import LawDoc

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"

# (case id, slug, decree note, DOF date, first and last article of the excerpt)
CASES = [
    ("2025-12-15-lgra", "lgra", 5776177, "15-12-2025", "63", "65"),
    ("2026-03-03-cpeum", "cpeum", 5781417, "03-03-2026", "122", "124"),
]


def excerpt(text: str, first: str, last: str) -> str:
    """Front matter, header, the articles first..last and the transitorios headings with the last decree."""
    doc = LawDoc.parse(text)
    a, b = doc.article(first), doc.article(last)
    head = doc.blocks[: doc.articles[0].heading]
    body = doc.blocks[a.heading : b.end]
    trans = [blk for blk in doc.blocks[doc.trans_start :] if blk.startswith("## ")]
    decrees = [i for i in range(doc.trans_start, len(doc.blocks)) if doc.blocks[i].startswith("### ")]
    tail = doc.blocks[decrees[-1] :] if decrees else []
    out = LawDoc(front=doc.front, blocks=head + body + trans + tail)
    out.reindex()
    out.set_front(articles=len(out.articles))
    return out.render()


def main() -> None:
    cfg = Config.load(str(ROOT))
    for sub in ("sidof", "leyes/leyes", "official", "claude"):
        (FIX / sub).mkdir(parents=True, exist_ok=True)
    slugs = sorted({c[1] for c in CASES})
    catalog = ["groups:", "  test: Test", "laws:"] + [f"  - {{key: {s}, slug: {s}, group: test}}" for s in slugs]
    (FIX / "leyes" / "catalog.yml").write_text("\n".join(catalog) + "\n", encoding="utf-8")
    for case_id, slug, code, dmy, first, last in CASES:
        for name in (f"notas__nota__{code}.json", f"notas__{dmy}.json"):
            shutil.copyfile(cfg.sidof_cache / name, FIX / "sidof" / name)
        record = json.loads((cfg.data / "casos" / f"{case_id}.json").read_text(encoding="utf-8"))
        before = law_at(cfg.leyes, record["base"]["commit"], slug)
        after = law_at(cfg.leyes, record["official"]["commit"], slug)
        (FIX / "leyes" / "leyes" / f"{slug}.md").write_text(excerpt(before, first, last), encoding="utf-8")
        (FIX / "official" / f"{slug}.md").write_text(excerpt(after, first, last), encoding="utf-8")
        outputs = []
        for call in record["calls"]:
            raw = json.loads((cfg.claude_cache / call["key"][:2] / f"{call['key']}.json").read_text(encoding="utf-8"))
            outputs.append(raw["structured_output"])
        (FIX / "claude" / f"{case_id}.json").write_text(
            json.dumps(outputs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print("recorded", case_id)


if __name__ == "__main__":
    main()
