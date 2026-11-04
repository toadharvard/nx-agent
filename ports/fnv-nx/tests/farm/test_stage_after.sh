#!/usr/bin/env bash
# test_stage_after.sh - lib_job.sh's stage_after: publish_link copies a guest or boot only when it was built on the
# link being published (J-0125: full_job.sh's first publish shipped the previous commit's guest and boot).
#   bash tests/farm/test_stage_after.sh        -> "stage_after: ok"
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
LIB=$HERE/../../tools/farm/lib_job.sh
# only the two helpers: lib_job.sh itself expects a job's environment
eval "$(sed -n '/^stage_ok() {/,/^}/p; /^stage_after() {/,/^}/p' "$LIB")"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
rec() { mkdir -p "$T/$1"; printf '{"stage": "%s", "status": "%s"}\n' "$1" "$2" > "$T/$1/stage.json"; touch -d "$3" "$T/$1/stage.json"; }
fail=0
want() { if "${@:2}"; then got=yes; else got=no; fi; [ "$got" = "$1" ] || { echo "FAIL: ${*:2}: want $1, got $got"; fail=1; }; }

# the previous job's guest and boot, then this job's link (link_job.sh's publish inside full_job.sh)
rec guest ok '2026-11-04 09:40'; rec boot warn '2026-11-04 09:45'; rec link ok '2026-11-04 10:50'
want no stage_after "$T" guest link
want yes stage_after "$T" boot guest
# make BOOT=1 on this link: guest and boot again (cached stages rewrite their record too)
rec guest ok '2026-11-04 10:55'; rec boot warn '2026-11-04 11:00'
want yes stage_after "$T" guest link
want yes stage_after "$T" boot guest
# a failed guest is never published; same second counts as after
rec guest fail '2026-11-04 10:55'
want no stage_after "$T" guest link
rec guest ok '2026-11-04 10:50'
want yes stage_after "$T" guest link
# no link record: nothing to compare with
rm -rf "$T/link"
want no stage_after "$T" guest link
[ $fail = 0 ] && echo "stage_after: ok"
exit $fail
