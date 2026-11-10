#!/usr/bin/env bash
# brepro_job.sh - farm job: does VC9 (cl 15.00.30729) /Brepro make COFF objects reproducible? What differs without it?
#
#   bash <checkout>/ports/fnv-nx/farm/brepro_job.sh <report.md>        (run directly on a farm, not through the runner)
#
# Reuses tools/farm/equiv_job.sh --toolchain (Wine + VC9 SP1 install / fetch from branch toolchain/msvc9 / sanity run;
# prints "toolchain: <sp1 root> <rtm root>"; the Wine prefix is $FARM_HOME/msvc9/wineprefix) and sources
# tools/farm/lib_job.sh (log). Writes its own synthetic sources (a.c, b.cpp, c.cpp), compiles each twice 2 s apart
# (same directory and file name) for six flag sets, compares the pairs byte for byte and through verify/coffnorm.py,
# then lib (archive) and link /DLL (PE) with and without /Brepro. Every step records its rc and goes on.
# Needs: what equiv_job.sh --toolchain needs (Ubuntu 24.04, root for apt, git, curl, python3, ~6 GB, the artifacts
# repository attached with push on a fresh farm). Env: FARM_HOME (~/fnvnx-farm), ARTIFACTS. Report: numbers and yes/no.
# Exit 0 once the report is written. Work dir: $FARM_HOME/brepro (rebuilt every run).
set -uo pipefail
REPORT=${1:?usage: brepro_job.sh <report.md>}; REPORT=$(readlink -f "$REPORT")
HERE=$(cd "$(dirname "$0")" && pwd); NX=$(dirname "$HERE")
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}; export FARM_HOME
# shellcheck source=/dev/null
source "$NX/tools/farm/lib_job.sh"
tcl=$(bash "$NX/tools/farm/equiv_job.sh" --toolchain 2>&1 | tee "$FARM_HOME/brepro-toolchain.log" | tail -1)
case $tcl in "toolchain: "*) ROOT=$(echo "$tcl" | cut -d' ' -f2) ;; *) log "toolchain step failed"; tail -20 "$FARM_HOME/brepro-toolchain.log"; exit 1 ;; esac
export WINEPREFIX=$FARM_HOME/msvc9/wineprefix WINEDEBUG=-all WINEARCH=win32
W=$FARM_HOME/brepro; rm -rf "$W"; mkdir -p "$W/w"; ROWS=$W/rows.tsv; : > "$ROWS"
win() { printf 'Z:%s' "$1" | tr / '\\'; }
ci() { for d in "$@"; do [ -d "$d" ] && { echo "$d"; return; }; done; echo "$1"; }   # first directory that exists
inc="$(ci "$ROOT/VC/include" "$ROOT/VC/Include")" lib="$(ci "$ROOT/VC/lib" "$ROOT/VC/Lib")"
sdki="$(ci "$ROOT/SDK/Include" "$ROOT/SDK/include")" sdkl="$(ci "$ROOT/SDK/Lib" "$ROOT/SDK/lib")"
export INCLUDE="$(win "$inc");$(win "$sdki")" LIB="$(win "$lib");$(win "$sdkl")" WINEPATH="$(win "$ROOT/VC/bin")"
tool() { local t=$1; shift; wine "$ROOT/VC/bin/$t.exe" /nologo "$@"; }   # tool <cl|link|lib> args, in the current dir

cat > "$W/w/a.c" <<'EOS'
static const char msg[] = "brepro probe a";
int a_sum(const int *p, int n) { int s = 0, i; for (i = 0; i < n; i++) s += p[i] * 3; return s + (int)sizeof(msg); }
const char *a_msg(void) { return msg; }
EOS
cat > "$W/w/b.cpp" <<'EOS'
template <class T> T tmax(T x, T y) { return x > y ? x : y; }
inline int twice(int v) { return v * 2; }
class Base { public: virtual int f(int v) { return twice(v); } virtual ~Base() {} };
class Derived : public Base { public: virtual int f(int v) { return tmax(v, 7) + 1; } };
int use(int v) { Derived d; Base *b = &d; return b->f(v) + tmax(1.5, 2.5 * v) > 0; }
EOS
cat > "$W/w/c.cpp" <<'EOS'
const char *c_file(void) { return __FILE__; }
EOS

