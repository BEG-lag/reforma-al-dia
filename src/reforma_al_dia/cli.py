"""Command line: ``reforma-al-dia run | detect | audit | backtest | site``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta

from . import __version__
from .config import Config, setup_logging

log = logging.getLogger("reforma_al_dia")


def _date(value: str) -> date:
    from .sidof import today_mx

    if value == "today":
        return today_mx()
    if value == "yesterday":
        return today_mx() - timedelta(days=1)
    return date.fromisoformat(value)


def _claude(cfg: Config, offline: bool = False):
    from .claude import Claude

    return Claude(cfg.claude_cache, binary=cfg.claude_binary, model=cfg.model, effort=cfg.effort, offline=offline)


def _sidof(cfg: Config, offline: bool = False):
    from .sidof import Sidof

    return Sidof(cfg.sidof_cache, offline=offline)


# --------------------------------------------------------------------------
# run / detect / audit
# --------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace, cfg: Config) -> int:
    from .runner import run_day

    report = run_day(cfg, _date(args.date), dry_run=not args.no_dry_run, sidof=_sidof(cfg), claude=_claude(cfg))
    print(report.summary())
    return 0 if report.ok else 1


def cmd_detect(args: argparse.Namespace, cfg: Config) -> int:
    from .catalog import load_catalog
    from .detect import detect

    laws = load_catalog(cfg.leyes)
    with _sidof(cfg) as sidof:
        found = detect(sidof, _date(args.date), laws)
    for d in found:
        print(f"{d.note.day} {d.note.code} {','.join(d.slugs) or '-'} {d.note.title}")
        for note in d.notes:
            print(f"    {note}")
    if not found:
        print("no reform decrees of laws in the catalog")
    return 0


def cmd_audit(args: argparse.Namespace, cfg: Config) -> int:
    from .runner import audit_live

    results = audit_live(cfg)
    for line in results:
        print(line)
    return _scoreboard(cfg)


# --------------------------------------------------------------------------
# backtest
# --------------------------------------------------------------------------


def cmd_backtest_screen(args: argparse.Namespace, cfg: Config) -> int:
    from .backtest import screen
    from .catalog import load_catalog

    laws = load_catalog(cfg.leyes)
    with _sidof(cfg) as sidof:
        rows = screen(
            cfg.leyes,
            laws,
            sidof,
            date.fromisoformat(args.since),
            date.fromisoformat(args.until) if args.until else None,
        )
    out = {
        "generated": date.today().isoformat(),
        "since": args.since,
        "rule": "pairs of consecutive leyes-mx versions with exactly one decree in between",
        "selected": [s.pair.to_json() for s in rows if s.reason is None],
        "skipped": [{**s.pair.to_json(), "reason": s.reason} for s in rows if s.reason],
    }
    path = cfg.data / "backtest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for s in rows:
        print(
            f"{'RUN ' if s.reason is None else 'skip'} {s.pair.id:24} {s.pair.note_code or '-':>8} {s.reason or s.pair.decree_title[:80]}"
        )
    print(f"{len(out['selected'])} selected, {len(out['skipped'])} skipped -> {path}")
    return 0


def cmd_backtest_run(args: argparse.Namespace, cfg: Config) -> int:
    from .backtest import run_case, write_case
    from .catalog import load_catalog
    from .history import Pair

    laws = {law.slug: law for law in load_catalog(cfg.leyes)}
    selection = json.loads((cfg.data / "backtest.json").read_text(encoding="utf-8"))["selected"]
    if args.only:
        selection = [s for s in selection if s["id"] in args.only]
    claude = _claude(cfg, offline=args.offline)
    failures = 0
    with _sidof(cfg) as sidof:
        for row in selection:
            pair = Pair(
                slug=row["slug"],
                abbreviation=row["abbreviation"],
                before=row["before"],
                after=row["after"],
                version_date=date.fromisoformat(row["version_date"]),
                decree_date=date.fromisoformat(row["decree_date"]),
                decree_title=row["decree_title"],
                decree_pdf=row.get("decree_pdf"),
                note_code=row.get("note_code"),
            )
            started = time.monotonic()
            try:
                record = run_case(
                    cfg.leyes, laws[pair.slug], list(laws.values()), pair, sidof, claude, workers=cfg.workers
                )
            except Exception as exc:  # keep going: one broken case must not stop the backtest
                log.exception("case %s failed", pair.id)
                print(f"FAIL {pair.id}: {exc}")
                failures += 1
                continue
            errors = [c["error"] for c in record["calls"] if c.get("error")]
            if errors:
                print(f"FAIL {pair.id}: {len(errors)} model call(s) failed: {errors[0][:160]}", flush=True)
                failures += 1
                if any("usage limit" in e for e in errors):
                    print("stopping: the Claude account hit its usage limit; rerun later (finished cases are cached)")
                    break
                continue
            write_case(cfg.data, record)
            c = record["counts"]
            print(
                f"{pair.id:24} exact {c['exact']:3} norm {c['normalized']:3} mism {c['mismatch']:3} "
                f"review {c['not_applied']:3} outside {c['outside_decree']:3} | trans {record['transitorios']:10} "
                f"| identical {record['body_identical']!s:5} | calls {len(record['calls'])} | {time.monotonic() - started:.0f}s",
                flush=True,
            )
    print(f"claude calls made now: {claude.calls}")
    _scoreboard(cfg)
    return 1 if failures else 0


def cmd_backtest_pr(args: argparse.Namespace, cfg: Config) -> int:
    """Print the commit message and pull request a backtest case would have produced (offline)."""
    from .catalog import load_catalog
    from .decree import parse_decree
    from .history import law_at
    from .lawdoc import LawDoc
    from .pipeline import run_law
    from .propose import commit_message, pr_body, pr_title
    from .runner import SCOREBOARD_URL

    case = json.loads((cfg.data / "casos" / f"{args.id}.json").read_text(encoding="utf-8"))
    laws = load_catalog(cfg.leyes)
    law = next(x for x in laws if x.slug == case["law"]["slug"])
    with _sidof(cfg) as sidof:
        note = sidof.note(case["decree"]["code"])
    decree = parse_decree(note.code, note.day, note.title, note.html, laws)
    before = LawDoc.parse(law_at(cfg.leyes, case["base"]["commit"], law.slug))
    run = run_law(before, law, decree, _claude(cfg, offline=True), laws)
    after = run.applied.doc.render() if run.applied else before.render()
    added = removed = 0
    import difflib

    for line in difflib.unified_diff(before.render().splitlines(), after.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    print("=== commit message ===")
    print(commit_message(run))
    print("=== pull request title ===")
    print(pr_title(run))
    print("=== pull request body ===")
    print(pr_body(run, numstat=(added, removed), base_articles=len(before.articles), scoreboard_url=SCOREBOARD_URL))
    return 0


def _scoreboard(cfg: Config) -> int:
    from . import scoreboard
    from .backtest import load_cases

    board = scoreboard.build(load_cases(cfg.data))
    scoreboard.write(cfg.data / "auditoria.json", board)
    t = board["totals"]["all"]
    print(
        f"scoreboard: {t['cases']} cases, {t['applied']} applied articles: exact {t['exact']}, "
        f"normalized {t['normalized']}, mismatch {t['mismatch']}; not applied {t['not_applied']}; "
        f"outside decree {t['outside_decree']}"
    )
    return 0


def cmd_scoreboard(args: argparse.Namespace, cfg: Config) -> int:
    return _scoreboard(cfg)


def cmd_site(args: argparse.Namespace, cfg: Config) -> int:
    from .site import build_site

    path = build_site(cfg)
    print(f"site written to {path}")
    return 0


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reforma-al-dia", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--root", help="this repository (default: current directory)")
    parser.add_argument(
        "--leyes", help="clone of leyes-mx (default: ~/.cache/reforma-al-dia/leyes-mx or $REFORMA_LEYES)"
    )
    parser.add_argument("--work", help="local state folder (default: ./.work)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="detect, apply, propose and audit for one DOF date")
    p.add_argument("--date", default="today", help="YYYY-MM-DD, today or yesterday (Mexico City time)")
    p.add_argument("--no-dry-run", action="store_true", help="push the branch and open the pull request for real")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("detect", help="list reform decrees of catalog laws published on a date")
    p.add_argument("--date", default="today")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("audit", help="compare proposals with official texts that have arrived; rebuild the scoreboard")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("backtest", help="replay past reforms")
    bsub = p.add_subparsers(dest="bcmd", required=True)
    q = bsub.add_parser("screen", help="find clean single-decree pairs in the leyes-mx history")
    q.add_argument("--since", default="2019-01-01")
    q.add_argument("--until")
    q.set_defaults(func=cmd_backtest_screen)
    q = bsub.add_parser("run", help="run the pipeline on the selected pairs and audit each one")
    q.add_argument("--only", nargs="*", help="case ids to run")
    q.add_argument("--offline", action="store_true", help="use recorded Claude responses only")
    q.set_defaults(func=cmd_backtest_run)

    q = bsub.add_parser("pr", help="print the commit and pull request a backtest case would have produced")
    q.add_argument("id")
    q.set_defaults(func=cmd_backtest_pr)

    p = sub.add_parser("scoreboard", help="rebuild data/auditoria.json from data/casos/")
    p.set_defaults(func=cmd_scoreboard)

    p = sub.add_parser("site", help="build the scoreboard site in docs/")
    p.set_defaults(func=cmd_site)

    args = parser.parse_args(argv)
    cfg = Config.load(args.root, args.leyes, args.work)
    setup_logging(cfg, args.verbose)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
