#!/usr/bin/env bash
# full_job.sh - farm job: the whole pipeline (`make BOOT=1`: prepare, census, compile, link, guest, emulated boot,
# host, package, tests) on one commit of this repo, from nothing but the repository.
#
#   tools/farm/full_job.sh <commit> <report.md>
#
# Work trees live under $FARM_HOME (default ~/fnvnx-farm), never inside the repository: the commit's tools as a
# detached worktree src-<commit>, the pinned checkouts in ext/ (tools/fetch.sh), the toolchain in
# toolchain-<id> (shared by every commit with the same toolchain scripts and pins: the pinned Ubuntu AArch64
# cross packages, toolchain/cross/setup.sh; fnvnx-a64 + DEVKITPRO with libnx, ~1 h on 4 cores the first time;
# Ogg Vorbis), the build in full-<commit>/build. The FARM_KEEP newest builds (default 2) are kept, older ones and
# their worktrees removed. The demangle cache of the newest earlier build seeds the link (LINK_SEED).
#
# The report holds the provenance, the stage table, compile / link counts, undefined names by class, the boot
# (milestones, first fault, skipped initializers by fault), the boot frontier's first 20 entries and the package
# check: numbers, unit names and short diagnostics only (tools/farm/full_report.py). The job exits 0 once the
# report is written; the report states make's exit code.
#
# Needs (Ubuntu 24.04): git python3 make curl jq dpkg-deb gcc g++ llvm-18 (llvm-undname) qemu-user (tests),
# for fnvnx-a64: libgmp-dev libmpfr-dev libmpc-dev texinfo bison flex; python: unicorn pyelftools.
# FARM_APT=1 installs the missing ones with apt-get / pip (as root). The AArch64 cross compiler needs no package.
#
# Optional: PRISTINE_OBJS=<dir> (S11's MSVC 2008 objects of the pristine decomp, tools/equiv) gives the boot S14's
# synthesized data image, else it runs on the all-zero image; JOBS (default nproc); TESTS (default all);
# FARM_SRC=<checkout at <commit>> instead of a new worktree; FARM_MAKE_ARGS (extra make variables, e.g.
# "UNITS=<list>" for a quick check of the job itself); FNVNX_TC / DEVKITPRO / VORBIS to reuse existing ones.
set -euo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
REPO=$(cd "$(dirname "$0")/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
JOBS=${JOBS:-$(nproc)}
EXT=$FARM_HOME/ext
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
t0=$(date +%s)

# 0. Host packages (the toolchain build's own only when it has to build fnvnx-a64, see step 3).
NEED_APT="git python3 python3-pip make curl jq dpkg gcc g++ llvm-18 qemu-user libgmp-dev libmpfr-dev libmpc-dev texinfo bison flex"
host_check() {   # commands (py:<module> for python modules) -> installed with apt-get / pip when FARM_APT=1
	local missing="" c
	for c in "$@"; do
		case "$c" in
			llvm-undname) command -v llvm-undname >/dev/null || command -v llvm-undname-18 >/dev/null || missing="$missing $c" ;;
			py:*) python3 -I -c "import ${c#py:}" 2>/dev/null || missing="$missing $c" ;;
			*) command -v "$c" >/dev/null || missing="$missing $c" ;;
		esac
	done
	[ -z "$missing" ] && return 0
	if [ "${FARM_APT:-0}" = 1 ]; then
		apt-get update -qq
		# shellcheck disable=SC2086
		DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $NEED_APT
		python3 -m pip install -q --break-system-packages unicorn==2.1.4 pyelftools==0.33
		return 0
	fi
	echo "full_job.sh: missing on this host:$missing (FARM_APT=1 installs: $NEED_APT; pip unicorn pyelftools)"
	exit 1
}
host_check git python3 make curl jq dpkg-deb gcc g++ llvm-undname py:unicorn py:elftools

