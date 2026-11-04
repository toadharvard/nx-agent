#!/usr/bin/env bash
# test_link_exe.sh - S2d against J-0144: link_job.sh hands the player's exe to the link (FNVNX_EXE, for fnvlink bodies'
# exe-addr class) through lib_job.sh's job_gamedata, and never puts the exe's bytes into its report or publish.
#
# Runs the real tools/farm/link_job.sh on a scratch copy of the port's tools whose Makefile is a mock: `make link`
# records the FNVNX_EXE it was given and writes an ok link. The exe is a synthetic one on a local gamedata/exe branch.
#   1  the gamedata branch has the pinned exe: make link sees FNVNX_EXE = the fetched exe, the report names it
#   2  FARM_REAL_EXE=0: FNVNX_EXE is empty, the report says so
#   3  an exe with another sha256: refused, FNVNX_EXE is empty
#   4  the publish (FARM_PUBLISH=always, local artifacts remote) and the report hold no byte run of the exe; the
#      publish has S2d's tables out/bodies/{bodies,bind,synth}.tsv + stats.txt, not its work dir or objects
#   5  build.py's link stage: link_exe() picks FNVNX_EXE, else a BOOT_IMAGE file, and the stage fingerprint changes
#      with the exe's content (a cached link made without the exe is redone with it)
#   bash tests/farm/test_link_exe.sh   -> "link_exe: ok", exit 1 on any failure
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PORT=$(cd "$HERE/../.." && pwd)
T=$(mktemp -d) && [ -d "$T" ] || { echo "mktemp failed"; exit 2; }
trap 'rm -rf "$T"' EXIT
g() { git -c user.name=t -c user.email=t@invalid "$@"; }
fail=0
check() { [ "$1" = "$2" ] || { echo "FAIL: $3: want '$2', got '$1'"; fail=1; }; }

