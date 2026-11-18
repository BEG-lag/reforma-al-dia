"""``data/auditoria.json``: the public accuracy scoreboard, built from every audited case.

Schema (version 1)::

    {
      "schema": 1,
      "as_of": "YYYY-MM-DD",           # latest DOF date among the cases (deterministic)
      "definitions": {status: description, ...},
      "totals": {                      # one block per source: "backtest", "live", "all"
        "backtest": {
          "decrees": int,              # distinct DOF notes
          "cases": int,                # (decree, law) pairs
          "articles": int,             # articles in play, all statuses
          "applied": int,              # exact + normalized + mismatch
          "exact": int, "normalized": int, "mismatch": int,
          "not_applied": int,          # sent to human review instead of guessed
          "outside_decree": int,       # official changes the decree does not name (excluded)
          "mismatch_causes": {"repeal_placeholder", "numbering", "official_annotation", "text"},
          "notes_match": int,          # applied articles whose editorial notes also match
          "body_identical": int,       # cases whose whole law body equals the official one
          "transitorios_exact": int,
          "claude_calls": int, "claude_seconds": float, "claude_cost_usd": float
        }, ...
      },
      "cases": [
        {
          "id": "2025-12-15-lgra", "source": "backtest" | "live",
          "law": {"slug", "title", "abbreviation"},
          "decree": {"code", "title", "date", "url", "pdf"},
          "official": {"commit", "date", "same_day"},
          "proposal": {"branch", "pr", "commit"} | null,
          "counts": {status: int, "notes_match": int},
          "transitorios": status, "body_identical": bool,
          "needs_review": [{"article", "reason", "source"}],
          "articles": [{"key", "label", "status", "cause", "notes_match", "diff": [[op, text], ...]}]
        }
      ]
    }

``diff`` is a word-level diff from the AI text (or, for articles not applied,
from the previous text) to the official text: ``"="`` unchanged words, ``"-"``
words only in the AI text, ``"+"`` words only in the official text. Full texts
of every article (before, AI, official) are in ``data/casos/<id>.json``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .audit import CAUSES, STATUSES

DEFINITIONS = {
    "exact": "The legal text the AI applied is identical, byte for byte, to the official consolidated text.",
    "normalized": "Identical after normalizing whitespace, punctuation, quotes, dashes and Markdown escaping.",
    "mismatch": "Different words. The word-level diff is published.",
    "not_applied": "The decree changed the article but the AI did not apply it: it was sent to human review instead of guessed.",
    "cause": "For mismatches: repeal_placeholder (only 'Se deroga' placeholders differ), numbering (only unit labels differ), official_annotation (the official text only adds Cámara annotations), text (anything else).",
    "outside_decree": "The official text changed an article the decree does not name (Cámara corrections or source noise). Excluded from accuracy.",
}


def _totals(cases: list[dict]) -> dict:
    t: dict = {"decrees": len({c["decree"]["code"] for c in cases}), "cases": len(cases)}
    for s in STATUSES:
        t[s] = sum(c["counts"].get(s, 0) for c in cases)
    t["applied"] = t["exact"] + t["normalized"] + t["mismatch"]
    t["articles"] = t["applied"] + t["not_applied"] + t["outside_decree"]
    t["mismatch_causes"] = {k: sum(c["counts"].get(f"mismatch_{k}", 0) for c in cases) for k in CAUSES}
    t["notes_match"] = sum(c["counts"].get("notes_match", 0) for c in cases)
    t["body_identical"] = sum(1 for c in cases if c.get("body_identical"))
    t["transitorios_exact"] = sum(1 for c in cases if c.get("transitorios") == "exact")
    calls = [x for c in cases for x in c.get("calls", [])]
    t["claude_calls"] = len(calls)
    t["claude_seconds"] = round(sum(x.get("duration_ms", 0) for x in calls) / 1000, 1)
    t["claude_cost_usd"] = round(sum(x.get("cost_usd", 0.0) for x in calls), 2)
    return t


def build(cases: list[dict], today: date | None = None) -> dict:
    cases = sorted(cases, key=lambda c: (c["decree"]["date"], c["law"]["slug"]), reverse=True)
    slim = []
    for c in cases:
        slim.append(
            {
                "id": c["id"],
                "source": c["source"],
                "law": c["law"],
                "decree": {k: c["decree"].get(k) for k in ("code", "title", "date", "url", "pdf")},
                "official": c.get("official"),
                "proposal": c.get("proposal"),
                "counts": c["counts"],
                "transitorios": c.get("transitorios"),
                "body_identical": c.get("body_identical"),
                "needs_review": c.get("needs_review", []),
                "articles": [
                    {k: a.get(k) for k in ("key", "label", "status", "cause", "notes_match", "diff")}
                    for a in c.get("articles", [])
                ],
            }
        )
    return {
        "schema": 1,
        "as_of": max((c["decree"]["date"] for c in cases), default=(today or date.today()).isoformat()),
        "definitions": DEFINITIONS,
        "totals": {
            "backtest": _totals([c for c in cases if c["source"] == "backtest"]),
            "live": _totals([c for c in cases if c["source"] == "live"]),
            "all": _totals(cases),
        },
        "cases": slim,
    }


def write(path: Path, board: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
