#!/usr/bin/env bash
# faults_job.sh - farm job `faults` (D25 #1): the fault classifier (tools/report/faults.py) over the head's boot logs,
# grouped by (kind, function, 3 callers) and split into new / known / fixed against the previous head this farm
# classified. zero_targets.py's #1 row reads the sidecar (<c12>-faults.json, fnvnx-faults/1).
#
#   tools/farm/faults_job.sh <commit> <report.md>
#
# Logs: FAULTS_LOGS="<log>..." (S18's scenario logs, any emu_guest or linhost output), else the full job's published
# boot logs of the head's link from the artifacts repository ($ARTIFACTS, branch link/<c12>: boot/emu.log and
# boot/stub/emu_stub.log), FAULTS_ELF the guest (default: that link's guest/fnvnx.elf). The previous head: the newest
# older commit of the integration branch with a classification in $FARM_HOME/faults/ (FAULTS_PREV=<json> instead).
# Exit 0 when the report is written.
set -uo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd); REPO=$(cd "$P/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
C12=${COMMIT:0:12}; W=$FARM_HOME/faults-work/$C12; ST=$FARM_HOME/faults; rm -rf "$W"; mkdir -p "$W" "$ST" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
log() { echo "$(date -u +%FT%TZ) faults_job: $*"; }

LOGS=(); ELF=${FAULTS_ELF:-}; NOTE=""
if [ -n "${FAULTS_LOGS:-}" ]; then
	read -r -a LOGS <<< "$FAULTS_LOGS"; NOTE="logs handed in: ${#LOGS[@]}"
elif [ -n "${ARTIFACTS:-}" ] && git -C "$ARTIFACTS" fetch -q origin "+refs/heads/link/$C12:refs/remotes/origin/link/$C12" 2>/dev/null; then
	for f in boot/emu.log boot/stub/emu_stub.log guest/fnvnx.elf; do
		mkdir -p "$W/link/$(dirname "$f")"
		git -C "$ARTIFACTS" show "refs/remotes/origin/link/$C12:$f" > "$W/link/$f" 2>/dev/null || rm -f "$W/link/$f"
	done
	for f in "$W/link/boot/emu.log" "$W/link/boot/stub/emu_stub.log"; do [ -s "$f" ] && LOGS+=("$f"); done
	[ -z "$ELF" ] && [ -s "$W/link/guest/fnvnx.elf" ] && ELF=$W/link/guest/fnvnx.elf
	NOTE="link/$C12: ${#LOGS[@]} boot logs"
else
	NOTE="no logs: no FAULTS_LOGS and no link/$C12 on the artifacts repository"
fi
PREV=${FAULTS_PREV:-}
if [ -z "$PREV" ]; then
	for h in $(git -C "$REPO" log --format=%H -n 400 "$COMMIT" 2>/dev/null | tail -n +2); do
		[ -f "$ST/${h:0:12}.json" ] && { PREV=$ST/${h:0:12}.json; break; }
	done
fi
log "$NOTE; previous: ${PREV:-none}"
if [ ${#LOGS[@]} -gt 0 ]; then
	python3 -I "$P/tools/report/faults.py" "${LOGS[@]}" ${ELF:+--elf "$ELF"} ${PREV:+--prev "$PREV"} --out "$W/faults.md" \
		> "$W/faults.log" 2>&1
	[ -f "$W/faults.json" ] && cp "$W/faults.json" "$ST/$C12.json"
fi
{
	echo "# faults $C12"; echo
	echo "- $NOTE; previous head's classification: $( [ -n "$PREV" ] && basename "$PREV" .json || echo none)"
	if [ -f "$W/faults.md" ]; then tail -n +2 "$W/faults.md"; else echo "- not classified (no logs)"; fi
} > "$REPORT"
[ -f "$W/faults.json" ] && cp "$W/faults.json" "${REPORT%.md}.json"
log "report: $REPORT"
exit 0
