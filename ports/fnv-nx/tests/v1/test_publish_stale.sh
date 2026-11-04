#!/usr/bin/env bash
# test_publish_stale.sh - V1 (D14) against J-0125/J-0135: "link publishes never carry a stale guest or boot".
#
# Runs the real tools/farm/full_job.sh (which runs the real link_job.sh, then make BOOT=1, the stub boot step and
# publish_link twice) for this job's commit NEW, in a farm home whose full/build still holds what a previous job
# (commit OLD) left: link, guest, boot on the synthesized image, a stub boot. Only the pipeline is mocked: a Makefile
# in a scratch copy of the port's tools that writes the stage records the scenario asks for (MOCK_MODE). The boots use
# a synthetic "player exe" on a local gamedata/exe branch. Every publish lands in a throw-away local artifacts
# repository; both publishes of NEW (link_job.sh's, read from link-head's history, and full_job.sh's, link/<c12>) are
# checked for files and meta.json fields of OLD, and for the exe's bytes (scan_gamedata_leak.py).
#   A  link_job.sh's publish, before make BOOT=1 (link/<c12> as the fuzz farms see it in the meantime)
#   B  make BOOT=1 rebuilt guest and boot, but frontier.py failed (boot warn) before writing frontier.tsv
#   C  a wall-clock step back between the jobs: the previous job's records are newer than this job's link
#   D  make BOOT=1 failed in the guest stage: nothing of a boot may be claimed (files or meta.json boot_* fields)
#   E  this job's link failed: nothing is published for NEW at all
#   bash tests/v1/test_publish_stale.sh   -> one line per case and publish, exit 1 when any shows a defect
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PORT=$(cd "$HERE/../.." && pwd)
T=$(mktemp -d) && [ -d "$T" ] || { echo "mktemp failed"; exit 2; }   # never run with T="" (writes under /)
[ -n "${V1_KEEP:-}" ] || trap 'rm -rf "$T"' EXIT   # V1_KEEP=1: keep the scratch for a look
g() { git -c user.name=t -c user.email=t@invalid "$@"; }

# the port's tools in a scratch repository (full_job.sh's FARM_SRC must be a checkout at <commit>), with a mock pipeline
SRC=$T/src; P=$SRC/ports/fnv-nx; mkdir -p "$P/tests"
cp -r "$PORT/tools" "$PORT/toolchain" "$P/"; cp -r "$PORT/tests/s5" "$PORT/tests/v1" "$P/tests/"
cat > "$P/Makefile" <<'EOF'
.PHONY: all fetch toolchain link
all: ; @python3 -I $(CURDIR)/mock_pipe.py boot '$(BUILD_DIR)'
fetch: ; @:
toolchain: ; @python3 -I $(CURDIR)/mock_pipe.py toolchain '$(BUILD_DIR)'
link: ; @python3 -I $(CURDIR)/mock_pipe.py link '$(BUILD_DIR)'
EOF
cat > "$P/mock_pipe.py" <<'EOF'
import json, os, subprocess, sys
stage, bd = sys.argv[1], sys.argv[2]
mode = os.environ.get('MOCK_MODE', 'ok')
here = os.path.dirname(os.path.abspath(__file__))
commit = subprocess.check_output(['git', '-C', here, 'rev-parse', 'HEAD'], text=True).strip()
def rec(s, status, extra=None):
    os.makedirs(os.path.join(bd, s), exist_ok=True)
    r = {'stage': s, 'status': status}
    r.update(extra or {})
    json.dump(r, open(os.path.join(bd, s, 'stage.json'), 'w'))
