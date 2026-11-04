#!/usr/bin/env bash
# full_job.sh - farm job: link_job.sh, then the rest of `make BOOT=1` on that link: the ILP32 guest, S5's emulated
# boot with the boot frontier, the host module and the package check; the link outputs are published again with the
# guest ELF and the boot reports next to them.
#
#   tools/farm/full_job.sh <commit> <report.md>          (farm machines: tools/farm/runner.sh <name> full)
#
# Everything link_job.sh takes (tools, pinned checkouts, the prepare farm's tree, the compile farms' objects; its
# variables apply), plus, under $FARM_HOME:
#   toolchain  toolchain-<id>, shared by every commit with the same toolchain scripts and pins: the pinned Ubuntu
#              AArch64 cross packages (toolchain/cross/setup.sh), fnvnx-a64 + DEVKITPRO with libnx (~1 h on 4 cores
#              the first time, resumable), Ogg Vorbis (`make toolchain` in that directory; FNVNX_TC / DEVKITPRO /
#              VORBIS reuse existing ones)
#   pristine   S11's MSVC 2008 objects of the pristine decomp from branch msvc/pristine/<decomp12>, for the boot's
#              synthesized data image (PRISTINE_OBJS=<dir> instead); without them the boot uses the zero image
# The build directory full/build persists across commits. The host gates are not run (TESTS=none; TESTS=all runs
# them).
#
# Outputs: <report.md> (tools/farm/full_report.py: provenance, stage table, link counts, undefined names by class,
# boot reached and first fault, skipped initializers by fault, frontier top 20, package check; numbers, unit names
# and short diagnostics only), and link/<commit12> (link_job.sh's files + guest/fnvnx.elf,
# boot/{summary.txt,frontier.txt,frontier.tsv,emu.log} of the raw boot, boot/stub/{summary.txt,emu_stub.log,
# frontier_stub.txt,frontier_stub.tsv} of the stub-fault boot, boot/synth.exe + boot/synth_summary.txt: the data image
# both booted on, its sha256 in meta.json). Exit 0 once the report is written.
#
# Needs: link_job.sh's, plus g++; for fnvnx-a64: libgmp-dev libmpfr-dev libmpc-dev texinfo bison flex; python
# unicorn; for TESTS=all also qemu-user, cmake, meson, ninja-build. FARM_APT=1 installs them (root).
set -euo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
JOBS=${JOBS:-$(nproc)}
if [ -z "${ARTIFACTS:-}" ]; then
	if [ -d /home/user/fnv-nx-build/.git ]; then ARTIFACTS=/home/user/fnv-nx-build; else ARTIFACTS=$FARM_HOME/artifacts; fi
fi
export ARTIFACTS FARM_HOME JOBS
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
# shellcheck source=lib_job.sh
. "$HERE/lib_job.sh"
t0=$(date +%s)

host_check git python3 make curl jq rsync dpkg-deb gcc g++ llvm-undname py:unicorn py:elftools
job_sources "$REPO"
F=$FARM_HOME/full; mkdir -p "$F"

