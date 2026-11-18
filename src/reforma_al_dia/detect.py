"""Find, in one DOF date, the decrees that amend laws kept in leyes-mx.

Two passes. The table of contents of the day is filtered by title: a decree
that "reforma", "adiciona" or "deroga" and names a catalog law, or one with a
generic title ("diversos ordenamientos") that may hide one. Then each
candidate's full text is read: only decrees of the Congress (the enacting
formula "EL CONGRESO GENERAL ... DECRETA") count, which leaves out regulations
and executive decrees that merely cite a law, and the operative articles tell
which laws the decree really amends.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

from .catalog import Law, find_laws
from .decree import Decree, parse_decree
from .legal import fold
from .sidof import NoteRef, Sidof

log = logging.getLogger(__name__)

_AMENDS = re.compile(r"\b(?:reforma|adiciona|deroga|declara\w*\s+reformad)", re.IGNORECASE)
_GENERIC = re.compile(
    r"diversos ordenamientos|diversas leyes|diversos codigos|leyes federales|diversas disposiciones de diversos|"
    r"diversas disposiciones legales"
)
_CONGRESS = re.compile(r"congreso general de los estados unidos mexicanos|comision permanente|congreso de la union")


@dataclass
class Detected:
    note: NoteRef
    decree: Decree
    slugs: list[str]
    notes: list[str] = field(default_factory=list)


def title_candidates(notes: list[NoteRef], laws: list[Law]) -> list[NoteRef]:
    out = []
    for note in notes:
        title = fold(note.title)
        if not title.startswith("decreto") or not _AMENDS.search(title):
            continue
        if find_laws(note.title, laws) or _GENERIC.search(title):
            out.append(note)
    return out


def detect(sidof: Sidof, day: date, laws: list[Law]) -> list[Detected]:
    found: list[Detected] = []
    for ref in title_candidates(sidof.day_index(day), laws):
        note = sidof.note(ref.code)
        decree = parse_decree(note.code, note.day, note.title, note.html, laws)
        head = fold(" ".join(p.text for p in decree.paragraphs[:20]))
        if not _CONGRESS.search(head):
            log.info("skipping %s: not a decree of the Congress", ref.code)
            continue
        slugs = decree.slugs
        if not slugs:
            continue
        remarks = []
        if re.search(r"\bse\s+expiden?\s+(?:la|el|una|un)\s+(?:nueva\s+)?(?:ley|codigo)", fold(note.title)):
            remarks.append("the decree also issues a new law; new laws enter leyes-mx through its catalog, not here")
        unmatched = [s.instruction[:120] for s in decree.sections if s.slug is None]
        if unmatched:
            remarks.append(f"{len(unmatched)} operative article(s) amend laws outside the catalog")
        found.append(Detected(ref, decree, slugs, remarks))
    return found
