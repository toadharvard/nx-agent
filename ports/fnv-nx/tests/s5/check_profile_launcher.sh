#!/usr/bin/env bash
# check_profile_launcher.sh - the first-run profile of platform/win32/profile.cpp against the real launcher's, on the
# player's own install (game data: nothing of it is printed beyond key names, nothing is committed).
#
#   tests/s5/check_profile_launcher.sh <install dir> <launcher profile dir>
#
# <launcher profile dir>: a Wine prefix's users/<name> after FalloutNVLauncher.exe ran once with no profile, e.g.
#   WINEPREFIX=<pfx> wine <install>/FalloutNVLauncher.exe   (it writes the files on start; then close it)
# It holds Documents/My Games/FalloutNV/{Fallout.ini,FalloutPrefs.ini} and AppData/Local/FalloutNV/{plugins.txt,
# NVDLCList.txt}. The check builds tests/s4's platform objects, runs fnvnx_first_run_profile() on a scratch card with the
# install's Fallout_default.ini, and compares file by file:
#   Fallout.ini        byte for byte
#   FalloutPrefs.ini   the same sections, keys and values, apart from the machine-specific keys: [Display] sD3DDevice
#                      (the adapter's name: the launcher's GPU vs ours) and iSize W / iSize H (the Switch's 1280x720)
#   plugins.txt, NVDLCList.txt   byte for byte
# Prints one line per check and "check_profile_launcher: N ok, M differ"; exit 1 when any differs.
set -uo pipefail
INST=${1:?install dir}; LP=${2:?launcher profile dir}
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
W=$(mktemp -d "${TMPDIR:-/tmp}/s5-profile.XXXXXX") || exit 1
trap 'rm -rf "$W"' EXIT
make -s -C "$ROOT/tests/s4" "$ROOT/tests/s4/build/s4_tests" > "$W/make.log" 2>&1 || { cat "$W/make.log"; exit 2; }
cat > "$W/main.cpp" <<'EOF'
#include <stdio.h>
#include "fnvnx_win32_ext.h"
extern "C" unsigned fnvnx_first_run_profile(void);
int main(int argc, char **argv) { fnvnx_path_reset(argv[1]); printf("mask %u\n", fnvnx_first_run_profile()); return 0; }
EOF
objs=$(find "$ROOT/tests/s4/build/platform" -name '*.o')
g++ -std=gnu++17 -fshort-wchar -pthread -I"$ROOT/platform/include" -iquote "$ROOT/shim/include" -I"$ROOT/platform/crt" \
  "$W/main.cpp" $objs -o "$W/profile" -lm 2> "$W/link.log" || { cat "$W/link.log"; exit 2; }
mkdir -p "$W/card"; ln -s "$(readlink -f "$INST/Fallout_default.ini")" "$W/card/Fallout_default.ini"   # the install is the root (NX1)
"$W/profile" "$W/card" > "$W/run.log" 2>&1 || { cat "$W/run.log"; exit 2; }
python3 -I - "$W/card/user" "$LP" <<'EOF'
import os, sys
ours, theirs = sys.argv[1], sys.argv[2]
MACHINE = {('display', 'sd3ddevice'), ('display', 'isize w'), ('display', 'isize h')}
def ini(p):
    d, sec, order = {}, None, []
    for line in open(p, encoding='latin-1'):
        s = line.strip()
        if s.startswith('['):
            sec = s[1:s.index(']')].lower(); order.append(sec); continue
        if '=' in s and sec is not None and not s.startswith(';'):
            k, _, v = s.partition('='); d[(sec, k.strip().lower())] = v.strip()
    return d, order
ok = bad = 0
def say(name, good, why=''):
    global ok, bad
    print(f"{name}: {'ok' if good else 'DIFFERS'}{' - ' + why if why else ''}")
    ok += good; bad += not good
for rel in ('Documents/My Games/FalloutNV/Fallout.ini', 'AppData/Local/FalloutNV/plugins.txt',
            'AppData/Local/FalloutNV/NVDLCList.txt'):
    a, b = os.path.join(ours, rel), os.path.join(theirs, rel)
    if not os.path.exists(a) or not os.path.exists(b):
        say(rel, False, f"ours {'present' if os.path.exists(a) else 'missing'}, launcher's "
            f"{'present' if os.path.exists(b) else 'missing'}"); continue
    say(rel, open(a, 'rb').read() == open(b, 'rb').read(), 'byte for byte')
rel = 'Documents/My Games/FalloutNV/FalloutPrefs.ini'
a, oa = ini(os.path.join(ours, rel)); b, ob = ini(os.path.join(theirs, rel))
say(rel + ' sections', oa == ob, f"{len(oa)} vs {len(ob)} sections, same order" if oa == ob else 'order differs')
diff = sorted(k for k in set(a) | set(b) if k not in MACHINE and a.get(k) != b.get(k))
say(rel + ' keys', not diff, f"{len(set(a) - MACHINE)} keys equal apart from {sorted('/'.join(k) for k in MACHINE)}"
    if not diff else f"{len(diff)} differ, e.g. {['/'.join(k) for k in diff[:6]]}")
say(rel + ' sD3DDevice', ('display', 'sd3ddevice') in a and a[('display', 'sd3ddevice')].startswith('"'),
    'written, quoted as the launcher writes it')
print(f"check_profile_launcher: {ok} ok, {bad} differ")
sys.exit(1 if bad else 0)
EOF
