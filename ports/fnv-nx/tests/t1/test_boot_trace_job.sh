#!/usr/bin/env bash
# T1: tools/farm/boot_trace_job.sh fails loudly. bt1 0e2a4b160f9e exited 0 with "guest 0 calls" and a compare
# traceback: `boot guest` began with rm -rf $W/guest, the directory the guest copy was kept in, so phase C booted a
# deleted guest. Here: the guest boot reuses the discovery's guest and writes a trace; a guest or reference with no
# trace, or a failing compare, exits 1 with a "- FAIL:" line under the title (the reference is still published); a
# discovery stopped by the wall-clock limit is a WARN note. boot_trace.py compare on a missing trace is an error
# line, not a traceback. Stand-ins for farm_boot.sh, wine_trace.sh, profile.py and boot_trace.py; a local remote.
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd)
T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0
ok() { if (eval "$2"); then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
Q=$T/p; mkdir -p "$Q/tools/farm" "$Q/tests/s5" "$Q/tools/lockstep" "$T/bin" "$T/game/Data"
cp "$P/tools/farm/boot_trace_job.sh" "$Q/tools/farm/"
for c in wine i686-w64-mingw32-gcc Xvfb aarch64-linux-gnu-gcc; do printf '#!/bin/sh\nexit 0\n' > "$T/bin/$c"; chmod +x "$T/bin/$c"; done
echo exe > "$T/game/FalloutNV.exe"; echo ini > "$T/game/Fallout_default.ini"
cat > "$Q/tests/s5/farm_boot.sh" <<'SH'
#!/usr/bin/env bash
# stand-in: <c12> <out>; FARM_GUEST_DIR must hold the guest (as the real one cds into it)
out=$2; tag=$(basename "$out"); mkdir -p "$out"
if [ -n "${FARM_GUEST_DIR:-}" ]; then [ -f "$FARM_GUEST_DIR/guest.nso" ] || { echo "no guest in $FARM_GUEST_DIR"; exit 1; }
else mkdir -p "$out/guest"; echo nso > "$out/guest/guest.nso"; echo elf > "$out/guest/fnvnx.elf"; fi
[ -n "${FARM_LINK_DIR:-}" ] || { mkdir -p "$out/link/out"; printf '0x401000\tf\n' > "$out/link/out/addrmap.tsv"; }
to=$(echo "$EMU_EXTRA" | sed -n 's/.*--trace-out \([^ ]*\).*/\1/p')
if [ "${FAKE_NOSTART:-}" = "$tag" ]; then   # emu_guest.py exits before the boot (bt1 74a1c281: link-head 4aa2f953)
	printf '   inject: heap_standin.o\n--inject /tmp/x/standins.o: _ZN14MessageHandler12FUN_00c3bcf0Eb is not in the guest\n' > "$out/emu_stub.log"
	echo 1 > "$out/emu_stub.rc"; exit 0
fi
echo "STOPPED: deadlock: {1: ('blocked', None)}" > "$out/emu_stub.log"; echo 0 > "$out/emu_stub.rc"
[ "$tag" = disc ] && [ -n "${FAKE_WALL:-}" ] && echo "STOPPED: run stopped by SIGTERM (wall-clock limit) in thread ?" > "$out/emu_stub.log"
if [ -n "$to" ] && [ "${FAKE_NOTRACE:-}" != "$tag" ]; then printf 'call\t1\t1\t0\tx\t0x401000\tf\n' > "$to"; fi
exit 0
SH
printf 'import os, sys\nos.makedirs(sys.argv[2], exist_ok=True)\n' > "$Q/tools/lockstep/profile.py"
cat > "$Q/tools/lockstep/wine_trace.sh" <<'SH'
#!/usr/bin/env bash
[ -n "${FAKE_NOWINE:-}" ] && exit 1
printf 'call\t1\t1\t0\tx\t0x401000\tf\n' > "$4"; echo "game ended"
SH
cat > "$Q/tests/s5/boot_trace.py" <<'PY'
import os, sys
cmd = sys.argv[1]
if cmd == "fnlist":
    print("0x401000 f")
elif cmd == "compare":
    if os.environ.get("FAKE_CMPFAIL"):
        sys.exit(1)
    j = sys.argv[sys.argv.index("--json") + 1]
    open(j, "w").write("{}"); print("lockstep: ended: same")
elif cmd == "seeds":
    open(sys.argv[sys.argv.index("--out") + 1], "w").write("{}\n"); print("seeds: 1")
PY
git init -q --bare "$T/origin.git"; git clone -q "$T/origin.git" "$T/art" 2> /dev/null
C=$(printf 'a%.0s' $(seq 40))
( cd "$T/art" && echo '{"commit": "'$C'"}' > meta.json && git add -A && git -c user.name=t -c user.email=t@t commit -qm l && git push -q origin "HEAD:refs/heads/link/${C:0:12}" ) > /dev/null 2>&1
run() {   # run <name> [VAR=value ...]: the job's exit code in $T/<name>.rc, its report in $T/<name>.md
	local n=$1; shift
	( export PATH=$T/bin:$PATH FARM_HOME=$T/home-$n FARM_GAME=$T/game FARM_APT=0 ARTIFACTS=$T/art "$@"
	  bash "$Q/tools/farm/boot_trace_job.sh" "$C" "$T/$n.md" > "$T/$n.log" 2>&1; echo $? > "$T/$n.rc" )
}
run good
ok "a good run: exit 0, guest calls traced, no FAIL line" '[ "$(cat $T/good.rc)" = 0 ] && grep -q "| guest | 1 calls" $T/good.md && ! grep -q "^- FAIL" $T/good.md'
ok "the guest boot reuses the discovery guest (not wiped with its work dir)" '[ -f $T/home-good/boot_trace/guest-bin/guest.nso ]'
run noguest FAKE_NOTRACE=guest
ok "the guest wrote no trace: exit 1, FAIL under the title, compare not run" '[ "$(cat $T/noguest.rc)" = 1 ] && sed -n 3p $T/noguest.md | grep -q "^- FAIL: the guest wrote no trace" && grep -q "| compare | not run" $T/noguest.md'
ok "the reference is still published" 'git -C $T/origin.git rev-parse -q --verify "trace/${C:0:12}" > /dev/null && grep -q "published trace/" $T/noguest.md'
run nowine FAKE_NOWINE=1
ok "the reference wrote no trace: exit 1, FAIL" '[ "$(cat $T/nowine.rc)" = 1 ] && grep -q "^- FAIL: the Wine reference wrote no trace" $T/nowine.md'
run cmpfail FAKE_CMPFAIL=1
ok "compare fails: exit 1, FAIL" '[ "$(cat $T/cmpfail.rc)" = 1 ] && grep -q "^- FAIL: boot_trace.py compare" $T/cmpfail.md'
run wall FAKE_WALL=1
ok "discovery at the wall-clock limit: a WARN note, still exit 0" '[ "$(cat $T/wall.rc)" = 0 ] && grep -q "WARN: discovery stopped by the wall-clock limit" $T/wall.md'
# the stub boot never started: NO TRACE with emu_guest.py's last line, rc 0, nothing published, and the last run's
# discovery is not reused (bt1 74a1c281..89d1e3a9 read 3,000,000 stale discovery calls and then FAILed)
mkdir -p "$T/home-nostart/boot_trace"; printf 'call\t1\t1\t0\tx\t0x401000\tf\n' > "$T/home-nostart/boot_trace/disc.tsv"
git -C "$T/origin.git" update-ref -d "refs/heads/trace/${C:0:12}"
run nostart FAKE_NOSTART=disc
ok "the stub boot did not start: rc 0, NO TRACE under the title naming emu_guest.py's last line" '[ "$(cat $T/nostart.rc)" = 0 ] && sed -n 3p $T/nostart.md | grep -q "^- NO TRACE: the stub boot of link ${C:0:12} did not start (emu_guest.py exit 1: --inject .*FUN_00c3bcf0Eb is not in the guest" && ! grep -q "^- FAIL" $T/nostart.md'
ok "... the stale discovery not used, no reference run, nothing published" '! grep -q "| discovery | discovery: 1 calls" $T/nostart.md && ! grep -q "| reference" $T/nostart.md && ! git -C $T/origin.git rev-parse -q --verify "trace/${C:0:12}" > /dev/null'
run nostartg FAKE_NOSTART=guest
ok "the guest boot did not start: NO TRACE, rc 0" '[ "$(cat $T/nostartg.rc)" = 0 ] && grep -q "^- NO TRACE: the stub boot of link" $T/nostartg.md'
# TRACE_SITES (rawvt-wine): the Wine pass only, on S1's site list; the report published to reports/rawvt-wine/<c12>.tsv
mkdir -p "$Q/tools/lockstep"
cat > "$Q/tools/lockstep/rawvt_sites.py" <<'PY2'
import sys
if sys.argv[1] == "list":
    open(sys.argv[4], "w").write("@00496800 edx s0\n"); open(sys.argv[5], "w").write("00496800\tedx\t0\tslot load\n"); print("1 sites for 1 rows")
else:
    open(sys.argv[6], "w").write("# rawvt-wine\nsrc/a.cpp\t004965d0\t121\t-\t00496800\tedx\tslot load\tsym\t3\t0x10a0000=.?AVX@@\n"); print("1 sites hit, 0 never reached")
PY2
( cd "$T/art" && git checkout -q --orphan s1s && git rm -rqf . 2> /dev/null; printf '# unit\nsrc/a.cpp\t004965d0\tthis\t121\t-\t00496800\tedx\tsym\n' > rawvt_wine_sites.tsv &&
  git add -A && git -c user.name=t -c user.email=t@t commit -qm s && git push -q origin HEAD:refs/heads/recon/s1_rawvt_sites ) > /dev/null 2>&1
cat > "$Q/tools/lockstep/wine_trace.sh" <<'SH'
#!/usr/bin/env bash
echo "list=$(cat "$3") sitemax=${FNVTRACE_SITEMAX:-unset}" > "$4.args"; printf 'site\t1\t1\t00496800\ts0\tedx\t0x10a0000\t0x401000\n' > "$4"; echo "game ended"
SH
run sites TRACE_SITES=recon/s1_rawvt_sites:rawvt_wine_sites.tsv
ok "TRACE_SITES: the Wine pass on the site lines only (no discovery, no guest), FNVTRACE_SITEMAX 8" '[ "$(cat $T/sites.rc)" = 0 ] && grep -q "^list=@00496800 edx s0 sitemax=8" $T/home-sites/boot_trace/sites-wine.tsv.args && ! grep -q "| discovery\|| guest" $T/sites.md'
ok "TRACE_SITES: the report published to reports/rawvt-wine/<c12>.tsv, said" 'git -C "$T/origin.git" show "reports/rawvt-wine:${C:0:12}.tsv" | grep -q "0x10a0000=.?AVX@@" && grep -q "published reports/rawvt-wine/${C:0:12}.tsv" $T/sites.md'
python3 -I "$P/tests/s5/boot_trace.py" compare "$T/none.tsv" "$T/none2.tsv" > "$T/real.out" 2>&1; rrc=$?
ok "boot_trace.py compare, missing guest trace: exit 2, an error line, no traceback" '[ $rrc = 2 ] && grep -q "no guest trace" $T/real.out && ! grep -q Traceback $T/real.out'
[ $fails = 0 ] && echo "test_boot_trace_job: all ok" || { for f in "$T"/*.log; do echo "== $f"; tail -5 "$f"; done; echo "test_boot_trace_job: $fails failed"; exit 1; }
