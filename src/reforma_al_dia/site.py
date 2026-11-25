"""The public scoreboard: a static site in ``docs/`` (GitHub Pages, no build step, no CDN).

* ``docs/index.html``: the idea in ten seconds, the accuracy numbers, every
  decree with its links, and the anatomy of one reform.
* ``docs/casos/<id>.html``: one page per case, article by article: the text
  before, what the AI applied, the official text, and the word diff.
* ``docs/data/auditoria.json``: the scoreboard data, for anyone to reuse.
"""

from __future__ import annotations

import json
import shutil
from html import escape
from pathlib import Path

from . import __version__
from .backtest import load_cases
from .config import LEYES_URL, Config
from .scoreboard import build

STATUS_ES = {
    "exact": "exacto",
    "normalized": "normalizado",
    "mismatch": "diferente",
    "not_applied": "a revisión",
    "outside_decree": "fuera del decreto",
    "unavailable": "n/d",
}
CAUSE_ES = {
    "repeal_placeholder": "marcadores «Se deroga»",
    "numbering": "numeración",
    "official_annotation": "anotación de la Cámara",
    "text": "texto",
}
ANATOMY = ("2026-03-03-cpeum", "123")  # the 40-hour work week: Artículo 123, Apartado A, fracción IV

CSS = """\
@font-face{font-family:"JetBrains Mono";font-weight:400;font-display:swap;src:url(FONTS/jetbrains-mono-latin-400-normal.woff2) format("woff2")}
@font-face{font-family:"JetBrains Mono";font-weight:700;font-display:swap;src:url(FONTS/jetbrains-mono-latin-700-normal.woff2) format("woff2")}
@font-face{font-family:Inter;font-weight:400;font-display:swap;src:url(FONTS/inter-latin-400-normal.woff2) format("woff2")}
@font-face{font-family:Inter;font-weight:600;font-display:swap;src:url(FONTS/inter-latin-600-normal.woff2) format("woff2")}
@font-face{font-family:"Cormorant Garamond";font-style:italic;font-weight:500;font-display:swap;src:url(FONTS/cormorant-garamond-latin-500-italic.woff2) format("woff2")}
:root{--ink:#0a0c10;--ink-2:#11141b;--ink-3:#191d27;--paper:#eceee6;--muted:#969dab;--lime:#cfff45;--amber:#ffb648;
--coral:#ff5d47;--cyan:#7fd8ff;--line:rgba(236,238,230,.1);--mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--sans:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--serif:"Cormorant Garamond",Georgia,"Times New Roman",serif;color-scheme:dark}
*{box-sizing:border-box}
html{background:var(--ink)}
body{margin:0;color:var(--paper);background:var(--ink);font:15px/1.6 var(--sans);-webkit-font-smoothing:antialiased;
background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
background-size:48px 48px;background-position:-1px -1px}
a{color:inherit;text-decoration-color:rgba(207,255,69,.5);text-underline-offset:3px}
a:hover{color:var(--lime)}
:focus-visible{outline:2px solid var(--lime);outline-offset:2px}
.skip{position:absolute;left:-999px;top:8px;background:var(--lime);color:var(--ink);padding:6px 10px;font:600 13px var(--mono)}
.skip:focus{left:8px;z-index:9}
.wrap{max-width:1160px;margin:0 auto;padding:0 16px}
header.top{padding:48px 0 28px;border-bottom:1px solid var(--line)}
.kicker{font:400 12px/1.4 var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.kicker b{color:var(--lime);font-weight:400}
/* PIVOTEK logo at the end of the kicker line (orange on ink; isotype on narrow screens) */
.kicker{display:flex;align-items:center}
.kicker>span{min-width:0}
.pvk{display:flex;align-items:center;flex:none;height:22px;margin:-3px 0 -3px 16px;padding-left:16px;border-left:1px solid var(--line);text-decoration:none}
.pvk:hover{text-decoration:none;opacity:.85}
.pvk img{display:block;transform:translateY(2px)}
.pvk-iso{display:none!important}
@media(max-width:360px){.pvk-logo{display:none!important}.pvk-iso{display:block!important}}
h1{font:700 clamp(38px,8vw,80px)/1 var(--mono);letter-spacing:-.04em;margin:16px 0 10px;overflow-wrap:anywhere}
h1 span{color:var(--lime)}
.tag{font:italic 500 clamp(22px,3.4vw,34px)/1.2 var(--serif);margin:0 0 18px}
.lede{max-width:760px;color:var(--muted);margin:0 0 10px}
.lede strong{color:var(--paper);font-weight:600}
.en{max-width:760px;color:var(--muted);font-size:13px;margin:0 0 22px}
.warn{border:1px solid rgba(255,182,72,.45);background:rgba(255,182,72,.06);border-radius:10px;padding:12px 14px;
font:400 13px/1.5 var(--mono);color:var(--amber);margin:18px 0 0}
.loop{display:grid;grid-template-columns:1fr;gap:1px;background:var(--line);border:1px solid var(--line);border-radius:12px;overflow:hidden;margin:26px 0 0}
@media(min-width:760px){.loop{grid-template-columns:repeat(4,1fr)}}
.step{background:var(--ink-2);padding:16px}
.step .t{font:700 12px/1 var(--mono);color:var(--lime);letter-spacing:.12em}
.step .h{font:600 15px/1.35 var(--sans);margin:8px 0 4px}
.step p{margin:0;color:var(--muted);font-size:13.5px}
.step:nth-child(2) .t{color:var(--cyan)}.step:nth-child(3) .t{color:var(--amber)}.step:nth-child(4) .t{color:var(--coral)}
main{padding:34px 0 56px}
section{margin:0 0 48px}
h2{font:400 12px/1 var(--mono);letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin:0 0 16px}
h3{font:italic 500 24px/1.2 var(--serif);margin:0 0 10px}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;background:var(--line);border:1px solid var(--line);border-radius:12px;overflow:hidden}
@media(min-width:760px){.stats{grid-template-columns:repeat(4,1fr)}}
.stat{background:var(--ink-2);padding:16px 18px}
.stat .n{font:700 clamp(26px,4vw,38px)/1.05 var(--mono)}
.stat .l{font:400 11px/1.45 var(--mono);color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-top:6px}
.c-exact{color:var(--lime)}.c-normalized{color:var(--cyan)}.c-mismatch{color:var(--coral)}.c-not_applied{color:var(--amber)}.c-outside_decree{color:var(--muted)}
.bar{display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--ink-3);margin:18px 0 8px}
.bar span{display:block;height:100%}
.b-exact{background:var(--lime)}.b-normalized{background:var(--cyan)}.b-mismatch{background:var(--coral)}.b-not_applied{background:var(--amber)}.b-outside_decree{background:#4a505c}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font:400 12px var(--mono);color:var(--muted);margin:0;padding:0;list-style:none}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.note{color:var(--muted);font-size:13px;max-width:820px}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--ink-2)}
table{border-collapse:collapse;width:100%;min-width:900px;font-size:13.5px}
td.title{min-width:360px}
caption{text-align:left;padding:12px 14px;color:var(--muted);font:400 12px var(--mono)}
th,td{padding:10px 12px;border-top:1px solid var(--line);vertical-align:top;text-align:left}
th{font:400 11px/1.3 var(--mono);text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
td.num{font-family:var(--mono);white-space:nowrap}
.abbr{font:700 12px/1.6 var(--mono);color:var(--ink);background:var(--lime);border-radius:4px;padding:1px 7px;white-space:nowrap}
.src{font:400 11px var(--mono);color:var(--muted);display:block;margin-top:4px}
.pill{display:inline-block;font:400 11px/1.6 var(--mono);border:1px solid currentColor;border-radius:999px;padding:0 8px;white-space:nowrap}
.mini{display:flex;height:8px;width:120px;border-radius:4px;overflow:hidden;background:var(--ink-3);margin-top:6px}
.anatomy{display:grid;grid-template-columns:1fr;gap:14px}
@media(min-width:980px){.anatomy{grid-template-columns:repeat(3,minmax(0,1fr))}}
.card{background:var(--ink-2);border:1px solid var(--line);border-radius:12px;padding:16px;min-width:0}
.card .k{font:400 11px var(--mono);text-transform:uppercase;letter-spacing:.14em;color:var(--muted);margin-bottom:10px}
.card p,.card li{font-size:13.5px}
pre{white-space:pre-wrap;word-break:break-word;font:400 12.5px/1.55 var(--mono);background:var(--ink);border:1px solid var(--line);border-radius:8px;padding:12px;margin:0;color:var(--paper);max-height:520px;overflow:auto}
.law p{margin:0 0 10px;font-size:13.5px}
.law .n{color:var(--muted);font-style:italic;font-size:12.5px}
.verdict{margin-top:12px;font:700 13px var(--mono)}
ins{background:rgba(207,255,69,.16);color:var(--lime);text-decoration:none;border-radius:3px;padding:0 2px}
del{background:rgba(255,93,71,.16);color:var(--coral);border-radius:3px;padding:0 2px}
.diff{font:400 13px/1.7 var(--sans)}
.art{border-top:1px solid var(--line);padding:16px 0}
.art summary{cursor:pointer;display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;list-style:none}
.art summary::-webkit-details-marker{display:none}
.art summary b{font:700 14px var(--mono)}
.cols{display:grid;grid-template-columns:1fr;gap:12px;margin-top:12px}
@media(min-width:980px){.cols{grid-template-columns:repeat(3,minmax(0,1fr))}}
.reviews li{margin:0 0 6px;font-size:13.5px}
footer{border-top:1px solid var(--line);padding:28px 0 48px;color:var(--muted);font-size:13px}
footer .who{font:italic 500 20px var(--serif);color:var(--paper)}
@media(prefers-reduced-motion:no-preference){.bar span{animation:grow .9s ease-out both}@keyframes grow{from{transform:scaleX(0);transform-origin:left}}}
"""


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "n/d"


