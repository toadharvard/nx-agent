#!/usr/bin/env bash
# perf_frame_job.sh - farm job: perf_job.sh on exactly one link and one window, with the window on the command line
# (the START line of the farm prompt) instead of in the environment.
#
#   tools/farm/perf_frame_job.sh <link commit> <start symbol> <stop symbol> <report.md>
#
# <link commit>: the commit of a branch link/<commit12> whose files include guest/fnvnx.elf (12 hex digits or more;
# no ancestor search, PERF_LINK_DEPTH=1). <start symbol> / <stop symbol>: guest symbols (e.g. the main loop's
# per-frame function twice, or the main loop's entry and the next frame's), passed to tools/perf/a57model/uctrace.py
# as --start / --stop by perf_job.sh. Run it only once the boot reaches that window (main menu / first frame):
# uctrace stops at the stop symbol or at PERF_TIMEOUT (here 7000 s by default, under runner.sh's 2 h limit), and a
# boot that never reaches the start symbol reports an empty window. Everything else (image, calibration, outputs)
# as perf_job.sh; the report's workload line names the window.
set -euo pipefail
LINK=${1:?link commit}; START=${2:?start symbol}; STOP=${3:?stop symbol}; REPORT=${4:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
case "$LINK" in *[!0-9a-f]*|"") echo "perf_frame_job: <link commit> must be hex: $LINK" >&2; exit 2 ;; esac
FULL=$(git -C "$REPO" rev-parse --verify --quiet "$LINK^{commit}" || true)
[ -n "$FULL" ] || { echo "perf_frame_job: $LINK is not a commit of this repository (fetch it first)" >&2; exit 2; }
export PERF_LINK_DEPTH=1 PERF_START="$START" PERF_STOP="$STOP" PERF_TIMEOUT="${PERF_TIMEOUT:-7000}"
exec bash "$HERE/perf_job.sh" "$FULL" "$REPORT"
