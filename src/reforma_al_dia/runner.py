"""The daily run: detect, apply, propose, audit. Idempotent and logged.

    reforma-al-dia run --date today            # dry run: branches and PR texts, nothing pushed
    reforma-al-dia run --date today --no-dry-run

A decree and a law are proposed at most once. Before doing anything the run
checks, in order: the public record in ``data/propuestas/``, a branch in the
leyes-mx remote that already carries the decree (``DOF-Nota:`` trailer), and an
existing pull request for that branch. Any of them means "already done".
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .backtest import case_record, write_case
from .catalog import Law, load_catalog
from .claude import Claude
from .config import Config
from .decree import parse_decree
from .detect import detect
from .edits import Review
from .history import law_at
from .lawdoc import LawDoc
from .pipeline import run_law
from .propose import (
    Proposal,
    branch_has_note,
    branch_name,
    commit_proposal,
    existing_pr,
    gh_command,
    git,
    pr_body,
    pr_title,
    publish,
    remote_branch_exists,
)
from .sidof import Sidof, dmy

log = logging.getLogger(__name__)
SCOREBOARD_URL = "https://beg-lag.github.io/reforma-al-dia/"


@dataclass
class Report:
    day: date
    dry_run: bool
    lines: list[str] = field(default_factory=list)
    failures: int = 0
    branches: set[str] = field(default_factory=set)  # local branches created by this run

    def add(self, line: str, *, failure: bool = False) -> None:
        self.lines.append(line)
        (log.error if failure else log.info)(line)
        self.failures += failure

    @property
    def ok(self) -> bool:
        return self.failures == 0

    def summary(self) -> str:
        head = f"reforma-al-dia {self.day.isoformat()} ({'dry run' if self.dry_run else 'live'})"
        return "\n".join([head, *[f"  {x}" for x in self.lines or ["no reform decrees of catalog laws"]]])


# --------------------------------------------------------------------------
# leyes-mx clone
# --------------------------------------------------------------------------


def ensure_clone(cfg: Config) -> None:
    if not (cfg.leyes / ".git").exists():
        cfg.leyes.parent.mkdir(parents=True, exist_ok=True)
        git(cfg.leyes.parent, "clone", "-q", f"https://github.com/{cfg.leyes_repo}.git", str(cfg.leyes))
    git(cfg.leyes, "fetch", "-q", "--prune", "origin")
    git(cfg.leyes, "checkout", "-q", "--force", "--detach", "origin/main")


def stale_reviews(clone: Path, law: Law, base: LawDoc, day: date) -> list[Review]:
    """Signs that the base text in leyes-mx is behind: then the AI would amend an outdated law."""
    last = base.meta.get("last_reform_dof")
    if not isinstance(last, date):
        return []
    out: list[Review] = []
    path = clone / "data" / "reformas" / f"{law.slug}.json"
    if path.exists():
        decrees = json.loads(path.read_text(encoding="utf-8")).get("decrees", [])
        missing = [d for d in decrees if last < date.fromisoformat(d["dof_date"]) < day]
        if missing:
            names = "; ".join(f"DOF {dmy(date.fromisoformat(d['dof_date']))}" for d in missing[:5])
            out.append(
                Review(
                    law.abbreviation,
                    f"El texto base (DOF {dmy(last)}) no incluye {len(missing)} decreto(s) anteriores ({names}); revisar contra el DOF.",
                    "pipeline",
                )
            )
    index = clone / "data" / "indice.json"
    listed = json.loads(index.read_text(encoding="utf-8")).get(law.slug) if index.exists() else None
    if listed and date.fromisoformat(listed) > last:
        out.append(
            Review(
                law.abbreviation,
                f"El índice de la Cámara registra la última reforma al DOF {dmy(date.fromisoformat(listed))}, pero el texto de leyes-mx es del DOF {dmy(last)}: el texto base puede estar desactualizado.",
                "pipeline",
            )
        )
    return out


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


def record_path(cfg: Config, day: date, slug: str, code: int, *, dry_run: bool = False) -> Path:
    """Public record of a real proposal; dry runs keep theirs in the local work folder."""
    folder = cfg.work / "dry-run" if dry_run else cfg.data / "propuestas"
    return folder / f"{day.isoformat()}-{slug}-{code}.json"


def write_record(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def run_day(cfg: Config, day: date, *, dry_run: bool, sidof: Sidof, claude: Claude) -> Report:
    report = Report(day, dry_run)
    ensure_clone(cfg)
    laws = load_catalog(cfg.leyes)
    by_slug = {law.slug: law for law in laws}
    base_commit = git(cfg.leyes, "rev-parse", "origin/main").strip()
    found = detect(sidof, day, laws)
    for det in found:
        for remark in det.notes:
            report.add(f"{det.note.code}: {remark}")
        for slug in det.slugs:
            law = by_slug[slug]
            _propose_one(cfg, report, law, laws, det.decree, base_commit, claude, dry_run=dry_run)
    for line in audit_live(cfg):
        report.add(line)
    return report


def _propose_one(cfg, report, law, laws, decree, base_commit, claude, *, dry_run) -> None:
    day, code = decree.day, decree.code
    tag = f"{law.abbreviation} DOF {dmy(day)} nota {code}"
    real = record_path(cfg, day, law.slug, code)
    path = record_path(cfg, day, law.slug, code, dry_run=dry_run)
    branch = branch_name(day, law.slug)
    if real.exists():
        report.add(f"{tag}: already proposed ({json.loads(real.read_text())['status']}), skipping")
        return
    on_remote = remote_branch_exists(cfg.leyes, branch)
    if on_remote:
        git(cfg.leyes, "fetch", "-q", "--force", "origin", f"{branch}:{branch}", check=False)
        if branch_has_note(cfg.leyes, branch, code):
            report.add(f"{tag}: branch {branch} already carries this decree, skipping")
            return
        if url := existing_pr(cfg.leyes_repo, branch):
            report.add(f"{tag}: adding this decree to the open pull request {url}")
    elif branch not in report.branches:
        # A local leftover (e.g. from a dry run) that was never pushed: start over from the base.
        git(cfg.leyes, "branch", "-q", "-D", branch, check=False)

    base = LawDoc.parse(law_at(cfg.leyes, base_commit, law.slug))
    last = base.meta.get("last_reform_dof")
    record = {"id": path.stem, "day": day.isoformat(), "code": code, "slug": law.slug, "abbreviation": law.abbreviation,
              "decree_title": decree.title, "base_commit": base_commit, "branch": branch, "created": datetime.now().isoformat(timespec="seconds")}  # fmt: skip
    if isinstance(last, date) and last >= day:
        record["status"] = "already_official"
        write_record(path, record)
        report.add(f"{tag}: leyes-mx already has the official text (last reform {last}); nothing to propose")
        return

    run = run_law(base, law, decree, claude, laws, workers=cfg.workers)
    run.reviews = stale_reviews(cfg.leyes, law, base, day) + run.reviews
    if not run.ok:
        record["status"] = "failed"
        record["needs_review"] = [r.to_json() for r in run.reviews]
        write_record(path, record)
        report.add(f"{tag}: no edit could be applied; {len(run.reviews)} item(s) for review", failure=True)
        return

    branch, sha, numstat = commit_proposal(cfg.leyes, run)
    report.branches.add(branch)
    body = pr_body(run, numstat=numstat, base_articles=len(base.articles), scoreboard_url=SCOREBOARD_URL)
    body_file = cfg.work / "pr" / f"{branch.replace('/', '_')}-{code}.md"
    body_file.parent.mkdir(parents=True, exist_ok=True)
    body_file.write_text(body, encoding="utf-8")
    proposal = Proposal(
        law.slug,
        branch,
        sha,
        pr_title(run),
        body,
        gh_command(cfg.leyes_repo, branch, pr_title(run), body_file),
        numstat,
    )
    if dry_run:
        report.add(f"{tag}: DRY RUN, committed {sha[:9]} on local branch {branch} (+{numstat[0]} -{numstat[1]})")
        report.add(f"{tag}: would run: git push -u origin {branch} && {proposal.command_line()}")
    else:
        proposal.pr_url = publish(cfg.leyes, proposal, cfg.leyes_repo)
        report.add(f"{tag}: pull request opened: {proposal.pr_url}")
    ai_text = run.applied.doc.render() if run.applied else ""
    record.update(
        status="proposed" if not dry_run else "dry_run",
        commit=sha,
        pr=proposal.pr_url,
        title=proposal.title,
        body_file=str(body_file),
        numstat=list(numstat),
        edits=len(run.applied.applied) if run.applied else 0,
        needs_review=[r.to_json() for r in run.reviews],
        ai_sha256=hashlib.sha256(ai_text.encode()).hexdigest(),
        calls=[c.__dict__ for c in run.calls],
    )
    write_record(path, record)


# --------------------------------------------------------------------------
# Live audit
# --------------------------------------------------------------------------

_DECREE_LINE = re.compile(r"^- .+ \(DOF (\d{2}-\d{2}-\d{4})\)", re.MULTILINE)


def find_official(clone: Path, slug: str, day: date) -> tuple[str, list[str]] | None:
    """First commit on origin/main with the official text that reflects a decree of ``day``."""
    log_text = git(clone, "log", "--reverse", "--format=%H%x1f%B%x1e", "origin/main", "--", f"leyes/{slug}.md")
    for entry in log_text.split("\x1e"):
        entry = entry.strip("\n")
        if not entry:
            continue
        sha, _, body = entry.partition("\x1f")
        if "Applied-by: Claude" in body:
            continue
        meta = LawDoc.parse(law_at(clone, sha.strip(), slug)).meta
        last = meta.get("last_reform_dof")
        if isinstance(last, date) and last >= day:
            return sha.strip(), _DECREE_LINE.findall(body)
    return None


def audit_live(cfg: Config) -> list[str]:
    folder = cfg.data / "propuestas"
    if not folder.exists():
        return []
    lines: list[str] = []
    laws = load_catalog(cfg.leyes)
    by_slug = {law.slug: law for law in laws}
    claude = Claude(cfg.claude_cache, binary=cfg.claude_binary, model=cfg.model, effort=cfg.effort, offline=True)
    with Sidof(cfg.sidof_cache) as sidof:
        for path in sorted(folder.glob("*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec.get("status") != "proposed" or rec.get("audited"):
                continue
            day = date.fromisoformat(rec["day"])
            found = find_official(cfg.leyes, rec["slug"], day)
            if found is None:
                lines.append(f"{rec['id']}: official consolidated text not published yet")
                continue
            official_sha, decrees = found
            note = sidof.note(rec["code"])
            decree = parse_decree(note.code, note.day, note.title, note.html, laws)
            before = LawDoc.parse(law_at(cfg.leyes, rec["base_commit"], rec["slug"]))
            run = run_law(before, by_slug[rec["slug"]], decree, claude, laws)
            ai_text = run.applied.doc.render() if run.applied else ""
            official = LawDoc.parse(law_at(cfg.leyes, official_sha, rec["slug"]))
            grouped = len(decrees) > 1
            case = case_record(
                run, before, official, pair=None, source="live",
                extra={
                    "id": rec["id"],
                    "official": {"commit": official_sha, "date": str(official.meta.get("last_reform_dof")), "grouped": grouped},
                    "base": {"commit": rec["base_commit"]},
                    "proposal": {"branch": rec["branch"], "pr": rec.get("pr"), "commit": rec.get("commit"),
                                 "reproduced": hashlib.sha256(ai_text.encode()).hexdigest() == rec.get("ai_sha256")},
                },
            )  # fmt: skip
            write_case(cfg.data, case)
            rec["audited"] = case["id"]
            write_record(path, rec)
            c = case["counts"]
            lines.append(
                f"{rec['id']}: audited against {official_sha[:9]}: exact {c['exact']}, normalized {c['normalized']}, "
                f"mismatch {c['mismatch']}, not applied {c['not_applied']}{' (grouped with other decrees)' if grouped else ''}"
            )
    return lines
