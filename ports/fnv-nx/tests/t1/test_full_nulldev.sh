#!/usr/bin/env bash
# T1: full_job.sh step 4b, the stub-fault boot, runs with FNVNX_STUB_NULLDEV=1 (S5 a3b368ed: CreateDevice on the null
# device, so the frontier goes past the renderer) and records it in boot/stub/nulldev; FULL_STUB_NULLDEV=0 runs it
# without. The raw boot never gets it: full_job unsets it before anything runs, even when the farm exports it.
# full_report.py names it in the stub boot's heading; ratchet.py keeps the stub boot's milestones with and without
# it as separate metrics. A stand-in farm_boot.sh records its environment.   bash tests/t1/test_full_nulldev.sh
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
sed -n '/^# step-4b-begin$/,/^# step-4b-end$/p' "$P/tools/farm/full_job.sh" >"$T/step.sh"
ok "step 4b found in full_job.sh" '[ "$(wc -l <"$T/step.sh")" -gt 8 ]'
mkdir -p "$T/p/tests/s5"
cat >"$T/p/tests/s5/farm_boot.sh" <<'SH'
#!/usr/bin/env bash
echo "${FNVNX_STUB_NULLDEV-unset}" > "$T_ENV"
SH
step() {   # step [VAR=value ...]: the block as full_job runs it, on a synthetic build directory
	rm -rf "$T/f"; mkdir -p "$T/f/build/guest" "$T/f/build/boot"; echo nso > "$T/f/build/guest/guest.nso"
	( set -euo pipefail; export "$@" T_ENV=$T/env; F=$T/f P=$T/p COMMIT=c0ffee0123456789 C12=c0ffee012345 INPUTS=1 TREE=""
	  log() { :; }; stage_ok() { return 0; }
	  . "$T/step.sh" ) > "$T/out" 2>&1
}
step
ok "the stub boot runs with FNVNX_STUB_NULLDEV=1, recorded" '[ "$(cat "$T/env")" = 1 ] && [ "$(cat "$T/f/build/boot/stub/nulldev")" = 1 ]'
step FULL_STUB_NULLDEV=0
ok "FULL_STUB_NULLDEV=0: without it, recorded" '[ "$(cat "$T/env")" = 0 ] && [ "$(cat "$T/f/build/boot/stub/nulldev")" = 0 ]'
ok "the raw boot never: unset before the job does anything" 'awk "/^set -euo pipefail/{s=1} s&&/^unset FNVNX_STUB_NULLDEV/{print NR; exit}" "$P/tools/farm/full_job.sh" | grep -q . && [ "$(grep -n "^unset FNVNX_STUB_NULLDEV" "$P/tools/farm/full_job.sh" | cut -d: -f1)" -lt "$(grep -n "make -C" "$P/tools/farm/full_job.sh" | head -1 | cut -d: -f1)" ]'
ok "and full_job sets it nowhere else" '[ "$(grep -v "^[[:space:]]*#" "$P/tools/farm/full_job.sh" | grep -c "FNVNX_STUB_NULLDEV=")" = 1 ]'
# full_report.py's heading and the ratchet's metrics
python3 -I - "$P" <<'PY' > "$T/py.out" 2>&1
import os, sys, tempfile
P = sys.argv[1]
sys.path.insert(0, os.path.join(P, 'tools', 'farm'))
import full_report, ratchet
d = tempfile.mkdtemp()
os.makedirs(os.path.join(d, 'boot', 'stub'))
open(os.path.join(d, 'boot', 'stub', 'emu_stub.log'), 'w').write('fnvnx boot: calling WinMain\nSTOPPED: x\n')
heads = {}
for v in ('1', '0'):
    open(os.path.join(d, 'boot', 'stub', 'nulldev'), 'w').write(v + '\n')
    heads[v] = [l for l in full_report.stub_boot(d, lambda s, n=200: s) if l.startswith('Stub-fault boot')][0]
print('HEAD1', heads['1']); print('HEAD0', heads['0'])
assert 'null device: FNVNX_STUB_NULLDEV=1' in heads['1'] and 'null device: off' in heads['0'], heads
md = lambda h: ('## Emulated boot\n\n- WinMain entered: yes; initializers skipped after a fault: 5\n\n'
                + h + '\n\n- WinMain entered: yes; initializers skipped after a fault: 2\n'
                '- game milestones: winmain yes, renderer-init yes; last reached: renderer-init\n')
n1 = {m.name for m in ratchet.x_full(md(heads['1']), None)}
n0 = {m.name for m in ratchet.x_full(md(heads['0']), None)}
print(sorted(n1)); print(sorted(n0))
assert 'full: stub boot (nulldev) milestones' in n1 and 'full: stub boot milestones' not in n1
assert 'full: stub boot milestones' in n0 and not any('nulldev' in n for n in n0)
assert 'full: boot initializers skipped' in n1 and 'full: boot initializers skipped' in n0
print('py ok')
PY
ok "full_report names the null device; the ratchet keeps separate stub-boot metrics" 'grep -q "^py ok" "$T/py.out"'
[ $fails = 0 ] && echo "test_full_nulldev: all ok" || { cat "$T/out" "$T/py.out"; echo "test_full_nulldev: $fails failed"; exit 1; }
