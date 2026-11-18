# Scheduling the daily run

`reforma-al-dia run --date today` is meant to run once a day on the owner's Mac, at about
**08:45 Mexico City time**, after the morning edition of the DOF is online. It needs:

- this repository, with `uv sync` done;
- the Claude Code CLI (`claude`) logged in with the owner's account;
- for live runs, the GitHub CLI (`gh auth status` OK) with push access to `BEG-lag/leyes-mx`.

The run is idempotent: a decree and a law are proposed at most once (see "Idempotency" in the
README). Every step is logged to `.work/reforma-al-dia.log`.

Start in **dry-run** mode (the default): it commits to local branches in the leyes-mx clone
(`~/.cache/reforma-al-dia/leyes-mx`) and writes the pull request text to `.work/pr/`, but pushes
nothing. Switch `REFORMA_LIVE` to `1` once the dry runs look right.

## Option A: launchd (recommended, no Claude session needed)

The agent file is `scheduling/mx.pivotek.reforma-al-dia.plist`. It is **not installed** by this
repository. To install it:

```bash
# 1. Check the paths inside the plist (they assume /Users/esponda/GitHub/reforma-al-dia).
# 2. The Mac must be on Mexico City time (System Settings > General > Date & Time).
cp scheduling/mx.pivotek.reforma-al-dia.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/mx.pivotek.reforma-al-dia.plist
launchctl print gui/$(id -u)/mx.pivotek.reforma-al-dia | head -20   # loaded?
launchctl kickstart gui/$(id -u)/mx.pivotek.reforma-al-dia           # run once now (optional)
```

To go live, edit `REFORMA_LIVE` (and, if you want the scoreboard pushed automatically,
`REFORMA_PUBLISH`) to `1` in the installed plist and reload it:

```bash
launchctl bootout gui/$(id -u)/mx.pivotek.reforma-al-dia
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/mx.pivotek.reforma-al-dia.plist
```

To remove it: `launchctl bootout gui/$(id -u)/mx.pivotek.reforma-al-dia` and delete the file.

If the Mac is asleep at 08:45, launchd runs the job when it wakes up. The script is
`scheduling/run-daily.sh`; its output goes to `.work/launchd.log`.

## Option B: a Claude Code scheduled task

In the Claude Code desktop app, create a scheduled task (daily, 08:45, America/Mexico_City) whose
prompt is only:

```text
Run `cd ~/GitHub/reforma-al-dia && scheduling/run-daily.sh` in the terminal and report its
last 30 lines. Do not edit any file and do not run any other command.
```

The pipeline itself still calls Claude headless with tools disabled; the scheduled task only
starts it. Option A is simpler and does not depend on an open session.

## What a day looks like

```text
reforma-al-dia 2026-11-13 (dry run)
  LISR DOF 13-11-2026 nota 5803758: DRY RUN, committed 1a768f948 on local branch reforma/2026-11-13-lisr (+303 -239)
  LISR DOF 13-11-2026 nota 5803758: would run: git push -u origin reforma/2026-11-13-lisr && gh pr create --repo BEG-lag/leyes-mx ...
  CPF DOF 13-11-2026 nota 5803837: DRY RUN, committed 19c76efbb on local branch reforma/2026-11-13-cpf (+144 -52)
  LFPPI DOF 13-11-2026 nota 5803837: DRY RUN, committed 76ce0a195 on local branch reforma/2026-11-13-lfppi (+104 -16)
```

On a day without reform decrees of the laws in leyes-mx the run ends in a few seconds, after
one request to the DOF table of contents.