# 1. The toolchain, shared by the commits that build it the same way.
TCID=$(cat "$P"/toolchain/sources.sh "$P"/toolchain/gcc/build.sh "$P"/toolchain/devkita64/* "$P"/toolchain/cross/* \
	"$P"/toolchain/vorbis/build.sh "$P"/tools/fetch.sh | sha256sum | cut -c1-12)
TCB=$FARM_HOME/toolchain-$TCID
mkdir -p "$TCB"
if [ -z "${FNVNX_TC:-}${DEVKITPRO:-}" ] && [ ! -f "$TCB/toolchain/prefix/.fnvnx-build-id" ]; then
	host_check makeinfo bison flex     # toolchain/gcc/build.sh (its configure checks the gmp/mpfr/mpc headers)
fi
log "toolchain $TCB"
make -C "$P" fetch toolchain BUILD_DIR="$TCB" EXT="$EXT" JOBS="$JOBS" TESTS=none > "$TCB/make.log" 2>&1 || true
art() { python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r.get("artifacts",{}).get(sys.argv[2]) or "")' \
	"$TCB/toolchain/stage.json" "$1"; }
stage_ok "$TCB" toolchain || { echo "toolchain stage failed, see $TCB/make.log"; tail -5 "$TCB/make.log"; exit 1; }
ARGS=(FNVNX_TC="$(art prefix)" DEVKITPRO="$(art devkitpro)")
[ -n "$(art cross_bin)" ] && ARGS+=(CROSS_BIN="$(art cross_bin)")
if [ -n "$(art vorbis)" ]; then ARGS+=(VORBIS="$(art vorbis)"); else ARGS+=(VORBIS=none); fi

# 2. The link (link_job.sh in this job's build directory; it publishes link/<commit12> already).
# the same cross tools as the steps after it, so the link is not redone for another compiler path
CROSS_BIN="$(art cross_bin)" LINK_BUILD=$F/build FARM_SRC=$SRC FARM_EXT=$EXT \
	bash "$HERE/link_job.sh" "$COMMIT" "$F/link-$C12.md" > "$F/link_job.log" 2>&1 \
	|| log "link_job.sh exited $?"
# shellcheck disable=SC1090
. "$F/inputs-$C12.env"
[ -n "$TREE" ] && ARGS+=(PREPARED="$TREE")
[ -n "$OBJS" ] && ARGS+=(OBJECTS="$OBJS")

# 3. Pristine MSVC objects for the synthesized boot image (link_job.sh took them for resolve --msvc-code too).
job_pristine
[ -n "$PR" ] && ARGS+=(PRISTINE_OBJS="$PR" MSVC_CODE="$PR")
log "tree: $NOTE_TREE; objects: $NOTE_OBJS; $NOTE_PR"

# 4. Guest, emulated boot, host, package on that link (the link stage stays cached: same inputs).
rc=0
if [ -n "$TREE" ] && [ -n "$OBJS" ]; then
	# shellcheck disable=SC2086
	make -C "$P" BOOT=1 BUILD_DIR="$F/build" EXT="$EXT" JOBS="$JOBS" TESTS="${TESTS:-none}" "${ARGS[@]}" \
		${FARM_MAKE_ARGS:-} > "$F/make-$C12.log" 2>&1 || rc=$?
	ls -1t "$F"/make-*.log | tail -n +6 | xargs -r rm -f
else
	rc=2
fi

# 4b. S5's stub-fault boot: the same guest on the same data image with tests/s5/known_faults.txt, which runs past the
# known faults (docs/TOOLCHAIN.md section 18), so the frontier beyond them is published next to the raw boot's
# (boot/stub/). tests/s5/farm_boot.sh FARM_BOOTS=stub on the pipeline's build directory; under a minute.
STUB=$F/build/boot/stub
rm -rf "$STUB"
if [ $rc -ne 2 ] && stage_ok "$F/build" boot && [ -f "$F/build/guest/guest.nso" ]; then
	log "stub-fault boot (tests/s5/farm_boot.sh)"
	IMG=$F/build/boot/synth/synth.exe; [ -f "$IMG" ] || IMG=""
	FARM_LINK_DIR=$F/build/link FARM_GUEST_DIR=$F/build/guest FARM_BOOTS=stub \
		FARM_UNITS="${TREE:+$TREE/build/units.json}" PY="${PY:-python3}" EMU_TIMEOUT="${EMU_TIMEOUT:-3000}" \
		bash "$P/tests/s5/farm_boot.sh" "$COMMIT" "$STUB" $IMG > "$F/stub_boot-$C12.log" 2>&1 \
		|| log "farm_boot.sh (stub boot) exited $?"
	ls -1t "$F"/stub_boot-*.log | tail -n +6 | xargs -r rm -f
fi
# 4c. S5's work list of hand-stored image vptrs (tests/s5/hand_vptrs.py): each vptr store of an image vftable
# address in the prepared tree, how this link binds it and the rule that fixes it, boot-hit addresses first;
# published as boot/hand_vptrs*.tsv. About a minute.
rm -f "$F/build/boot/hand_vptrs.tsv" "$F/build/boot/hand_vptrs_sites.tsv"
if [ -n "$TREE" ] && [ -f "$F/build/link/out/aliases.ld" ] && [ -d "$F/build/boot" ]; then
	HV=(--tree "$TREE" --link "$F/build/link" --out "$F/build/boot/hand_vptrs")
	[ -f "$F/build/guest/fnvnx.elf" ] && HV+=(--elf "$F/build/guest/fnvnx.elf")
	for l in "$F/build/boot/emu.log" "$STUB/emu_stub.log"; do [ -f "$l" ] && HV+=(--boot "$l"); done
	"${PY:-python3}" -I "$P/tests/s5/hand_vptrs.py" "${HV[@]}" > "$F/build/boot/hand_vptrs.txt" 2>&1 \
		|| log "hand_vptrs.py exited $?"
fi
t1=$(date +%s)

# 5. Report, then the link outputs again with the guest ELF and the boot reports.
python3 -I "$P/tools/farm/full_report.py" "$F/build" "$REPORT" --commit "$COMMIT" --make-rc "$rc" \
	--seconds $((t1 - t0)) --inputs "tree: $NOTE_TREE" "objects: $NOTE_OBJS" \
	"pristine MSVC objects: ${PR:+branch msvc/pristine/$D12}${PR:-none (zero data image)}" \
	--hide FARM_HOME="$FARM_HOME" SRC="$SRC" EXT="$EXT" TOOLCHAIN="$TCB" ARTIFACTS="$ARTIFACTS" \
	${FNVNX_TC:+FNVNX_TC="$FNVNX_TC"} ${DEVKITPRO:+DEVKITPRO="$DEVKITPRO"} ${PRISTINE_OBJS:+PRISTINE_OBJS="$PRISTINE_OBJS"} \
	HOME="$HOME" > /dev/null
if [ $rc -ne 2 ]; then publish_link "$F/build"; else NOTE_PUB="not published (no tree or no objects)"; fi
echo "- link outputs: $NOTE_PUB" >> "$REPORT"
echo "$REPORT"