def _bar(counts: dict, keys: list[str], cls: str = "bar") -> str:
    total = sum(counts.get(k, 0) for k in keys)
    if not total:
        return f'<div class="{cls}" aria-hidden="true"></div>'
    spans = "".join(
        f'<span class="b-{k}" style="width:{100 * counts.get(k, 0) / total:.3f}%"></span>'
        for k in keys
        if counts.get(k, 0)
    )
    return f'<div class="{cls}" aria-hidden="true">{spans}</div>'


def _diff_html(ops: list[list[str]]) -> str:
    out = []
    for op, text in ops:
        t = escape(text).replace(" ¶ ", "<br>").replace("¶", "<br>")
        out.append(t if op == "=" else f"<del>{t}</del>" if op == "-" else f"<ins>{t}</ins>")
    return " ".join(out)


def _paras(lines: list[str]) -> str:
    out = []
    for line in lines:
        cls = ' class="n"' if _looks_note(line) else ""
        out.append(f"<p{cls}>{escape(line)}</p>")
    return "".join(out) or "<p class='n'>(sin texto)</p>"


def _looks_note(line: str) -> bool:
    from .legal import is_note

    return is_note(line)


def _pvk(prefix: str) -> str:
    """PIVOTEK logo for the header: the logotype, or the isotype on narrow screens."""
    return (
        '<a class="pvk" href="https://pivotek.mx" title="PIVOTEK">'
        f'<img class="pvk-logo" src="{prefix}assets/pivotek-logotipo-naranja.svg" alt="PIVOTEK" width="90" height="34">'
        f'<img class="pvk-iso" src="{prefix}assets/pivotek-isotipo-naranja.svg" alt="PIVOTEK" width="21" height="24"></a>'
    )


