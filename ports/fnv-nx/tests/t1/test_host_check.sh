#!/usr/bin/env bash
# T1: lib_job.sh host_check installs the pinned Python modules the link imports and checks again, so a host that
# lacks one fails at setup (bl1 bea5a2ed failed at the link stage after 72 min: No module named 'capstone' in
# tools/link/slotfill2.py, which no setup installed). A stand-in python3 whose capstone appears once pip ran:
# FARM_APT=1 installs it (pip, not apt) and returns 0; a pip that installs nothing exits 1 "still missing"; FARM_APT=0
# exits 1 naming it. full_job, bootloop, link_job and race_job check $LINK_PY (capstone among them).
set -uo pipefail
P=$(cd "$(dirname "$0")/../.." && pwd); T=${KEEP_T:-$(mktemp -d)}; [ -n "${KEEP_T:-}" ] || trap 'rm -rf "$T"' EXIT
fails=0; ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }
mkdir -p "$T/bin"
cat > "$T/bin/python3" <<'SH'
#!/bin/bash
# stand-in: `-m pip install ...` records the call (and installs, unless FAKE_PIP_BROKEN); `-I -c "import X"` succeeds
# for X != capstone, and for capstone once installed
if [ "$1" = -m ] && [ "$2" = pip ]; then echo "pip $*" >> "$T_LOG"; [ -n "${FAKE_PIP_BROKEN:-}" ] || touch "$T_DIR/installed"; exit 0; fi
if [ "$1" = -I ] && [ "$2" = -c ]; then
	case "$3" in *capstone*) [ -f "$T_DIR/installed" ] && exit 0; exit 1 ;; *) exit 0 ;; esac
fi
exit 0
SH
cat > "$T/bin/apt-get" <<'SH'
#!/bin/bash
echo "apt-get $*" >> "$T_LOG"
SH
chmod +x "$T/bin/python3" "$T/bin/apt-get"
hc() {   # hc <name> [VAR=value ...]: host_check $LINK_PY in a subshell; rc in $T/<name>.rc, output in $T/<name>.out
	local n=$1; shift
	rm -f "$T/installed"; : > "$T/log"
	( export PATH=$T/bin:$PATH T_LOG=$T/log T_DIR=$T "$@"; . "$P/tools/farm/lib_job.sh"; host_check git $LINK_PY ) > "$T/$n.out" 2>&1
	echo $? > "$T/$n.rc"
}
hc apt FARM_APT=1
ok "FARM_APT=1: capstone missing -> pip installs the pinned set, no apt, then it is there (rc 0)" '[ "$(cat $T/apt.rc)" = 0 ] && grep -q "pip -m pip install .*capstone==5.0.9 unicorn==2.1.4 pyelftools==0.33" $T/log && ! grep -q "^apt-get" $T/log'
hc broken FARM_APT=1 FAKE_PIP_BROKEN=1
ok "a pip that installs nothing: exit 1 at setup, naming the module" '[ "$(cat $T/broken.rc)" = 1 ] && grep -q "still missing after installing: py:capstone" $T/broken.out'
hc noapt FARM_APT=0
ok "FARM_APT=0: exit 1, naming it" '[ "$(cat $T/noapt.rc)" = 1 ] && grep -q "missing on this host: py:capstone" $T/noapt.out'
ok "the link jobs check capstone" 'for j in full_job bootloop_job link_job race_job; do grep -q "^host_check .*\$LINK_PY" "$P/tools/farm/$j.sh" || exit 1; done; grep -q "LINK_PY=\"py:capstone" "$P/tools/farm/lib_job.sh"'
[ $fails = 0 ] && echo "test_host_check: all ok" || { cat "$T"/*.out; echo "test_host_check: $fails failed"; exit 1; }