def w(rel, text):
    p = os.path.join(bd, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w').write(text)
if stage == 'toolchain':
    rec('toolchain', 'ok', {'artifacts': {'prefix': '/nonexistent', 'devkitpro': '/nonexistent'}})
elif stage == 'link':
    if mode == 'linkfail':
        rec('link', 'fail')
        sys.exit(2)
    vd = os.path.join(bd, 'link', 'out', 'vtables')   # as tools/link/vtables.py run(): old .vt.S / .o go first
    for n in os.listdir(vd) if os.path.isdir(vd) else ():
        if n.endswith(('.vt.S', '.o')):
            os.remove(os.path.join(vd, n))
    for f in ('aliases.ld', 'refs.tsv', 'abi.tsv', 'addrmap.tsv', 'layouts.tsv', 'cache/msvc.tsv', 'vtables/new.vt.S'):
        w('link/out/' + f, 'NEW\n')
    w('link/fixed/zz_bridges.o', 'NEW\n'); w('link/summary.txt', 'NEW\n')
    rec('compile', 'ok')
    rec('link', 'ok', {'prepared_from': {'port_commit': commit}})
else:   # make BOOT=1: the link stays cached; guest, boot
    if mode == 'guestfail':
        rec('guest', 'fail')
        sys.exit(2)
    w('guest/fnvnx.elf', 'NEW\n')
    rec('guest', 'ok', {'metrics': {'guest_undefined': 3811}})
    w('boot/emu.log', 'fnvnx boot: calling WinMain NEW\n'); w('boot/summary.txt', 'NEW\n'); w('boot/frontier.txt', '')
    if mode != 'nofrontier':
        w('boot/frontier.tsv', 'NEW\n')
    rec('boot', 'warn', {'metrics': {'boot_winmain': 1}, 'stop': 'STOPPED: NEW'})
EOF
g init -q "$SRC"; g -C "$SRC" add -A; g -C "$SRC" commit -qm tools

# the artifacts repository, with a synthetic player exe on gamedata/exe
git init -q --bare "$T/origin.git"
g clone -q "$T/origin.git" "$T/artifacts" 2>/dev/null
g -C "$T/artifacts" commit -q --allow-empty -m init; g -C "$T/artifacts" push -q origin HEAD:refs/heads/main 2>/dev/null
head -c 65536 /dev/urandom > "$T/fake.exe"; printf 'MZ' | dd of="$T/fake.exe" conv=notrunc 2>/dev/null
g init -q "$T/gd"; (cd "$T/gd" && g checkout -q -b gamedata/exe && cp "$T/fake.exe" FalloutNV.exe && g add -A && g commit -qm exe \
	&& g push -q "$T/origin.git" gamedata/exe 2>/dev/null)
export FARM_EXE_SHA256; FARM_EXE_SHA256=$(sha256sum "$T/fake.exe" | cut -d' ' -f1)
export ARTIFACTS=$T/artifacts FARM_HOME=$T/farm FARM_SRC=$SRC FARM_EXT=$T/ext FARM_TREE=$T/tree FARM_OBJS=$T/objs/objects.tsv
export FARM_V1=0 FARM_APT=0 FARM_TREE_WAIT=0 FARM_OBJ_WAIT=0 JOBS=1
# the mock toolchain's prefix: full_job.sh then skips the fnvnx-a64 build-host check (makeinfo bison flex), which a
# development host need not pass (the test failed on hosts without flex, not on the publish)
export FNVNX_TC=/nonexistent
mkdir -p "$FARM_EXT" "$FARM_TREE/build" "$T/objs"; echo '{}' > "$FARM_TREE/build/units.json"; : > "$FARM_OBJS"
BD=$FARM_HOME/full/build

orec() { # the previous job's record: orec <stage> <status> <time> [json extra]
	mkdir -p "$BD/$1"
	printf '{"stage": "%s", "status": "%s"%s}\n' "$1" "$2" "${4:-}" > "$BD/$1/stage.json"
	touch -d "$3" "$BD/$1/stage.json"
}
oldjob() { # oldjob <time of its link>: what the previous job (commit OLD) left in full/build
	local t=$1
	rm -rf "$BD"; mkdir -p "$BD/link/out/cache" "$BD/link/out/vtables" "$BD/link/fixed" "$BD/guest" "$BD/boot/synth" "$BD/boot/stub"
	for f in aliases.ld refs.tsv abi.tsv addrmap.tsv layouts.tsv cache/msvc.tsv vtables/old.vt.S; do echo OLD > "$BD/link/out/$f"; done
	echo OLD > "$BD/link/fixed/zz_bridges.o"; echo OLD > "$BD/link/summary.txt"
	orec compile ok "$t"; orec link ok "$t" ', "prepared_from": {"port_commit": "old"}'
	echo OLD > "$BD/guest/fnvnx.elf"; orec guest ok "$t 10 min" ', "metrics": {"guest_undefined": 4423}'
	for f in summary.txt frontier.txt frontier.tsv; do echo OLD > "$BD/boot/$f"; done
	echo "fnvnx boot: calling WinMain OLD" > "$BD/boot/emu.log"; echo OLD > "$BD/boot/synth/synth.exe"
	printf 'fnvnx boot: calling WinMain\nSTOPPED: OLD fault\n' > "$BD/boot/stub/emu_stub.log"
	orec boot warn "$t 20 min" ', "metrics": {"boot_winmain": 1}, "stop": "STOPPED: OLD fault 0x00aa3e40"'
}
fail=0
check() { # check <label> <git ref in origin> [noboot]: OLD files or fields, exe bytes, (noboot) any boot claim
	local c=$1 ref=$2 out=$T/pub bad=""
	rm -rf "$out"; mkdir -p "$out"
	git -C "$T/origin.git" archive "$ref" | tar -x -C "$out" || { echo "$c: NOT PUBLISHED ($ref)"; fail=1; return; }
	bad=$(grep -rl OLD "$out" | sed "s|^$out/||" | tr '\n' ' ')
	bad="$bad $(python3 -I - "$out/meta.json" "${3:-}" <<'EOF'
import json, sys
m = json.load(open(sys.argv[1]))
bad = ['meta.%s=%s' % (k, str(m[k])[:24]) for k in sorted(m)
       if 'OLD' in json.dumps(m[k]) or m[k] == 4423 or (sys.argv[2] and k.startswith('boot') and m[k] is not None)]
print(' '.join(bad))
EOF
)"
	[ -n "${3:-}" ] && [ -d "$out/boot" ] && bad="$bad boot/"
	python3 -I "$HERE/scan_gamedata_leak.py" "$T/fake.exe" "$out" >/dev/null || bad="$bad EXE-BYTES"
	bad=$(echo $bad)
	if [ -n "$bad" ]; then echo "$c: STALE: $bad"; fail=1; else echo "$c: ok"; fi
}
newjob() { # newjob <mode>: a new commit of the tools, then the real full_job.sh on it -> C12
	g -C "$SRC" commit -q --allow-empty -m "job $1"; COMMIT=$(git -C "$SRC" rev-parse HEAD); C12=${COMMIT:0:12}
	MOCK_MODE=$1 bash "$P/tools/farm/full_job.sh" "$COMMIT" "$T/report-$C12.md" > "$T/full-$C12.log" 2>&1 \
		|| { echo "full_job.sh exited $? ($1), see:"; tail -5 "$T/full-$C12.log"; fail=1; }
}
publishes() { git -C "$T/origin.git" log --format=%H --grep="^$1 link outputs" link-head | wc -l; }
first_publish() { git -C "$T/origin.git" log --reverse --format=%H --grep="^$1 link outputs" link-head | head -1; }

