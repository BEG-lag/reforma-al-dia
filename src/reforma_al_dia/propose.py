"""Turn an amended law into a branch, a commit and a pull request in leyes-mx.

The commit follows the leyes-mx convention: authored and committed by the bot
``Diario Oficial (leyes-mx) <dof@leyes-mx.invalid>`` and dated on the DOF
publication (noon, Mexico City), so ``git log`` keeps the legal timeline. It
carries trailers that say who applied the text and from which DOF note. The
pull request is opened for a human to review; nothing is merged automatically.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import __version__
from .edits import Edit, Review
from .legal import article_key
from .pipeline import LawRun
from .sidof import dmy, note_url

log = logging.getLogger(__name__)

BOT_NAME = "Diario Oficial (leyes-mx)"
BOT_EMAIL = "dof@leyes-mx.invalid"
DOF_TIME = "12:00:00 -0600"
LABEL = "provisional"
TRAILER_APPLIED = "Applied-by: Claude (provisional until the official consolidated text)"

OPS_ES = {"reform": "reforma", "add": "adición", "repeal": "derogación"}
UNITS_ES = {
    "articulo": "artículo", "parrafo": "párrafo", "fraccion": "fracción", "inciso": "inciso",
    "subinciso": "subinciso", "numeral": "numeral", "apartado": "apartado", "base": "base",
    "epigrafe": "epígrafe", "otro": "otro",
}  # fmt: skip


def git(root: Path, *args: str, env: dict | None = None, input: str | None = None, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=root, env=env, input=input, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def bot_env(day: date) -> dict[str, str]:
    env = dict(os.environ)
    stamp = f"{day.isoformat()} {DOF_TIME}"
    env.update(
        GIT_AUTHOR_NAME=BOT_NAME,
        GIT_AUTHOR_EMAIL=BOT_EMAIL,
        GIT_COMMITTER_NAME=BOT_NAME,
        GIT_COMMITTER_EMAIL=BOT_EMAIL,
        GIT_AUTHOR_DATE=stamp,
        GIT_COMMITTER_DATE=stamp,
    )
    return env


def branch_name(day: date, slug: str) -> str:
    return f"reforma/{day.isoformat()}-{slug}"


@dataclass
class Proposal:
    slug: str
    branch: str
    commit: str
    title: str
    body: str
    command: list[str]
    numstat: tuple[int, int] = (0, 0)
    pushed: bool = False
    pr_url: str | None = None
    skipped: str | None = None
    extra: dict = field(default_factory=dict)

    def command_line(self) -> str:
        return " ".join(shlex.quote(x) for x in self.command)


# --------------------------------------------------------------------------
# Texts
# --------------------------------------------------------------------------


def commit_message(run: LawRun) -> str:
    d = run.decree
    url = note_url(d.code, d.day)
    title = d.official_title
    return (
        f"{run.law.abbreviation}: reforma DOF {dmy(d.day)} (provisional)\n\n"
        "Texto aplicado por IA a partir del decreto publicado en el Diario Oficial de la\n"
        "Federación. Es provisional hasta que la Cámara de Diputados publique el texto\n"
        "consolidado oficial; sólo el DOF es fuente oficial.\n\n"
        f"- {title} (DOF {dmy(d.day)})\n  {url}\n\n"
        f"{TRAILER_APPLIED}\n"
        f"DOF: {url}\n"
        f"DOF-Nota: {d.code}\n"
        f"Reforma-al-dia: {__version__}\n"
    )


def pr_title(run: LawRun) -> str:
    return f"{run.law.abbreviation}: reforma DOF {dmy(run.decree.day)} (provisional, aplicada por IA)"


def order_of(doc, key: str) -> int:
    return next((i for i, a in enumerate(doc.articles) if a.key == key), 10**6)


def _unit(unit_type: str, unit: str) -> str:
    if unit_type == "articulo":
        return "artículo completo"
    noun = UNITS_ES.get(unit_type, unit_type)
    return unit if noun in unit.lower() or unit_type == "otro" else f"{noun} {unit}".strip()


def _reviews_md(reviews: list[Review]) -> list[str]:
    if not reviews:
        return ["Ninguno: todas las unidades que nombra el decreto se aplicaron y pasaron las validaciones."]
    return [f"- [ ] **{r.article}**: {r.reason}" for r in reviews]


def pr_body(run: LawRun, *, numstat: tuple[int, int], base_articles: int, scoreboard_url: str) -> str:
    d = run.decree
    url = note_url(d.code, d.day)
    applied = run.applied
    assert applied is not None
    after_articles = len(applied.doc.articles)
    touched = sorted(applied.touched, key=lambda k: order_of(applied.doc, k))
    lines = [
        "> [!WARNING]",
        "> **Texto provisional aplicado por IA. Sólo el DOF es fuente oficial. No es asesoría legal.**",
        "> Este PR existe para que una persona lo revise antes de fusionarlo. Cuando la Cámara de Diputados",
        "> publique el texto consolidado oficial, la actualización diaria de leyes-mx lo reemplazará y",
        f"> [reforma-al-dia]({scoreboard_url}) publicará la comparación palabra por palabra.",
        "",
        f"**Decreto:** [{d.official_title}]({url})  ",
        f"**Publicado en el DOF:** {dmy(d.day)} · nota {d.code}  ",
        f"**Ley:** {run.law.title} (`{run.law.path}`)",
        "",
        "### Instrucción del decreto",
        "",
    ]
    for s in run.sections:
        lines.append("> " + s.instruction)
        lines.append("")
    lines += ["### Cambios aplicados", "", "| Artículo | Unidad | Operación |", "|---|---|---|"]
    order = {a.key: i for i, a in enumerate(applied.doc.articles)}

    def position(e: Edit) -> tuple[int, int]:
        return (order.get(article_key(e.article) or "", 10**6), e.from_block or e.insert_after or 0)

    for e in sorted(applied.applied, key=position):
        lines.append(f"| {e.article} | {_unit(e.unit_type, e.unit)} | {OPS_ES.get(e.operation, e.operation)} |")
    for div in applied.divisions:
        lines.append(
            f"| {div.before_article or ''} | {div.label} · {div.name} | {'nuevo encabezado' if div.operation == 'add' else 'denominación'} |"
        )
    lines += [
        "",
        f"### Requiere revisión humana ({len(run.reviews)})",
        "",
        *_reviews_md(run.reviews),
        "",
        "### Validaciones automáticas",
        "",
        f"- Artículos modificados: {len(touched)} ({', '.join(_labels(run, touched)[:12])}{'…' if len(touched) > 12 else ''}).",
        "- Ningún otro artículo cambió; ningún encabezado desapareció; la numeración sigue siendo válida.",
        f"- Artículos en la ley: {base_articles} → {after_articles} ({len(applied.added)} adicionados).",
        '- Notas editoriales ("Párrafo reformado DOF ...") y transitorios del decreto agregados por código determinista.',
        f"- Diff: +{numstat[0]} −{numstat[1]} líneas en `{run.law.path}`.",
        "",
        "<details><summary>Ediciones estructuradas devueltas por el modelo (JSON)</summary>",
        "",
        "```json",
        json.dumps(run.plan.to_json(), ensure_ascii=False, indent=1)[:50000],
        "```",
        "",
        "</details>",
        "",
        "---",
        f"Generado por [reforma-al-dia]({scoreboard_url}) {__version__} con Claude "
        f"({', '.join(sorted({c.model for c in run.calls if c.model})) or 'modelo por defecto'}), "
        f"{len(run.calls)} llamada(s), sin herramientas. Sólo el DOF es fuente oficial. No es asesoría legal.",
    ]
    return "\n".join(lines) + "\n"


def _labels(run: LawRun, keys: list[str]) -> list[str]:
    doc = run.applied.doc if run.applied else None
    out = []
    for k in keys:
        art = doc.article(k) if doc else None
        out.append(art.label if art else k)
    return out


# --------------------------------------------------------------------------
# Git and GitHub
# --------------------------------------------------------------------------


def remote_branch_exists(clone: Path, branch: str) -> bool:
    out = git(clone, "ls-remote", "--heads", "origin", branch, check=False)
    return bool(out.strip())


def existing_pr(repo: str, branch: str) -> str | None:
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--head", branch, "--state", "all", "--json", "url", "--limit", "1"],
            capture_output=True, text=True, check=False, timeout=60,
        )  # fmt: skip
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    rows = json.loads(proc.stdout or "[]")
    return rows[0]["url"] if rows else None


def branch_has_note(clone: Path, branch: str, code: int) -> bool:
    if not git(clone, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).strip():
        return False
    log_text = git(clone, "log", "--format=%B", f"origin/main..{branch}", check=False)
    return f"DOF-Nota: {code}" in log_text


def commit_proposal(clone: Path, run: LawRun, *, base: str = "origin/main") -> tuple[str, str, tuple[int, int]]:
    """Create or extend the branch for this law and date with one commit for this decree."""
    assert run.applied is not None
    branch = branch_name(run.decree.day, run.law.slug)
    exists = bool(git(clone, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).strip())
    git(clone, "checkout", "-q", "--force", branch if exists else "-B", *([] if exists else [branch, base]))
    path = clone / run.law.path
    path.write_text(run.applied.doc.render(), encoding="utf-8")
    git(clone, "add", "--", run.law.path)
    git(clone, "commit", "-q", "-F", "-", env=bot_env(run.decree.day), input=commit_message(run))
    sha = git(clone, "rev-parse", "HEAD").strip()
    stat = git(clone, "diff", "--numstat", f"{base}..{branch}", "--", run.law.path).split()
    numstat = (int(stat[0]), int(stat[1])) if len(stat) >= 2 and stat[0].isdigit() else (0, 0)
    git(clone, "checkout", "-q", "--force", "--detach", base)
    return branch, sha, numstat


def gh_command(repo: str, branch: str, title: str, body_file: Path) -> list[str]:
    return [
        "gh", "pr", "create", "--repo", repo, "--base", "main", "--head", branch,
        "--title", title, "--body-file", str(body_file), "--label", LABEL,
    ]  # fmt: skip


def publish(clone: Path, proposal: Proposal, repo: str) -> str:
    """Push the branch and open the pull request (only outside dry-run)."""
    git(clone, "push", "-u", "origin", f"{proposal.branch}:{proposal.branch}")
    subprocess.run(
        ["gh", "label", "create", LABEL, "--repo", repo, "--color", "ffb648", "--force",
         "--description", "AI-applied text pending the official consolidated text"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    proc = subprocess.run(proposal.command, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gh pr create failed: {proc.stderr.strip()}")
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""


__all__ = [
    "LABEL",
    "Proposal",
    "branch_has_note",
    "branch_name",
    "commit_message",
    "commit_proposal",
    "existing_pr",
    "gh_command",
    "pr_body",
    "pr_title",
    "publish",
    "remote_branch_exists",
]
