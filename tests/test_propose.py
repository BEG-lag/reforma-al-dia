import json
import shutil
import subprocess
from datetime import date

import pytest

from reforma_al_dia.config import Config
from reforma_al_dia.decree import parse_decree
from reforma_al_dia.lawdoc import LawDoc
from reforma_al_dia.pipeline import run_law
from reforma_al_dia.propose import commit_proposal, gh_command, pr_body, pr_title
from reforma_al_dia.runner import Report, _propose_one, record_path


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def clone(tmp_path, fix):
    """A leyes-mx clone with an origin, holding the fixture laws."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "leyes-mx"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    shutil.copytree(fix / "leyes", work)
    sh(work, "git", "init", "-q", "-b", "main")
    sh(work, "git", "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    sh(work, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "laws")
    sh(work, "git", "remote", "add", "origin", str(origin))
    sh(work, "git", "push", "-q", "origin", "main")
    sh(work, "git", "fetch", "-q", "origin")
    return work


@pytest.fixture
def lgra_run(fix, laws, sidof, recorded):
    note = sidof.note(5776177)
    decree = parse_decree(note.code, note.day, note.title, note.html, laws)
    before = LawDoc.parse((fix / "leyes" / "leyes" / "lgra.md").read_text(encoding="utf-8"))
    law = next(x for x in laws if x.slug == "lgra")
    return run_law(before, law, decree, recorded("2025-12-15-lgra"), laws), before


def test_commit_is_by_the_bot_on_the_dof_date_with_trailers(clone, lgra_run):
    run, _ = lgra_run
    branch, _sha, numstat = commit_proposal(clone, run)
    assert branch == "reforma/2025-12-15-lgra"
    meta = sh(clone, "git", "log", "-1", "--format=%an <%ae>|%cn|%ad|%B", "--date=iso", branch)
    author, committer, when, message = meta.split("|", 3)
    assert author == "Diario Oficial (leyes-mx) <dof@leyes-mx.invalid>"
    assert committer == "Diario Oficial (leyes-mx)"
    assert when.startswith("2025-12-15 12:00:00 -0600")
    assert message.startswith("LGRA: reforma DOF 15-12-2025 (provisional)")
    trailers = message.strip().splitlines()[-4:]
    assert trailers[:3] == [
        "Applied-by: Claude (provisional until the official consolidated text)",
        "DOF: https://dof.gob.mx/nota_detalle.php?codigo=5776177&fecha=15/12/2025",
        "DOF-Nota: 5776177",
    ]
    assert numstat[0] > 0
    changed = sh(clone, "git", "diff", "--name-only", f"origin/main..{branch}").split()
    assert changed == ["leyes/lgra.md"]


def test_pr_text(lgra_run, tmp_path):
    run, before = lgra_run
    body = pr_body(run, numstat=(27, 3), base_articles=len(before.articles), scoreboard_url="https://example.invalid/")
    assert body.startswith("> [!WARNING]")
    assert "Sólo el DOF es fuente oficial. No es asesoría legal." in body
    assert "https://dof.gob.mx/nota_detalle.php?codigo=5776177&fecha=15/12/2025" in body
    assert "| Artículo 64 Quáter. | artículo completo | adición |" in body
    assert "### Requiere revisión humana (0)" in body
    assert "\u2014" not in body
    title = pr_title(run)
    assert title == "LGRA: reforma DOF 15-12-2025 (provisional, aplicada por IA)"
    cmd = gh_command("BEG-lag/leyes-mx", "reforma/2025-12-15-lgra", title, tmp_path / "body.md")
    assert cmd[:3] == ["gh", "pr", "create"] and "--label" in cmd and "provisional" in cmd


def test_runner_never_proposes_the_same_decree_twice(tmp_path, clone, lgra_run, laws):
    run, _ = lgra_run
    cfg = Config(root=tmp_path / "repo", leyes=clone, work=tmp_path / "work")
    real = record_path(cfg, run.decree.day, "lgra", run.decree.code)
    real.parent.mkdir(parents=True)
    real.write_text(json.dumps({"status": "proposed"}))
    report = Report(date(2025, 12, 15), dry_run=False)

    class NoClaude:
        def run(self, *a):
            raise AssertionError("must not be called")

    law = next(x for x in laws if x.slug == "lgra")
    _propose_one(cfg, report, law, laws, run.decree, "origin/main", NoClaude(), dry_run=False)
    assert "already proposed" in report.lines[0]


def test_dry_run_commits_locally_and_records_outside_data(tmp_path, clone, lgra_run, laws, recorded):
    run, _ = lgra_run
    cfg = Config(root=tmp_path / "repo", leyes=clone, work=tmp_path / "work")
    law = next(x for x in laws if x.slug == "lgra")
    base = sh(clone, "git", "rev-parse", "origin/main").strip()
    for _ in range(2):  # twice: the second dry run starts over instead of stacking commits
        report = Report(run.decree.day, dry_run=True)
        _propose_one(cfg, report, law, laws, run.decree, base, recorded("2025-12-15-lgra"), dry_run=True)
        assert any("DRY RUN" in line for line in report.lines)
    commits = sh(clone, "git", "rev-list", "--count", "origin/main..reforma/2025-12-15-lgra").strip()
    assert commits == "1"
    assert not (cfg.data / "propuestas").exists()
    assert record_path(cfg, run.decree.day, "lgra", run.decree.code, dry_run=True).exists()
    assert not sh(clone, "git", "ls-remote", "--heads", "origin", "reforma/2025-12-15-lgra").strip()
