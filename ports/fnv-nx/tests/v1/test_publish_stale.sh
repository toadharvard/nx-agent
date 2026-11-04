#!/usr/bin/env bash
# test_publish_stale.sh - V1 (D14) against J-0125: "link publishes never carry a stale guest or boot".
#
# Runs lib_job.sh's real publish_link on a mocked build directory into a throw-away local artifacts repository and
# looks at what link/<commit12> carries, in the orders of make runs full_job.sh produces:
#   A  previous job: link, guest, boot (synth image, stub boot); this job: link_job.sh's link, then its publish
#   B  this job's make BOOT=1: guest and boot rebuilt, but frontier.py failed (boot status warn) before writing
#      frontier.tsv, so the previous job's frontier.tsv is still in build/boot
#   C  a wall-clock step back between jobs (VM/container migration, NTP): this job's link record is older than the
#      previous job's guest record
#   bash tests/v1/test_publish_stale.sh   -> one line per case, "ok" or "STALE: ...", exit 1 when any is stale
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
LIB=$HERE/../../tools/farm/lib_job.sh
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export FARM_HOME=$T/farm; mkdir -p "$FARM_HOME"
git init -q --bare "$T/origin.git"
git clone -q "$T/origin.git" "$T/artifacts" 2>/dev/null
git -C "$T/artifacts" -c user.name=t -c user.email=t@invalid commit -q --allow-empty -m init
git -C "$T/artifacts" push -q origin HEAD:refs/heads/main 2>/dev/null
export ARTIFACTS=$T/artifacts
# shellcheck source=/dev/null
. "$LIB"
NOTE_TREE=t; NOTE_OBJS=o; OBJ_COMMIT=""
REPORT=$T/report.md; echo "# report" > "$REPORT"
BD=$T/build
rec() { # rec <stage> <status> <time> [json extra]
	mkdir -p "$BD/$1"
	printf '{"stage": "%s", "status": "%s"%s}\n' "$1" "$2" "${4:-}" > "$BD/$1/stage.json"
	touch -d "$3" "$BD/$1/stage.json"
}
link_outputs() {  # <commit>
	mkdir -p "$BD/link/out/cache" "$BD/link/out/vtables" "$BD/link/fixed"
	for f in aliases.ld refs.tsv abi.tsv addrmap.tsv layouts.tsv; do echo "$1" > "$BD/link/out/$f"; done
	echo "$1" > "$BD/link/out/cache/msvc.tsv"; echo "$1" > "$BD/link/fixed/zz_bridges.o"; echo "$1" > "$BD/link/summary.txt"
	rec compile ok '2026-11-04 08:00'
}
fail=0
check() { # check <case> <commit12>: what link/<commit12> carries that the previous job (commit OLD) built
	local c=$1 c12=$2 out=$T/pub-$1 bad=""
	rm -rf "$out"; mkdir -p "$out"
	git -C "$T/origin.git" archive "refs/heads/link/$c12" | tar -x -C "$out"
	[ -f "$out/guest/fnvnx.elf" ] && grep -q OLD "$out/guest/fnvnx.elf" && bad="$bad guest/fnvnx.elf"
	for f in "$out"/boot/*.* "$out"/boot/stub/*; do [ -f "$f" ] && grep -q OLD "$f" && bad="$bad ${f#$out/}"; done
	python3 -I - "$out/meta.json" <<'EOF' > "$T/meta.txt"
import json, sys
m = json.load(open(sys.argv[1]))
import hashlib
OLD = {'guest_undefined': 4423, 'boot_image_sha256': hashlib.sha256(b'OLD\n').hexdigest()}
bad = [k for k in sorted(m) if (k in OLD and m[k] == OLD[k]) or ('OLD' in str(m[k]) and k.startswith(('boot', 'guest')))]
print(' '.join('meta.%s=%s' % (k, str(m[k])[:28]) for k in bad))
EOF
	bad="$bad $(cat "$T/meta.txt")"
	bad=$(echo $bad)
	if [ -n "$bad" ]; then echo "case $c: STALE: $bad"; fail=1; else echo "case $c: ok"; fi
}
oldjob() { # the previous job (commit OLD): link, guest, boot on S14's synthesized image, stub boot
	rm -rf "$BD"; link_outputs OLD
	rec link ok '2026-11-04 09:00' ', "prepared_from": {"port_commit": "old"}'
	mkdir -p "$BD/guest" "$BD/boot/synth" "$BD/boot/stub"
	echo OLD > "$BD/guest/fnvnx.elf"
	rec guest ok '2026-11-04 09:10' ', "metrics": {"guest_undefined": 4423}'
	for f in summary.txt frontier.txt frontier.tsv; do echo OLD > "$BD/boot/$f"; done
	echo "fnvnx boot: calling WinMain OLD" > "$BD/boot/emu.log"
	echo OLD > "$BD/boot/synth/synth.exe"
	printf 'fnvnx boot: calling WinMain\nSTOPPED: OLD fault\n' > "$BD/boot/stub/emu_stub.log"
	rec boot warn '2026-11-04 09:20' ', "metrics": {"boot_winmain": 1}, "stop": "STOPPED: OLD fault 0x00aa3e40"'
}

# A: full_job.sh step 2, link_job.sh's publish of the new link (before step 3b removes boot/synth, before 4b removes
# boot/stub, before make BOOT=1)
oldjob
COMMIT=1111111111111111111111111111111111111111; C12=${COMMIT:0:12}
link_outputs NEW; rec link ok '2026-11-04 10:00' ", \"prepared_from\": {\"port_commit\": \"$COMMIT\"}"
publish_link "$BD"; check A "$C12"

# B: this job's make BOOT=1 rebuilt guest and boot; frontier.py exited non-zero before writing frontier.tsv
# (Boot.run: status warn, the file is not rewritten), and the real exe is used (step 3b removed boot/synth)
rm -rf "$BD/boot/synth" "$BD/boot/stub"
echo NEW > "$BD/guest/fnvnx.elf"; rec guest ok '2026-11-04 10:10' ', "metrics": {"guest_undefined": 3811}'
echo "fnvnx boot: calling WinMain NEW" > "$BD/boot/emu.log"; echo NEW > "$BD/boot/summary.txt"; : > "$BD/boot/frontier.txt"
rec boot warn '2026-11-04 10:20' ', "metrics": {"boot_winmain": 1}, "stop": "STOPPED: NEW"'
COMMIT=2222222222222222222222222222222222222222; C12=${COMMIT:0:12}
rec link ok '2026-11-04 10:00' ", \"prepared_from\": {\"port_commit\": \"$COMMIT\"}"
publish_link "$BD"; check B "$C12"

# C: the clock stepped back between the previous job and this one
oldjob
COMMIT=3333333333333333333333333333333333333333; C12=${COMMIT:0:12}
link_outputs NEW; rec link ok '2026-11-04 09:05' ", \"prepared_from\": {\"port_commit\": \"$COMMIT\"}"
publish_link "$BD"; check C "$C12"
exit $fail