def _page(title: str, body: str, *, prefix: str, description: str) -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="{escape(description)}">
<meta name="color-scheme" content="dark">
<link rel="icon" href="{prefix}assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{prefix}assets/site.css">
</head>
<body>
<a class="skip" href="#main">Ir al contenido</a>
{body}
<footer>
  <div class="wrap">
    <p class="who">Benjamín Esponda García</p>
    <p>Abogado corporativo mexicano que construye con IA · <a href="https://pivotek.mx">pivotek.mx</a> · <a href="https://github.com/BEG-lag">github.com/BEG-lag</a></p>
    <p><strong>Texto provisional aplicado por IA. Sólo el DOF es fuente oficial. No es asesoría legal.</strong>
    Provisional AI-applied text. Only the DOF is authoritative. Not legal advice.</p>
    <p>reforma-al-dia {__version__} · código MIT · textos legales de dominio público (LFDA, art. 14, fr. VIII) ·
    <a href="https://github.com/BEG-lag/reforma-al-dia">código fuente</a> · <a href="{prefix}data/auditoria.json">datos (JSON)</a></p>
  </div>
</footer>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------


def _anatomy(cases: dict[str, dict]) -> str:
    case_id, key = ANATOMY
    case = cases.get(case_id)
    if case is None:
        return ""
    art = next((a for a in case["articles"] if a["key"] == key), None)
    decree = case.get("decree_text", {}).get(key)
    if art is None or decree is None:
        return ""
    edits = [
        e
        for e in case["plan"]["edits"]
        if e["article"].replace("Artículo", "").strip(" .") == key or key in e["article"]
    ]
    shown = [
        {k: e[k] for k in ("article", "operation", "unit_type", "unit", "from_block", "to_block", "paragraphs")}
        for e in edits
    ]
    # Official text around the edited units only: the blocks the edits produced, located in the official article.
    wanted = [p for e in edits for p in e["paragraphs"]]
    official = art["official"]
    idx = [i for i, line in enumerate(official) if any(line.startswith(w[:40]) for w in wanted)]
    lo, hi = (max(0, min(idx) - 1), min(len(official), max(idx) + 3)) if idx else (0, min(len(official), 8))
    status = art["status"]
    instruction = case["decree"]["instruction"][0]
    decree_lines = "\n".join(decree["lines"])
    return f"""
<section aria-labelledby="anatomia">
  <h2 id="anatomia">Anatomía de una reforma</h2>
  <h3>{escape(case["law"]["abbreviation"])}, {escape(art["label"])} · DOF {escape(_dmy(case["decree"]["date"]))}</h3>
  <p class="note">La reducción de la jornada laboral a cuarenta horas. A la izquierda, lo que publicó el Diario Oficial;
  al centro, lo único que devolvió el modelo (ediciones estructuradas, no un archivo reescrito); a la derecha, el texto
  consolidado que la Cámara de Diputados publicó después. Resultado: <strong class="c-{status}">{STATUS_ES[status]}</strong>.</p>
  <div class="anatomy">
    <div class="card"><div class="k">1 · El decreto (DOF)</div>
      <p>{escape(instruction)}</p>
      <pre>{escape(decree_lines)}</pre>
      <p class="src"><a href="{escape(case["decree"]["url"])}">Nota {case["decree"]["code"]} en dof.gob.mx</a></p>
    </div>
    <div class="card"><div class="k">2 · La edición de la IA</div>
      <pre>{escape(json.dumps(shown, ensure_ascii=False, indent=1))}</pre>
      <p class="src">Un programa determinista aplica esto al Markdown, agrega la nota «Fracción reformada DOF …» y valida que nada más cambie.</p>
    </div>
    <div class="card"><div class="k">3 · El texto oficial (Cámara de Diputados)</div>
      <div class="law">{_paras(official[lo:hi])}</div>
      <p class="verdict c-{status}">{STATUS_ES[status].upper()}: {"idéntico, byte por byte" if status == "exact" else "ver diferencias"}</p>
      <p class="src"><a href="{LEYES_URL}/commit/{escape(case["official"]["commit"] or "")}">Commit oficial en leyes-mx</a> · <a href="casos/{escape(case_id)}.html">caso completo</a></p>
    </div>
  </div>
</section>"""