# 1. The port's tools at that commit, as a detached worktree outside the main checkout.
if [ -n "${FARM_SRC:-}" ]; then
	SRC=$(cd "$FARM_SRC" && pwd)
	[ "$(git -C "$SRC" rev-parse HEAD)" = "$(git -C "$SRC" rev-parse "$COMMIT^{commit}")" ] \
		|| { echo "FARM_SRC=$SRC is not at $COMMIT"; exit 1; }
else
	SRC=$FARM_HOME/src-${COMMIT:0:12}
	if [ ! -d "$SRC" ]; then git -C "$REPO" worktree add --detach "$SRC" "$COMMIT" >/dev/null; fi
fi
P=$SRC/ports/fnv-nx
COMMIT=$(git -C "$SRC" rev-parse HEAD)

# 2. Pinned third-party checkouts.
bash "$P/tools/fetch.sh" "$EXT" >/dev/null

# 3. The toolchain, shared by the commits that build it the same way.
TCID=$(cat "$P"/toolchain/sources.sh "$P"/toolchain/gcc/build.sh "$P"/toolchain/devkita64/* "$P"/toolchain/cross/* \
	"$P"/toolchain/vorbis/build.sh "$P"/tools/fetch.sh | sha256sum | cut -c1-12)
TCB=$FARM_HOME/toolchain-$TCID
mkdir -p "$TCB"
if [ -z "${FNVNX_TC:-}${DEVKITPRO:-}" ] && [ ! -f "$TCB/toolchain/prefix/.fnvnx-build-id" ]; then
	host_check makeinfo bison flex     # toolchain/gcc/build.sh (its configure checks the gmp/mpfr/mpc headers)
fi
make -C "$P" fetch toolchain BUILD_DIR="$TCB" EXT="$EXT" JOBS="$JOBS" TESTS=none > "$TCB/make.log" 2>&1 || true
art() { python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r.get("artifacts",{}).get(sys.argv[2]) or "")' \
	"$TCB/toolchain/stage.json" "$1"; }
TC_STATUS=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("status"))' "$TCB/toolchain/stage.json" 2>/dev/null || echo none)
case "$TC_STATUS" in ok|warn) ;; *) echo "toolchain stage $TC_STATUS, see $TCB/make.log"; tail -5 "$TCB/make.log"; exit 1 ;; esac
TC_ARGS=(FNVNX_TC="$(art prefix)" DEVKITPRO="$(art devkitpro)")
[ -n "$(art cross_bin)" ] && TC_ARGS+=(CROSS_BIN="$(art cross_bin)")
[ -n "$(art vorbis)" ] && TC_ARGS+=(VORBIS="$(art vorbis)") || TC_ARGS+=(VORBIS=none)

# 4. The build.
B=$FARM_HOME/full-${COMMIT:0:12}
mkdir -p "$B"
SEED=$(ls -1dt "$FARM_HOME"/full-*/build/link/out 2>/dev/null | grep -v "^$B/" | head -1 || true)
rc=0
# shellcheck disable=SC2086
make -C "$P" BOOT=1 BUILD_DIR="$B/build" EXT="$EXT" JOBS="$JOBS" TESTS="${TESTS:-all}" "${TC_ARGS[@]}" \
	${SEED:+LINK_SEED="$SEED"} ${PRISTINE_OBJS:+PRISTINE_OBJS="$PRISTINE_OBJS"} ${FARM_MAKE_ARGS:-} \
	> "$B/make.log" 2>&1 || rc=$?
t1=$(date +%s)

# 5. Report.
python3 -I "$P/tools/farm/full_report.py" "$B/build" "$REPORT" --commit "$COMMIT" --make-rc "$rc" \
	--seconds $((t1 - t0)) --hide FARM_HOME="$FARM_HOME" SRC="$SRC" > /dev/null

# 6. Keep the FARM_KEEP newest builds.
ls -1dt "$FARM_HOME"/full-* 2>/dev/null | tail -n +$((${FARM_KEEP:-2} + 1)) | while read -r old; do
	c=${old##*/full-}
	rm -rf "$old"
	if [ -d "$FARM_HOME/src-$c" ]; then git -C "$REPO" worktree remove --force "$FARM_HOME/src-$c" || true; fi
done
echo "$REPORT"