# A + B: frontier.py fails in this job's boot
oldjob '2026-11-04 09:00'; newjob nofrontier
check "A (link_job.sh's publish)" "$(first_publish "$C12")"
check "B (full_job.sh's publish, no frontier.tsv this time)" "refs/heads/link/$C12"
# C: the previous job's records are a day newer than this job's
oldjob 'tomorrow'; newjob ok
check "C (clock step, link_job.sh's publish)" "$(first_publish "$C12")"
check "C (clock step, full_job.sh's publish)" "refs/heads/link/$C12"
# D: the guest stage failed
oldjob '2026-11-04 09:00'; newjob guestfail
check "D (guest failed, full_job.sh's publish)" "refs/heads/link/$C12" noboot
# full_job.sh publishes a second time (report, audits) also when make BOOT=1 failed; it read make's exit 2 as "no tree
# or no objects" (GNU make exits 2 on any failed recipe)
n=$(publishes "$C12"); grep -q 'no tree or no objects' "$T/report-$C12.md" && n="$n, report: no tree or no objects"
[ "$n" = 2 ] && echo "D (guest failed, second publish): ok" || { echo "D (guest failed, second publish): MISSING ($n publishes)"; fail=1; }
# E: the link failed
oldjob '2026-11-04 09:00'; newjob linkfail
if git -C "$T/origin.git" rev-parse -q --verify "refs/heads/link/$C12" >/dev/null; then
	check "E (link failed: published anyway)" "refs/heads/link/$C12"; fail=1
else echo "E (link failed): ok (not published)"; fi
exit $fail
