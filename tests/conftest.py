"""Shared fixtures. Tests never touch the network or call Claude.

* ``tests/fixtures/sidof``: real SIDOF responses for two decrees (DOF 15-12-2025 and 03-03-2026);
* ``tests/fixtures/leyes``: a miniature leyes-mx (catalog + excerpts of the LGRA and the CPEUM before each decree);
* ``tests/fixtures/official``: the same excerpts of the official consolidated text after each decree;
* ``tests/fixtures/claude``: the structured edits Claude returned for each decree in the backtest.

``scripts/record_fixtures.py`` regenerates them from real data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reforma_al_dia.catalog import load_catalog
from reforma_al_dia.claude import Response
from reforma_al_dia.sidof import Sidof

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def fix() -> Path:
    return FIX


@pytest.fixture
def laws():
    return load_catalog(FIX / "leyes")


@pytest.fixture
def sidof():
    with Sidof(FIX / "sidof", offline=True) as client:
        yield client


class RecordedClaude:
    """Stands in for :class:`reforma_al_dia.claude.Claude`: replays recorded outputs in order."""

    def __init__(self, outputs: list[dict]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []
        self.calls = 0

    def run(self, system: str, prompt: str, schema: dict) -> Response:
        self.prompts.append(prompt)
        data = self.outputs.pop(0) if self.outputs else {"edits": [], "divisions": [], "needs_review": []}
        return Response(data=data, cached=True, duration_ms=0, cost_usd=0.0, model="recorded", key="recorded")


@pytest.fixture
def recorded():
    def make(case_id: str) -> RecordedClaude:
        return RecordedClaude(json.loads((FIX / "claude" / f"{case_id}.json").read_text(encoding="utf-8")))

    return make
