#!/bin/zsh -l
# Daily run of reforma-al-dia on the owner's Mac (called by launchd or by hand).
#
#   REFORMA_LIVE=1     push branches and open pull requests in leyes-mx (default: dry run)
#   REFORMA_PUBLISH=1  commit and push the refreshed scoreboard (data/ and docs/) of this repo
#
# Everything is logged to .work/reforma-al-dia.log; this script's own output goes to .work/launchd.log.
set -u
cd "$(dirname "$0")/.." || exit 1

flags=(--date today)
if [[ "${REFORMA_LIVE:-0}" == "1" ]]; then
  flags+=(--no-dry-run)
fi

echo "== $(date '+%Y-%m-%d %H:%M:%S') reforma-al-dia run ${flags[*]}"
uv run reforma-al-dia run "${flags[@]}"
status=$?
uv run reforma-al-dia site

if [[ "${REFORMA_PUBLISH:-0}" == "1" ]] && ! git diff --quiet -- data docs; then
  git add data docs
  git commit -q -m "chore(data): scoreboard after the DOF of $(date +%d-%m-%Y)"
  git push -q
fi
exit $status
