"""A small, polite client for the public SIDOF API of the Diario Oficial.

Endpoints and quirks are documented in BEG-lag/mcp-dof (``docs/api.md``):
``GET /notas/DD-MM-YYYY`` lists every note of a date and ``GET /notas/nota/{code}``
returns one note with its full HTML. ``Accept: application/json`` alone gets
HTTP 406, an impossible date gets HTTP 500, and a day without an edition is an
empty 200. Responses are cached on disk: past editions do not change, so a
backtest or a rerun never asks the server twice for the same thing.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import __version__

API = "https://sidof.segob.gob.mx/dof/sidof"
USER_AGENT = f"reforma-al-dia/{__version__} (+https://github.com/BEG-lag/reforma-al-dia)"
MX = ZoneInfo("America/Mexico_City")
RETRY = {429, 500, 502, 503, 504}

log = logging.getLogger(__name__)


class SidofError(RuntimeError):
    pass


def today_mx() -> date:
    return datetime.now(MX).date()


def dmy(day: date) -> str:
    return day.strftime("%d-%m-%Y")


def note_url(code: int, day: date) -> str:
    """The official page of a DOF note (bare domain: www.dof.gob.mx has served a wrong certificate)."""
    return f"https://dof.gob.mx/nota_detalle.php?codigo={code}&fecha={day.strftime('%d/%m/%Y')}"


@dataclass
class NoteRef:
    code: int
    title: str
    day: date
    edition: str  # MAT, VES, EXT
    agency: str
    order: float

    @property
    def url(self) -> str:
        return note_url(self.code, self.day)


@dataclass
class Note:
    code: int
    title: str
    day: date
    html: str

    @property
    def url(self) -> str:
        return note_url(self.code, self.day)


class Sidof:
    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        offline: bool = False,
        min_interval: float = 0.5,
        timeout: float = 30.0,
        retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.offline = offline
        self.min_interval = min_interval
        self.retries = retries
        self.requests = 0
        self._last = 0.0
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*;q=0.8"},
            transport=transport,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Sidof:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ HTTP

    def _cache_path(self, path: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (path.strip("/").replace("/", "__") + ".json")

    def get(self, path: str, *, cache: bool = True) -> dict:
        cached = self._cache_path(path)
        if cache and cached is not None and cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))
        if self.offline:
            raise SidofError(f"offline and not cached: {path}")
        url = f"{API}/{path.lstrip('/')}"
        error = "no attempt"
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(min(30.0, 1.5 * 2 ** (attempt - 1)))
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self._http.get(url)
                self.requests += 1
            except httpx.TransportError as exc:
                error = f"{type(exc).__name__}: {exc}"
                continue
            if response.status_code in RETRY:
                error = f"HTTP {response.status_code}"
                continue
            if response.status_code >= 400:
                raise SidofError(f"SIDOF answered HTTP {response.status_code} for {url}")
            payload = response.json()
            if cached is not None and cache:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return payload
        raise SidofError(f"SIDOF is not responding ({error}): {url}")

    # ------------------------------------------------------------- endpoints

    def day_index(self, day: date) -> list[NoteRef]:
        """Every titled note published on ``day``, in edition order."""
        # Today's and yesterday's editions can still grow (vespertinas, extraordinarias).
        fresh = (today_mx() - day).days <= 1
        payload = self.get(f"notas/{dmy(day)}", cache=not fresh)
        notes: list[NoteRef] = []
        for edition, key in (("MAT", "NotasMatutinas"), ("VES", "NotasVespertinas"), ("EXT", "NotasExtraordinarias")):
            for row in payload.get(key) or []:
                title = (row.get("titulo") or "").strip()
                if not title or title == "null":
                    continue
                notes.append(
                    NoteRef(
                        code=int(row["codNota"]),
                        title=" ".join(title.split()),
                        day=day,
                        edition=edition,
                        agency=str(row.get("codOrgaDos") or ""),
                        order=float(row.get("orden") or 0),
                    )
                )
        rank = {"MAT": 0, "VES": 1, "EXT": 2}
        return sorted(notes, key=lambda n: (rank[n.edition], n.order, n.code))

    def note(self, code: int) -> Note:
        payload = self.get(f"notas/nota/{code}")
        row = payload.get("Nota")
        if not row:
            raise SidofError(f"there is no DOF note {code}")
        d, m, y = (int(x) for x in str(row["fecha"]).split("-"))
        return Note(code=int(row["codNota"]), title=" ".join(str(row["titulo"]).split()), day=date(y, m, d),
                    html=row.get("cadenaContenido") or "")  # fmt: skip
