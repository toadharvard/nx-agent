#!/usr/bin/env bash
# test_nx1_publish_nro.sh - V1 (D14) against NX1 ac00629: full_job.sh's publish_nro (tools/farm/lib_job.sh) puts
# "the device package of this head" on the private branch nro/<commit12>.
#
# Runs the real publish_nro (lib_job.sh sourced) against a throw-away local artifacts repository with a bare origin.
# The build directory is the farm's full/build, reused across jobs (full_job.sh), so it may still hold what a
# previous job (commit OLD) left. Synthetic files only (the strings OLD / NEW).
#   P1  this job (NEW): `make BOOT=1` failed in the host stage, so the package stage never ran (deps: host) and
#       package/sd is OLD's. publish_link refuses such leftovers (stage_after); publish_nro only checks that
#       package/sd/switch/fnv/fnv.nro exists, so OLD's fnv.nro must not land on nro/NEW
#   P2  the push of nro/NEW is rejected (a pre-receive hook) while nro/NEW already exists from an earlier run of the
#       same commit: the check after the push (`git ls-remote ... | grep -q .`) only asks whether the branch exists,
#       so NOTE_NRO must not say "published"
#   K1  control: package stage ok and newer than host, push accepted -> nro/NEW holds NEW's fnv.nro
#   bash tests/v1/test_nx1_publish_nro.sh   -> one line per case, exit 1 when any shows a defect
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PORT=$(cd "$HERE/../.." && pwd)
T=$(mktemp -d) && [ -d "$T" ] || { echo "mktemp failed"; exit 2; }   # never run with T="" (writes under /)
[ -n "${V1_KEEP:-}" ] || trap 'rm -rf "$T"' EXIT
g() { git -c user.name=t -c user.email=t@invalid "$@"; }
bad=0
NEW=aaaaaaaaaaaa

setup() {  # fresh origin + artifacts clone + build dir per case
	rm -rf "$T/origin.git" "$T/art" "$T/farm" "$T/bd"
	git init -q --bare "$T/origin.git"
	git init -q "$T/art"
	g -C "$T/art" commit -q --allow-empty -m init
	g -C "$T/art" remote add origin "$T/origin.git"
	mkdir -p "$T/farm" "$T/bd/package/sd/switch/fnv" "$T/bd/host"
}
stage() {  # stage <name> <status>
	mkdir -p "$T/bd/$1"; printf '{"stage": "%s", "status": "%s"}\n' "$1" "$2" > "$T/bd/$1/stage.json"
}
publish() {  # -> NOTE_NRO
	(
		export FARM_HOME=$T/farm ARTIFACTS=$T/art C12=$NEW FARM_APT=0 FARM_PUBLISH=auto
		# shellcheck disable=SC1091
		. "$PORT/tools/farm/lib_job.sh"
		publish_nro "$T/bd" >/dev/null 2>&1
		echo "$NOTE_NRO"
	)
}
remote_nro() { git -C "$T/origin.git" show "refs/heads/nro/$NEW:sd/switch/fnv/fnv.nro" 2>/dev/null; }
say() {  # say <case> <defect 0|1> <text>
	if [ "$2" = 1 ]; then bad=$((bad + 1)); echo "$1: DEFECT - $3"; else echo "$1: ok - $3"; fi
}

# K1 control
setup
stage host ok; sleep 1; stage package ok
echo NEW > "$T/bd/package/sd/switch/fnv/fnv.nro"
note=$(publish)
[ "$(remote_nro)" = NEW ] && say K1 0 "fresh package published ($note)" || say K1 1 "control failed: '$note', remote '$(remote_nro)'"

# P1: OLD's package left in the build dir, NEW's host stage failed, package stage never ran
setup
stage package ok; echo OLD > "$T/bd/package/sd/switch/fnv/fnv.nro"   # OLD job's leftovers
sleep 1; stage host fail                                                # this job
note=$(publish)
got=$(remote_nro)
[ "$got" = OLD ] && say P1 1 "nro/$NEW holds the previous job's fnv.nro (host failed, package not run): '$note'" \
	|| say P1 0 "no stale publish ('$note', remote '${got:-none}')"

# P2: push rejected, an older nro/NEW already on the remote
setup
stage host ok; sleep 1; stage package ok
echo EARLIER > "$T/bd/package/sd/switch/fnv/fnv.nro"
publish >/dev/null
printf '#!/bin/sh\nexit 1\n' > "$T/origin.git/hooks/pre-receive"; chmod +x "$T/origin.git/hooks/pre-receive"
echo NEW > "$T/bd/package/sd/switch/fnv/fnv.nro"; stage package ok
note=$(publish)
got=$(remote_nro)
case "$note" in
published*) [ "$got" != NEW ] && say P2 1 "push rejected, remote still '$got', yet NOTE_NRO='$note'" \
	|| say P2 0 "remote updated ($note)" ;;
*) say P2 0 "failure reported ('$note')" ;;
esac
exit $((bad > 0))