def _dmy(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}-{m}-{y}"


def _row(c: dict) -> str:
    counts = c["counts"]
    applied = counts["exact"] + counts["normalized"] + counts["mismatch"]
    links = [f'<a href="casos/{escape(c["id"])}.html">detalle</a>']
    if c.get("official", {}).get("commit"):
        links.append(f'<a href="{LEYES_URL}/commit/{escape(c["official"]["commit"])}">commit oficial</a>')
    prop = c.get("proposal") or {}
    if prop.get("pr"):
        links.append(f'<a href="{escape(prop["pr"])}">PR</a>')
    if c["decree"].get("pdf"):
        links.append(f'<a href="{escape(c["decree"]["pdf"])}">PDF</a>')
    src = "en vivo" if c["source"] == "live" else "backtest"
    return f"""<tr>
<td class="num">{_dmy(c["decree"]["date"])}<span class="src">{src}</span></td>
<td><span class="abbr">{escape(c["law"]["abbreviation"])}</span></td>
<td class="title"><a href="{escape(c["decree"]["url"])}">{escape(c["decree"]["title"])}</a><span class="src">{" · ".join(links)}</span></td>
<td class="num"><span class="c-exact">{counts["exact"]}</span> / <span class="c-normalized">{counts["normalized"]}</span> / <span class="c-mismatch">{counts["mismatch"]}</span>{_bar(counts, ["exact", "normalized", "mismatch", "not_applied"], "mini")}</td>
<td class="num c-not_applied">{counts["not_applied"]}</td>
<td class="num">{applied}</td>
<td class="num c-{c.get("transitorios", "unavailable") if c.get("transitorios") in STATUS_ES else "outside_decree"}">{STATUS_ES.get(c.get("transitorios", "unavailable"), "n/d")}</td>
</tr>"""


