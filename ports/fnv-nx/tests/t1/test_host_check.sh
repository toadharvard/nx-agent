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
# the plugin headers come with gcc-13-plugin-dev-aarch64-linux-gnu (NEED_APT)
case "$*" in *gcc-13-plugin-dev-aarch64-linux-gnu*) mkdir -p "$T_DIR/plugin/include"; touch "$T_DIR/plugin/include/gcc-plugin.h" ;; esac
SH
# stand-in cross compiler: its plugin directory is $T/plugin (headers there only once apt installed them)
printf '#!/bin/bash\n[ "$1" = -print-file-name=plugin ] && echo "$T_DIR/plugin"\nexit 0\n' > "$T/bin/aarch64-linux-gnu-g++"
chmod +x "$T/bin/python3" "$T/bin/apt-get" "$T/bin/aarch64-linux-gnu-g++"
hc() {   # hc <name> [VAR=value ...]: host_check $LINK_PY in a subshell; rc in $T/<name>.rc, output in $T/<name>.out
	local n=$1; shift
	rm -f "$T/installed"; : > "$T/log"; rm -rf "$T/plugin"; [ -n "${HAVE_PLUGIN:-}" ] && { mkdir -p "$T/plugin/include"; touch "$T/plugin/include/gcc-plugin.h"; }
	( export PATH=$T/bin:$PATH T_LOG=$T/log T_DIR=$T "$@"; . "$P/tools/farm/lib_job.sh"; host_check git $LINK_PY ) > "$T/$n.out" 2>&1
	echo $? > "$T/$n.rc"
}
HAVE_PLUGIN=1 hc apt FARM_APT=1
ok "FARM_APT=1: capstone missing -> pip installs the pinned set, no apt, then it is there (rc 0)" '[ "$(cat $T/apt.rc)" = 0 ] && grep -q "pip -m pip install .*capstone==5.0.9 unicorn==2.1.4 pyelftools==0.33" $T/log && ! grep -q "^apt-get" $T/log'
hc plugin FARM_APT=1
ok "the GCC plugin headers missing (sw14: compile.sh exit 2 at 0 s): apt installs gcc-13-plugin-dev, rc 0" '[ "$(cat $T/plugin.rc)" = 0 ] && grep -q "^apt-get install .*gcc-13-plugin-dev-aarch64-linux-gnu" $T/log'
HAVE_PLUGIN=1 hc broken FARM_APT=1 FAKE_PIP_BROKEN=1
ok "a pip that installs nothing: exit 1 at setup, naming the module" '[ "$(cat $T/broken.rc)" = 1 ] && grep -q "still missing after installing: py:capstone" $T/broken.out'
hc noapt FARM_APT=0
ok "FARM_APT=0: exit 1, naming them" '[ "$(cat $T/noapt.rc)" = 1 ] && grep -q "missing on this host: py:capstone gccplugin" $T/noapt.out'
ok "the link jobs check capstone" 'for j in full_job bootloop_job link_job race_job; do grep -q "^host_check .*\$LINK_PY" "$P/tools/farm/$j.sh" || exit 1; done; grep -q "LINK_PY=\"py:capstone" "$P/tools/farm/lib_job.sh"'
# usable_cross: the toolchain's cross_bin only when its g++ has the plugin headers (sw14: the pinned cross root has none)
mkdir -p "$T/xc/bin" "$T/xc/plugin/include"
printf '#!/bin/bash\n[ "$1" = -print-file-name=plugin ] && echo "%s/xc/plugin"\nexit 0\n' "$T" > "$T/xc/bin/aarch64-linux-gnu-g++"; chmod +x "$T/xc/bin/aarch64-linux-gnu-g++"
uc() { ( . "$P/tools/farm/lib_job.sh"; usable_cross "$1" ) 2> "$T/uc.err"; }
ok "usable_cross: no plugin headers -> nothing (the system cross tools), said on stderr" '[ -z "$(uc "$T/xc/bin")" ] && grep -q "no GCC plugin headers" $T/uc.err'
touch "$T/xc/plugin/include/gcc-plugin.h"
ok "usable_cross: with the headers -> the toolchain's cross_bin; none given -> nothing" '[ "$(uc "$T/xc/bin")" = "$T/xc/bin" ] && [ -z "$(uc "")" ]'
ok "the link jobs pass CROSS_BIN only through usable_cross" '! grep -n "CROSS_BIN=\"\$(art cross_bin)\"" "$P"/tools/farm/*_job.sh'
[ $fails = 0 ] && echo "test_host_check: all ok" || { cat "$T"/*.out; echo "test_host_check: $fails failed"; exit 1; }