SRC=$T/src; P=$SRC/ports/fnv-nx; mkdir -p "$P"
cp -r "$PORT/tools" "$P/"
cat > "$P/Makefile" <<'EOF'
.PHONY: link
link: ; @python3 -I $(CURDIR)/mock_pipe.py '$(BUILD_DIR)'
EOF
cat > "$P/mock_pipe.py" <<'EOF'
import json, os, subprocess, sys
bd = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
commit = subprocess.check_output(['git', '-C', here, 'rev-parse', 'HEAD'], text=True).strip()
open(os.environ['MOCK_SEEN'], 'w').write(os.environ.get('FNVNX_EXE', '<unset>'))
def w(rel, text):
    p = os.path.join(bd, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w').write(text)
for f in ('aliases.ld', 'refs.tsv', 'abi.tsv', 'addrmap.tsv', 'layouts.tsv', 'cache/msvc.tsv', 'vtables/a.vt.S'):
    w('link/out/' + f, 'x\n')
w('link/fixed/zz_bridges.o', 'x\n'); w('link/summary.txt', 'x\n')
for f in ('bodies.tsv', 'bind.tsv', 'synth.tsv', 'stats.txt', 'work/u.bd.cpp', 'zz_bd_u.o'):
    w('link/out/bodies/' + f, 'x\n')
for s, r in (('compile', {}), ('link', {'prepared_from': {'port_commit': commit},
                                        'metrics': {'link_bodies_s': 1.0, 'link_bodies_exe_addr': 7}})):
    os.makedirs(os.path.join(bd, s), exist_ok=True)
    json.dump(dict(stage=s, status='ok', **r), open(os.path.join(bd, s, 'stage.json'), 'w'))
EOF
g init -q "$SRC"; g -C "$SRC" add -A; g -C "$SRC" commit -qm tools
COMMIT=$(git -C "$SRC" rev-parse HEAD)

git init -q --bare "$T/origin.git"
g clone -q "$T/origin.git" "$T/artifacts" 2>/dev/null
g -C "$T/artifacts" commit -q --allow-empty -m init; g -C "$T/artifacts" push -q origin HEAD:refs/heads/main 2>/dev/null
head -c 65536 /dev/urandom > "$T/fake.exe"; printf 'MZ' | dd of="$T/fake.exe" conv=notrunc 2>/dev/null
g init -q "$T/gd"; (cd "$T/gd" && g checkout -q -b gamedata/exe && cp "$T/fake.exe" FalloutNV.exe && g add -A \
	&& g commit -qm exe && g push -q "$T/origin.git" gamedata/exe 2>/dev/null)
export ARTIFACTS=$T/artifacts FARM_SRC=$SRC FARM_EXT=$T/ext FARM_TREE=$T/tree FARM_OBJS=$T/objs/objects.tsv
export FARM_APT=0 FARM_TREE_WAIT=0 FARM_OBJ_WAIT=0 JOBS=1 PRISTINE_OBJS=$T/pristine MOCK_SEEN=$T/seen
mkdir -p "$FARM_EXT" "$FARM_TREE/build" "$T/objs" "$PRISTINE_OBJS"; echo '{}' > "$FARM_TREE/build/units.json"; : > "$FARM_OBJS"
PIN=$(sha256sum "$T/fake.exe" | cut -d' ' -f1)

run() { # run <home> [env...]: link_job.sh -> $T/seen, $T/<home>.md
	local h=$1; shift; rm -f "$T/seen"
	env FARM_HOME="$T/$h" "$@" bash "$P/tools/farm/link_job.sh" "$COMMIT" "$T/$h.md" > "$T/$h.log" 2>&1 \
		|| { echo "FAIL: link_job.sh exited $? ($h):"; tail -5 "$T/$h.log"; fail=1; }
}
run h1 FARM_EXE_SHA256="$PIN" FARM_PUBLISH=always
check "$(cat "$T/seen" 2>/dev/null)" "$T/h1/gamedata/FalloutNV.exe" "1: FNVNX_EXE for make link"
grep -q "^- bodies --exe: the player's FalloutNV.exe" "$T/h1.md" || { echo "FAIL: 1: report names no exe"; fail=1; }
grep -q '^- bodies (S2d, 1.0 s): exe_addr 7' "$T/h1.md" || { echo "FAIL: 1: report has no bodies line"; fail=1; }
run h2 FARM_EXE_SHA256="$PIN" FARM_REAL_EXE=0 FARM_PUBLISH=0
check "$(cat "$T/seen" 2>/dev/null)" "" "2: FARM_REAL_EXE=0"
grep -q '^- bodies --exe: player exe not used' "$T/h2.md" || { echo "FAIL: 2: report"; fail=1; }
run h3 FARM_EXE_SHA256=0000 FARM_PUBLISH=0
check "$(cat "$T/seen" 2>/dev/null)" "" "3: another sha256"
mkdir -p "$T/pub"
git -C "$T/origin.git" archive "refs/heads/link/${COMMIT:0:12}" | tar -x -C "$T/pub" \
	|| { echo "FAIL: 4: nothing published"; fail=1; }
for f in bodies.tsv bind.tsv synth.tsv stats.txt; do
	[ -f "$T/pub/out/bodies/$f" ] || { echo "FAIL: 4: out/bodies/$f not published"; fail=1; }
done
[ -e "$T/pub/out/bodies/work" ] || [ -e "$T/pub/out/bodies/zz_bd_u.o" ] && { echo "FAIL: 4: bodies work dir or objects published"; fail=1; }
cp "$T/h1.md" "$T/pub/report-h1.md"
python3 -I "$PORT/tests/v1/scan_gamedata_leak.py" "$T/fake.exe" "$T/pub" >/dev/null || { echo "FAIL: 4: exe bytes"; fail=1; }

# 5: the link stage's choice of exe and its fingerprint (build.py's own functions, a stand-in build object)
python3 -I - "$PORT/tools/pipeline" "$T" <<'EOF' || fail=1
import os, sys
sys.path.insert(0, sys.argv[1]); t = sys.argv[2]
import build
class B:
    cfg = {'BOOT_IMAGE': 'auto', 'OVERRIDE': ''}
b = B()
for k in ('link_tool_inputs', 'up_id', 'extern_text_id', 'msvc_objs_id', 'game_cxx_id'):
    setattr(build, k, lambda *a, **k: 'same')
fp = lambda: build.Link.fingerprint(None, b)
open(t + '/e1', 'wb').write(b'MZone'); open(t + '/e2', 'wb').write(b'MZtwo')
bad = []
os.environ.pop('FNVNX_EXE', None)
none = fp()
bad += [] if build.link_exe(b) == '' else ['no exe: %r' % build.link_exe(b)]
os.environ['FNVNX_EXE'] = t + '/e1'
one = fp()
bad += [] if build.link_exe(b) == t + '/e1' else ['FNVNX_EXE: %r' % build.link_exe(b)]
os.environ['FNVNX_EXE'] = t + '/e2'
two = fp()
del os.environ['FNVNX_EXE']; b.cfg['BOOT_IMAGE'] = t + '/e1'
boot = fp()
bad += [] if build.link_exe(b) == t + '/e1' else ['BOOT_IMAGE: %r' % build.link_exe(b)]
os.environ['FNVNX_EXE'] = t + '/missing'; b.cfg['BOOT_IMAGE'] = 'auto'
bad += [] if build.link_exe(b) == '' else ['missing file: %r' % build.link_exe(b)]
bad += [] if len({repr(none), repr(one), repr(two)}) == 3 else ['fingerprint ignores the exe']
bad += [] if one == boot else ['FNVNX_EXE and the same BOOT_IMAGE give different fingerprints (full_job relinks)']
for x in bad:
    print('FAIL: 5: ' + x)
sys.exit(1 if bad else 0)
EOF
[ $fail = 0 ] && echo "link_exe: ok"
exit $fail