def index_html(board: dict, cases: dict[str, dict]) -> str:
    t = board["totals"]["all"]
    bt = board["totals"]["backtest"]
    live = board["totals"]["live"]
    applied = t["applied"]
    causes = t.get("mismatch_causes", {})
    laws = len({c["law"]["slug"] for c in board["cases"]})
    rows = "\n".join(_row(c) for c in board["cases"])
    stats = f"""
<div class="stats">
  <div class="stat"><div class="n c-exact">{_pct(t["exact"], applied)}</div><div class="l">exactos, byte por byte<br>{t["exact"]} de {applied} artículos aplicados</div></div>
  <div class="stat"><div class="n c-normalized">{_pct(t["exact"] + t["normalized"], applied)}</div><div class="l">exactos o iguales al normalizar<br>espacios y puntuación</div></div>
  <div class="stat"><div class="n c-mismatch">{_pct(t["mismatch"], applied)}</div><div class="l">con diferencias de palabras<br>{t["mismatch"]} artículos, todos inspeccionables</div></div>
  <div class="stat"><div class="n c-not_applied">{t["not_applied"]}</div><div class="l">no aplicados: enviados<br>a revisión humana</div></div>
  <div class="stat"><div class="n">{t["decrees"]}</div><div class="l">decretos reales del DOF</div></div>
  <div class="stat"><div class="n">{laws}</div><div class="l">leyes distintas</div></div>
  <div class="stat"><div class="n">{t["body_identical"]}/{t["cases"]}</div><div class="l">leyes completas idénticas<br>a la versión oficial</div></div>
  <div class="stat"><div class="n">{t["claude_calls"]}</div><div class="l">llamadas a Claude<br>(en caché: repetir cuesta 0)</div></div>
</div>
{_bar(t, ["exact", "normalized", "mismatch", "not_applied"])}
<ul class="legend">
  <li><i class="b-exact"></i>exacto {t["exact"]}</li><li><i class="b-normalized"></i>normalizado {t["normalized"]}</li>
  <li><i class="b-mismatch"></i>diferente {t["mismatch"]}</li><li><i class="b-not_applied"></i>a revisión {t["not_applied"]}</li>
</ul>
<p class="note">Backtest: {bt["decrees"]} decretos ({bt["cases"]} pares decreto-ley) de 2019 a 2026 con versión anterior y posterior en
<a href="{LEYES_URL}">leyes-mx</a>. Auditorías en vivo: {live["cases"]}. Se cuentan los artículos que la IA modificó; además,
{t["outside_decree"]} artículos cambiaron en el texto oficial sin que el decreto los nombre (correcciones de la Cámara o ruido
entre archivos fuente) y se excluyen. Las notas editoriales («Párrafo reformado DOF …») se miden aparte: coinciden en
{t["notes_match"]} de {applied} artículos. Definiciones y datos: <a href="data/auditoria.json">auditoria.json</a>.</p>
<p class="note">Por qué difieren los {t["mismatch"]} artículos diferentes:
<strong>{causes.get("repeal_placeholder", 0)}</strong> sólo por marcadores «Se deroga» / «Derogado» que el decreto imprime y la
Cámara a veces omite; <strong>{causes.get("numbering", 0)}</strong> sólo por la numeración;
<strong>{causes.get("official_annotation", 0)}</strong> porque el texto oficial agrega anotaciones de la Cámara;
<strong class="c-mismatch">{causes.get("text", 0)}</strong> por el texto mismo. Cada uno se puede revisar en su caso.</p>"""

    body = f"""
<header class="top">
  <div class="wrap">
    <div class="kicker"><span><b>DOF</b> → IA → pull request → revisión humana → <b>auditoría</b></span>{_pvk("")}</div>
    <h1>reforma<span>-</span>al<span>-</span>día</h1>
    <p class="tag">La reforma, aplicada la misma mañana. Auditada después, en público.</p>
    <p class="lede">Cuando el <strong>Diario Oficial de la Federación</strong> publica un decreto que reforma una ley de
    <a href="{LEYES_URL}">leyes-mx</a>, un agente de IA aplica los cambios al texto de la ley esa misma mañana y abre un
    <strong>pull request</strong> para que una persona lo revise. Semanas después, cuando la Cámara de Diputados publica el texto
    consolidado oficial, el sistema <strong>se audita a sí mismo</strong>: compara palabra por palabra lo que aplicó la IA con el
    texto oficial y publica el marcador.</p>
    <p class="en">In English: the morning the DOF publishes a reform, an AI agent applies it to the law in leyes-mx and opens a
    pull request for a human to review. When the official consolidated text arrives, it audits itself and keeps this public
    scoreboard.</p>
    <div class="loop" role="list">
      <div class="step" role="listitem"><div class="t">06:00 · DOF</div><div class="h">Se publica el decreto</div><p>Edición matutina del Diario Oficial, vía la API pública de SIDOF.</p></div>
      <div class="step" role="listitem"><div class="t">08:45 · IA</div><div class="h">Claude propone ediciones</div><p>Por artículo y unidad: reforma, adición o derogación, con el texto exacto. Sin herramientas.</p></div>
      <div class="step" role="listitem"><div class="t">PR · Humano</div><div class="h">Una persona revisa</div><p>Código determinista aplica, valida y abre el PR marcado como provisional. Lo dudoso va a revisión.</p></div>
      <div class="step" role="listitem"><div class="t">Semanas · Auditoría</div><div class="h">Llega el texto oficial</div><p>Se compara con lo que aplicó la IA: exacto, normalizado o diferente, con el diff publicado.</p></div>
    </div>
    <p class="warn">Texto provisional aplicado por IA. Sólo el DOF es fuente oficial. No es asesoría legal.</p>
  </div>
</header>
<main id="main" class="wrap">
<section aria-labelledby="marcador">
  <h2 id="marcador">Marcador de precisión, por artículo</h2>
  {stats}
</section>
{_anatomy(cases)}
<section aria-labelledby="decretos">
  <h2 id="decretos">Cada decreto</h2>
  <div class="table-wrap">
  <table>
    <caption>Artículos aplicados por la IA: exactos / normalizados / diferentes. «A revisión»: el decreto los cambió y la IA no los aplicó, los marcó para revisión humana.</caption>
    <thead><tr><th scope="col">DOF</th><th scope="col">Ley</th><th scope="col">Decreto</th><th scope="col">Exacto / norm. / dif.</th><th scope="col">A revisión</th><th scope="col">Aplicados</th><th scope="col">Transitorios</th></tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
</section>
<section aria-labelledby="como">
  <h2 id="como">Cómo funciona y qué no hace</h2>
  <div class="anatomy">
    <div class="card"><div class="k">Humano en el circuito</div><p>Nada se fusiona solo. El PR lleva la etiqueta <code>provisional</code>, enlaza el decreto, resume cada edición y enumera lo que requiere revisión. El dueño de leyes-mx decide.</p></div>
    <div class="card"><div class="k">La IA no escribe archivos</div><p>Claude se llama sin herramientas, sin MCP y sin configuración personal, y sólo puede devolver JSON validado contra un esquema. Un programa aplica, numera notas, valida que ningún otro artículo cambie y que la numeración siga válida.</p></div>
    <div class="card"><div class="k">Límites</div><p>Decretos que reforman transitorios de otros decretos, declaratorias de invalidez de la SCJN y textos base desactualizados quedan fuera o van a revisión. La Cámara a veces omite párrafos «Se deroga» o corrige el texto por su cuenta: eso aparece como diferencia.</p></div>
  </div>
</section>
</main>"""
    return _page(
        "reforma-al-día · marcador",
        body,
        prefix="",
        description="Reformas del DOF aplicadas por IA a leyes-mx la misma mañana, con pull request para revisión humana y auditoría pública contra el texto oficial.",
    )


