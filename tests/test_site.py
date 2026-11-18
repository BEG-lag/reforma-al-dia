import json
from datetime import date

from reforma_al_dia import scoreboard
from reforma_al_dia.config import Config
from reforma_al_dia.site import build_site


def case(case_id, code, counts, source="backtest"):
    return {
        "id": case_id,
        "source": source,
        "law": {"slug": "lgra", "title": "Ley General de Responsabilidades Administrativas", "abbreviation": "LGRA"},
        "decree": {
            "code": code,
            "title": "DECRETO por el que se adiciona",
            "date": "2025-12-15",
            "url": "https://dof.gob.mx/x",
            "pdf": None,
            "instruction": ["Artículo Segundo.- Se adiciona"],
        },
        "base": {"commit": "a" * 40},
        "official": {"commit": "b" * 40, "date": "2025-12-15", "same_day": True},
        "calls": [{"duration_ms": 2000, "cost_usd": 0.03, "model": "m"}],
        "plan": {"edits": [], "divisions": [], "needs_review": []},
        "needs_review": [{"article": "Artículo 1.", "reason": "duda <b>", "source": "model"}],
        "counts": {**dict.fromkeys(scoreboard.STATUSES, 0), **counts, "notes_match": counts.get("exact", 0)},
        "transitorios": "exact",
        "body_identical": True,
        "articles": [
            {
                "key": "64 quater",
                "label": "Artículo 64 Quáter.",
                "status": "mismatch",
                "notes_match": False,
                "before": [],
                "ai": ["Texto de la IA."],
                "official": ["Texto oficial."],
                "diff": [["-", "de la IA."], ["+", "oficial."]],
            }
        ],
    }


def test_scoreboard_totals():
    board = scoreboard.build(
        [case("a", 1, {"exact": 8, "mismatch": 2}), case("b", 1, {"exact": 1, "not_applied": 3}, "live")],
        date(2026, 1, 1),
    )
    t = board["totals"]
    assert t["all"]["decrees"] == 1 and t["all"]["cases"] == 2
    assert t["all"]["applied"] == 11 and t["all"]["exact"] == 9 and t["all"]["not_applied"] == 3
    assert t["backtest"]["cases"] == 1 and t["live"]["cases"] == 1
    assert t["all"]["claude_calls"] == 2
    assert set(scoreboard.STATUSES) <= set(board["definitions"])
    assert "before" not in board["cases"][0]["articles"][0]


def test_site(tmp_path):
    cfg = Config(root=tmp_path, leyes=tmp_path / "leyes", work=tmp_path / ".work")
    (cfg.data / "casos").mkdir(parents=True)
    (cfg.data / "casos" / "2025-12-15-lgra.json").write_text(
        json.dumps(case("2025-12-15-lgra", 5776177, {"exact": 3, "mismatch": 1}))
    )
    scoreboard.write(
        cfg.data / "auditoria.json", scoreboard.build([case("2025-12-15-lgra", 5776177, {"exact": 3, "mismatch": 1})])
    )
    index = build_site(cfg).read_text(encoding="utf-8")
    assert '<html lang="es">' in index
    assert "75.0%" in index  # 3 of 4 applied articles exact
    assert "casos/2025-12-15-lgra.html" in index
    assert "fonts.googleapis" not in index
    page = (cfg.docs / "casos" / "2025-12-15-lgra.html").read_text(encoding="utf-8")
    assert "<del>de la IA.</del>" in page and "<ins>oficial.</ins>" in page
    assert "duda &lt;b&gt;" in page
    css = (cfg.docs / "assets" / "site.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css and "url(fonts/" in css
    assert (cfg.docs / "data" / "auditoria.json").exists()