nrc() { local f=$1; shift; "$@" > "$f.log" 2>&1; echo $?; }      # run, keep the log, print rc
cmpgen() {  # <kind coff|pe|ar> <obj1> <obj2> -> "identical diffbytes infield outside"; field offsets from coffnorm fields
  python3 -I - "$@" "$NX/verify/coffnorm.py" <<'EOP'
import subprocess, sys
kind, p1, p2, cn = sys.argv[1:5]
a, b = open(p1, 'rb').read(), open(p2, 'rb').read()
d = [i for i in range(min(len(a), len(b))) if a[i] != b[i]] + list(range(min(len(a), len(b)), max(len(a), len(b))))
cov = set()
if kind != 'pe':
    out = subprocess.run(['python3', '-I', cn, 'fields', p1], capture_output=True, text=True).stdout
    for ln in out.splitlines():
        t = ln.split()
        if len(t) >= 3 and t[0].startswith('0x'):
            cov.update(range(int(t[0], 16), int(t[0], 16) + int(t[1])))
inf = sum(1 for i in d if i in cov)
print('yes' if not d else 'no', len(d), inf, len(d) - inf)
EOP
}
pair() {  # <label> <kind> <o1> <o2> <cl rc> <d9 count>: one row of rows.tsv
  local lab=$1 kind=$2 o1=$3 o2=$4 rc=$5 d9=$6 id nb inf out chk=- idn=- rem=-
  read -r id nb inf out < <(cmpgen "$kind" "$o1" "$o2")
  if [ "$kind" != pe ]; then
    python3 -I "$NX/verify/coffnorm.py" check --allow-typeserver "$o1" "$o2" > "$W/chk.log" 2>&1; chk=$?
    if python3 -I "$NX/verify/coffnorm.py" normalize "$o1" -o "$o1.n" --allow-typeserver > /dev/null 2>&1 \
       && python3 -I "$NX/verify/coffnorm.py" normalize "$o2" -o "$o2.n" --allow-typeserver > /dev/null 2>&1; then
      read -r idn rem _ _ < <(cmpgen "$kind" "$o1.n" "$o2.n")
    else idn=error; rem=-; fi
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$lab" "$rc" "$d9" "${id:-?}" "${nb:--}" "$chk" "$idn" "$rem" "${inf:--}/${out:--}" >> "$ROWS"
}

FLAGSETS=("/c /O2" "/c /O2 /Brepro" "/c /O2 /Z7" "/c /O2 /Z7 /Brepro" "/c /O2 /Zi /Fdx.pdb" "/c /O2 /Zi /Fdx.pdb /Brepro")
cd "$W/w" || exit 1
for src in a.c b.cpp c.cpp; do
  base=${src%.*}
  for fl in "${FLAGSETS[@]}"; do
    tag=$(echo "$fl" | tr -c 'A-Za-z0-9\n' _); rm -f x.pdb vc*.pdb "$base.obj"
    # shellcheck disable=SC2086
    rc1=$(nrc "$W/c1.$base.$tag" tool cl $fl "$src" "/Fo$base.obj"); mv -f "$base.obj" "$W/$base.$tag.1.obj" 2>/dev/null
    rm -f x.pdb vc*.pdb; sleep 2
    # shellcheck disable=SC2086
    rc2=$(nrc "$W/c2.$base.$tag" tool cl $fl "$src" "/Fo$base.obj"); mv -f "$base.obj" "$W/$base.$tag.2.obj" 2>/dev/null
    d9=$(cat "$W/c1.$base.$tag.log" "$W/c2.$base.$tag.log" | grep -c -e D9002 -e D9025 || true)
    pair "$src | $fl" coff "$W/$base.$tag.1.obj" "$W/$base.$tag.2.obj" "$rc1/$rc2" "$d9"
  done
done
# archive and PE: inputs are fixed copies of the plain /c /O2 objects
for n in a b; do cp "$W/$n._c__O2.1.obj" "$W/w/$n.obj" 2>/dev/null; done
for i in 1 2; do rm -f x.lib; rc=$(nrc "$W/lib$i" tool lib /OUT:x.lib a.obj b.obj); eval "librc$i=$rc"; mv -f x.lib "$W/x.lib.$i" 2>/dev/null; [ $i = 1 ] && sleep 2; done
pair "lib /OUT:x.lib a.obj b.obj" ar "$W/x.lib.1" "$W/x.lib.2" "$librc1/$librc2" 0
for fl in "" "/Brepro"; do
  for i in 1 2; do rm -f x.dll x.lib x.exp; eval "lrc$i=$(nrc "$W/link$i" tool link /DLL /NOENTRY /NODEFAULTLIB /OUT:x.dll $fl a.obj)"; mv -f x.dll "$W/x.dll.$i" 2>/dev/null; [ $i = 1 ] && sleep 2; done
  pair "link /DLL /NOENTRY a.obj $fl" pe "$W/x.dll.1" "$W/x.dll.2" "$lrc1/$lrc2" "$(cat "$W/link1.log" "$W/link2.log" | grep -c -e D9002 -e D9025 -e LNK4044 || true)"
done

python3 -I - "$ROWS" "$REPORT" <<'EOP'
import sys
rows = [l.rstrip('\n').split('\t') for l in open(sys.argv[1])]
cl = [r for r in rows if r[0].split(' | ')[0] in ('a.c', 'b.cpp', 'c.cpp')]
br = [r for r in cl if '/Brepro' in r[0]]
z7 = [r for r in cl if '/Z7' in r[0] and '/Brepro' not in r[0]]
if br and any(int(r[2]) > 0 for r in br): v = 'REJECTED (unknown option)'
elif br and all(r[3] == 'yes' for r in br) and all(r[1] == '0/0' for r in br): v = 'EFFECTIVE (all /Brepro pairs identical)'
else: v = 'NOT EFFECTIVE'
suff = 'yes' if z7 and all(r[6] == 'yes' for r in z7) else 'no'
o = ['Brepro in VC9 cl: %s; coffnorm sufficient for /Z7 objects: %s' % (v, suff), '',
     '| source / tool and flags | rc 1st/2nd | D9002/D9025 lines | pair identical | differing bytes | coffnorm check rc | identical after coffnorm | remaining bytes | differing bytes in coffnorm fields/outside |',
     '|---|---|---|---|---|---|---|---|---|']
o += ['| ' + ' | '.join(r) + ' |' for r in rows]
open(sys.argv[2], 'w').write('\n'.join(o) + '\n')
EOP
log "report written: $REPORT"; exit 0