# --------------------------------------------------------------------------
# Case pages
# --------------------------------------------------------------------------


def case_html(case: dict) -> str:
    counts = case["counts"]
    applied = counts["exact"] + counts["normalized"] + counts["mismatch"]
    reviews = case.get("needs_review") or []
    review_html = "".join(
        f"<li><b>{escape(r['article'])}</b>: {escape(r['reason'])} <span class='src'>{escape(r.get('source', ''))}</span></li>"
        for r in reviews
    )
    arts = []
    for a in case["articles"]:
        st = a["status"]
        open_attr = " open" if st in ("mismatch", "not_applied") else ""
        diff = (
            f'<div class="card"><div class="k">Diferencias (IA → oficial)</div><div class="diff">{_diff_html(a["diff"])}</div></div>'
            if a.get("diff")
            else ""
        )
        ai_col = (
            _paras(a["ai"])
            if a["status"] not in ("not_applied", "outside_decree")
            else "<p class='n'>(la IA no modificó este artículo)</p>"
        )
        notes = "notas editoriales iguales" if a["notes_match"] else "notas editoriales distintas"
        cause = f' <span class="src">causa: {CAUSE_ES.get(a.get("cause") or "", "")}</span>' if a.get("cause") else ""
        arts.append(f"""<details class="art"{open_attr}>
<summary><b>{escape(a["label"])}</b> <span class="pill c-{st}">{STATUS_ES[st]}</span>{cause} <span class="src">{notes}</span></summary>
{diff}
<div class="cols">
  <div class="card"><div class="k">Antes</div><div class="law">{_paras(a["before"])}</div></div>
  <div class="card"><div class="k">IA (provisional)</div><div class="law">{ai_col}</div></div>
  <div class="card"><div class="k">Oficial (Cámara de Diputados)</div><div class="law">{_paras(a["official"])}</div></div>
</div>
</details>""")
    official = case.get("official") or {}
    links = [f'<a href="{escape(case["decree"]["url"])}">decreto en dof.gob.mx</a>']
    if official.get("commit"):
        links.append(f'<a href="{LEYES_URL}/commit/{escape(official["commit"])}">commit oficial en leyes-mx</a>')
    if case["base"].get("commit") and official.get("commit"):
        links.append(
            f'<a href="{LEYES_URL}/compare/{escape(case["base"]["commit"])}...{escape(official["commit"])}">diff oficial</a>'
        )
    prop = case.get("proposal") or {}
    if prop.get("pr"):
        links.append(f'<a href="{escape(prop["pr"])}">pull request</a>')
    later = ""
    if official.get("same_day") is False:
        later = (
            f'<p class="warn">La versión oficial con la que se compara es del DOF {_dmy(official["date"])}, posterior al '
            "decreto: puede incluir cambios de otros decretos o correcciones de la Cámara.</p>"
        )
    calls = case.get("calls", [])
    seconds = sum(c.get("duration_ms", 0) for c in calls) / 1000
    body = f"""
<header class="top"><div class="wrap">
  <div class="kicker"><span><a href="../index.html">reforma-al-día</a> / <b>{escape(case["law"]["abbreviation"])}</b> / DOF {_dmy(case["decree"]["date"])}</span>{_pvk("../")}</div>
  <h3 style="margin-top:14px">{escape(case["decree"]["title"])}</h3>
  <p class="lede">{escape(case["law"]["title"])} · {" · ".join(links)}</p>
  <div class="stats">
    <div class="stat"><div class="n c-exact">{counts["exact"]}</div><div class="l">exactos</div></div>
    <div class="stat"><div class="n c-normalized">{counts["normalized"]}</div><div class="l">normalizados</div></div>
    <div class="stat"><div class="n c-mismatch">{counts["mismatch"]}</div><div class="l">diferentes</div></div>
    <div class="stat"><div class="n c-not_applied">{counts["not_applied"]}</div><div class="l">a revisión</div></div>
  </div>
  {later}
  <p class="note">{applied} artículos aplicados · {counts["outside_decree"]} cambios oficiales fuera del decreto · transitorios: {STATUS_ES.get(case.get("transitorios", "unavailable"), "n/d")} ·
  ley completa idéntica a la oficial: {"sí" if case.get("body_identical") else "no"} · {len(calls)} llamada(s) a Claude, {seconds:.0f} s.</p>
</div></header>
<main id="main" class="wrap">
<section><h2>Instrucción del decreto</h2>{"".join(f'<p class="note">{escape(i)}</p>' for i in case["decree"].get("instruction", []))}</section>
<section><h2>Requiere revisión humana ({len(reviews)})</h2><ul class="reviews">{review_html or "<li>Nada.</li>"}</ul></section>
<section><h2>Artículo por artículo</h2>{"".join(arts)}</section>
<section><h2>Ediciones devueltas por el modelo</h2><pre>{escape(json.dumps(case["plan"], ensure_ascii=False, indent=1))}</pre></section>
</main>"""
    return _page(
        f"{case['law']['abbreviation']} DOF {_dmy(case['decree']['date'])} · reforma-al-día",
        body,
        prefix="../",
        description=f"Auditoría de la reforma {case['law']['abbreviation']} DOF {_dmy(case['decree']['date'])}: texto aplicado por IA contra el texto oficial.",
    )


FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#0a0c10"/><path d="M14 34l10 10 26-26" fill="none" stroke="#cfff45" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/></svg>
"""


def build_site(cfg: Config) -> Path:
    docs = cfg.docs
    cases_list = load_cases(cfg.data)
    board = build(cases_list)
    cases = {c["id"]: c for c in cases_list}
    (docs / "casos").mkdir(parents=True, exist_ok=True)
    (docs / "assets").mkdir(parents=True, exist_ok=True)
    (docs / "data").mkdir(parents=True, exist_ok=True)
    (docs / "assets" / "site.css").write_text(CSS.replace("FONTS/", "fonts/"), encoding="utf-8")
    (docs / "assets" / "favicon.svg").write_text(FAVICON, encoding="utf-8")
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    (docs / "index.html").write_text(index_html(board, cases), encoding="utf-8")
    for old in (docs / "casos").glob("*.html"):
        if old.stem not in cases:
            old.unlink()
    for case in cases_list:
        (docs / "casos" / f"{case['id']}.html").write_text(case_html(case), encoding="utf-8")
    shutil.copyfile(cfg.data / "auditoria.json", docs / "data" / "auditoria.json") if (
        cfg.data / "auditoria.json"
    ).exists() else None
    return docs / "index.html"
