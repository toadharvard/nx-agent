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
# both booted on, its sha256 in meta.json), v1/ (V1-link's audits of this link, step 4d; their table also in
# <report.md>). Exit 0 once the report is written.
#
# Needs: link_job.sh's, plus g++; for fnvnx-a64: libgmp-dev libmpfr-dev libmpc-dev texinfo bison flex; python
# unicorn; for TESTS=all also qemu-user, cmake, meson, ninja-build. FARM_APT=1 installs them (root).
set -euo pipefail
unset FNVNX_STUB_NULLDEV   # the stub boot (4b) sets it for itself only; never the raw boot or the product (S5 a3b368ed)
# the boots schedule as emu_guest.py does by default (S5 620572bc: threads switch at host calls, deterministic);
# no instruction count or timer slicing of ours, not even one the farm's environment carries (the timer slices
# of 2d1afaeb..6da559aa were nondeterministic). EMU_BUDGET: the raw boot's, tools/pipeline/build.py emu_slicing.
unset BUDGET SLICE_MS EMU_BUDGET EMU_SLICE_MS
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

host_check git python3 make curl jq rsync dpkg-deb gcc g++ llvm-undname $LINK_PY meson ninja flex bison   # meson..bison: deko3d (HOST_BACKEND=native); mako: uam (LINK_OVERRIDES=render)
# NX1's package checks (tests/nx1): switch-tools elf2nro needs lz4.h, the host device test qemu-user, the libnx
# syntax check newlib's headers. Libraries are not commands, so host_check cannot see them.
# uam (toolchain/uam/build.sh, LINK_OVERRIDES=render without RENDER_LIB) needs the system python's mako.
if [ "${FARM_APT:-0}" = 1 ] && { [ ! -f /usr/include/lz4.h ] || [ ! -d /usr/include/newlib ] || ! command -v qemu-aarch64 >/dev/null \
		|| ! /usr/bin/python3 -c 'import mako' 2>/dev/null; }; then
	apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends liblz4-dev libnewlib-dev qemu-user python3-mako \
		|| echo "full_job.sh: NX1 packages not installed; checks 1 and 7 report incomplete"
fi
# one worktree path for every head (lib_job.sh job_sources): the link's compile cache hits across heads
export FARM_STABLE_SRC=${FARM_STABLE_SRC:-$FARM_HOME/full-src}
job_sources "$REPO"
F=$FARM_HOME/full; mkdir -p "$F"
# the reconstructed bodies (D20, branches recon/*) for S1-refs' rule REC1, exported for every prepare this job runs
# (link_job.sh and make's prepare stage when no farm tree is adopted); none is not an error
# shellcheck source=recon.sh
. "$HERE/recon.sh"
fetch_recon "$ARTIFACTS" "$F/recon"
log "recon: $RECON_NOTE"

# 1. The toolchain, shared by the commits that build it the same way.
TCID=$(cat "$P"/toolchain/sources.sh "$P"/toolchain/gcc/build.sh "$P"/toolchain/devkita64/* "$P"/toolchain/cross/* \
	"$P"/toolchain/vorbis/build.sh "$P"/tools/fetch.sh | sha256sum | cut -c1-12)
TCB=$FARM_HOME/toolchain-$TCID
mkdir -p "$TCB"
if [ -z "${FNVNX_TC:-}${DEVKITPRO:-}" ] && [ ! -f "$TCB/toolchain/prefix/.fnvnx-build-id" ]; then
	host_check makeinfo bison flex     # toolchain/gcc/build.sh (its configure checks the gmp/mpfr/mpc headers)
fi
log "toolchain $TCB"
# T1: a fresh machine restores the built toolchain of this TCID from the artifacts repository (hostcache.sh) instead
# of building it (gcc ~45 min on 4 cores); the first machine to build it publishes it, in the background
TC_RESTORED=0
if [ ! -f "$TCB/toolchain/stage.json" ] && hostcache fetch toolchain "$TCID" "$TCB"; then TC_RESTORED=1; fi
make -C "$P" fetch toolchain BUILD_DIR="$TCB" EXT="$EXT" JOBS="$JOBS" TESTS=none > "$TCB/make.log" 2>&1 || true
if [ "$TC_RESTORED" = 0 ] && stage_ok "$TCB" toolchain; then
	hostcache_publish_bg toolchain "$TCID" "$TCB" --exclude toolchain/dl --exclude toolchain/src --exclude toolchain/work
fi
art() { python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r.get("artifacts",{}).get(sys.argv[2]) or "")' \
	"$TCB/toolchain/stage.json" "$1"; }
stage_ok "$TCB" toolchain || { echo "toolchain stage failed, see $TCB/make.log"; tail -5 "$TCB/make.log"; exit 1; }
ARGS=(FNVNX_TC="$(art prefix)" DEVKITPRO="$(art devkitpro)")
XCB=$(usable_cross "$(art cross_bin)"); [ -n "$XCB" ] && ARGS+=(CROSS_BIN="$XCB")
if [ -n "$(art vorbis)" ]; then ARGS+=(VORBIS="$(art vorbis)"); else ARGS+=(VORBIS=none); fi

# 2. The link (link_job.sh in this job's build directory; it publishes link/<commit12> already, without v1/: the
# previous commit's audits are removed first).
V1=$F/build/v1
rm -rf "$V1" "$F/v1-run" "$F/v1-report.md"
# the same cross tools as the steps after it, so the link is not redone for another compiler path
# Short waits for the prepare/compile farms' artifacts: when they are not there, this job prepares and compiles
# locally (step 4) instead of waiting up to 1.5 h for them
export FARM_TREE_WAIT=${FARM_TREE_WAIT:-300} FARM_OBJ_WAIT=${FARM_OBJ_WAIT:-300}
CROSS_BIN="$XCB" LINK_BUILD=$F/build FARM_SRC=$SRC FARM_EXT=$EXT \
	bash "$HERE/link_job.sh" "$COMMIT" "$F/link-$C12.md" > "$F/link_job.log" 2>&1 \
	|| log "link_job.sh exited $?"
# shellcheck disable=SC1090
. "$F/inputs-$C12.env"
[ -n "$TREE" ] && ARGS+=(PREPARED="$TREE")
[ -n "$OBJS" ] && ARGS+=(OBJECTS="$OBJS")

# 3. Pristine MSVC objects for the synthesized boot image (link_job.sh took them for resolve --msvc-code too).
job_pristine
[ -n "$PR" ] && ARGS+=(PRISTINE_OBJS="$PR" MSVC_CODE="$PR")
# the guest links the native renderer (S6n's overrides + libnxrender.a, built with this toolchain; RENDER_LIB=<a>
# takes a prebuilt one): without it the engine's D3D9 entry points have no device and the boot stops at renderer init
ARGS+=(LINK_OVERRIDES="${LINK_OVERRIDES:-render}")
[ -n "${RENDER_LIB:-}" ] && ARGS+=(RENDER_LIB="$RENDER_LIB")
# 3b. The player's exe, when the owner's copy is on the private branch gamedata/exe: the boots run on it instead of
# S14's synthesized image (J-0128)
job_gamedata
[ -n "$GAME_EXE" ] && { ARGS+=(BOOT_IMAGE="$GAME_EXE"); rm -rf "$F/build/boot/synth"; }   # no stale synth.exe in the publish
# the player's exe for every link tool that reads FNVNX_EXE (thisadj's secondary-base entries, exevft, bodies), as
# link_job.sh does: BOOT_IMAGE alone reached only resolve's --exe (full1 0540aff0: no __fnvnx_msvc_ binding at all)
if [ -n "$GAME_EXE" ]; then export FNVNX_EXE="$GAME_EXE"; else unset FNVNX_EXE; fi
log "tree: $NOTE_TREE; objects: $NOTE_OBJS; $NOTE_PR; boot image: $NOTE_EXE"

# 4. Guest, emulated boot, host, package on that link (the link stage stays cached: same inputs).
# INPUTS stays 1: without farm inputs the build prepares and compiles locally. Not rc=2: GNU make itself exits 2 whenever a stage fails (V1, J-0136), and such a
# build still gets its stub boot, audits and publish.
# Without the prepare farm's tree or the compile farms' objects of this commit, the build prepares and compiles
# locally at this head (make's own prepare and compile stages). It never reports the build directory's records of an
# older commit as this one's (the 097cdd1504c8 report showed 06857911's tree, objects and link as "cached").
rc=0; INPUTS=1
if [ -z "$TREE" ] || [ -z "$OBJS" ]; then
	log "no farm tree/objects for $C12: preparing and compiling locally at this head"
	NOTE_TREE="$NOTE_TREE; prepared locally at $C12"; NOTE_OBJS="$NOTE_OBJS; compiled locally at $C12"
	KEEP=(); for a in "${ARGS[@]}"; do case "$a" in PREPARED=*|OBJECTS=*) ;; *) KEEP+=("$a") ;; esac; done
	ARGS=("${KEEP[@]}")
fi
# the link's compile cache (T1, tools/link/objcache.py): $FARM_HOME/linkobj, seeded from the artifacts repository's
# linkobj/cache when this machine has (almost) none, so a fresh farm's first link is not cold (LINKCACHE=0: neither)
export FNVNX_LINK_OBJCACHE=${FNVNX_LINK_OBJCACHE:-$FARM_HOME/linkobj}
[ "${LINKCACHE:-1}" = 1 ] && { bash "$HERE/linkcache.sh" seed "$FNVNX_LINK_OBJCACHE" >> "$F/linkcache.log" 2>&1 || true; }
# T1: the game units' objects through the content-addressed cache shared by the farms (compile.sh
# FNVNX_UNIT_OBJCACHE; branch unitobj/cache): a unit another farm compiled with the same compiler, flags, source and
# headers at the same paths is taken instead of compiled (a flags change costs one farm's compile, not every farm's).
# Seeded every job (only the files this machine lacks), published after the build. UNITCACHE=0: neither.
if [ "${UNITCACHE:-1}" = 1 ]; then
	export FNVNX_UNIT_OBJCACHE=${FNVNX_UNIT_OBJCACHE:-$FARM_HOME/unitobj}
	LINKCACHE_BRANCH=unitobj/cache LINKCACHE_SEED_MIN=1000000000 bash "$HERE/linkcache.sh" seed "$FNVNX_UNIT_OBJCACHE" >> "$F/linkcache.log" 2>&1 || true
else unset FNVNX_UNIT_OBJCACHE; fi
# 3c. A local prepare publishes its tree right after prepare, not after the link (orchestrator: fastlink links a
# head only on a published tree with its prepare inputs and recon set, so S1's rule heads waited ~45 min for step 5c):
# `make prepare` alone first (the main build below finds it cached), then lib_job.sh publish_tree in the background
# while the build goes on (the tree is only read from now on); step 5c waits for it. FULL_EARLY_TREE=0: as before.
# step-3c-begin
TREEPUB_PID=""
if [ $INPUTS = 1 ] && [ -z "$TREE" ] && [ "${FULL_EARLY_TREE:-1}" = 1 ]; then
	# shellcheck disable=SC2086
	make -C "$P" prepare BUILD_DIR="$F/build" EXT="$EXT" JOBS="$JOBS" TESTS=none "${ARGS[@]}" ${FARM_MAKE_ARGS:-} \
		> "$F/make-prepare-$C12.log" 2>&1 || log "make prepare exited $? (the main build runs it again)"
	if stage_ok "$F/build" prepare; then
		( publish_tree "$F/build" || NOTE_TREEPUB="publish_tree exited $?"; echo "$NOTE_TREEPUB" > "$F/treepub-$C12.txt" ) \
			> "$F/treepub-$C12.log" 2>&1 &
		TREEPUB_PID=$!
		log "prepared tree: publishing t/$C12 in the background (pid $TREEPUB_PID)"
	fi
fi
# step-3c-end
# shellcheck disable=SC2086
make -C "$P" BOOT=1 BUILD_DIR="$F/build" EXT="$EXT" JOBS="$JOBS" TESTS="${TESTS:-none}" "${ARGS[@]}" \
	${FARM_MAKE_ARGS:-} > "$F/make-$C12.log" 2>&1 || rc=$?
ls -1t "$F"/make-*.log | tail -n +6 | xargs -r rm -f

# 4a. LINKEQ=1 (T1, the incremental link's farm check, once per farm): when this head's link took compiles from the
# link's compile cache (tools/link/objcache.py: link_objcache_*_hits > 0 in its stage.json), the link and guest are
# built again cold at the same paths (the link and guest directories set aside, an empty compile cache) and
# guest.nso and addrmap.tsv must have the same sha256 as the warm ones. The result goes to $F/linkeq.txt and the
# report; a pass writes $F/linkeq.done (not again), a FAIL keeps the warm directories as link.warm/guest.warm and
# the cold build in place (the cold one is the reference). About a cold link plus the guest (~1 h).
NOTE_LINKEQ=""
# step-4a-begin
if [ "${LINKEQ:-0}" = 1 ] && [ ! -f "$F/linkeq.done" ] && [ $INPUTS = 1 ] && [ -f "$F/build/guest/guest.nso" ] \
	&& [ -f "$F/build/link/out/addrmap.tsv" ]; then
	hits=$(python3 -I -c 'import json, sys
m = json.load(open(sys.argv[1])).get("metrics", {})
print(int(sum(v for k, v in m.items() if k.startswith("link_objcache_") and k.endswith("_hits"))))' \
		"$F/build/link/stage.json" 2>/dev/null || echo 0)
	if [ "${hits:-0}" -gt 0 ]; then
		log "LINKEQ: warm link took $hits compiles from the cache; linking again cold"
		warm=$(cd "$F/build" && sha256sum guest/guest.nso link/out/addrmap.tsv)
		rm -rf "$F/build/link.warm" "$F/build/guest.warm"
		mv "$F/build/link" "$F/build/link.warm"; mv "$F/build/guest" "$F/build/guest.warm"
		EQC=$F/linkeq-cache; rm -rf "$EQC"
		t_eq=$(date +%s)
		# shellcheck disable=SC2086
		FNVNX_LINK_OBJCACHE=$EQC make -C "$P" guest BUILD_DIR="$F/build" EXT="$EXT" JOBS="$JOBS" TESTS=none \
			"${ARGS[@]}" ${FARM_MAKE_ARGS:-} > "$F/linkeq-$C12.log" 2>&1 || true
		rm -rf "$EQC"
		cold=$(cd "$F/build" && sha256sum guest/guest.nso link/out/addrmap.tsv 2>/dev/null || echo "no cold outputs")
		{ echo "commit $COMMIT; warm link: $hits compiles from the cache; cold link + guest $(( $(date +%s) - t_eq )) s"
		  echo "warm:"; echo "$warm"; echo "cold:"; echo "$cold"; } > "$F/linkeq.txt"
		if [ "$warm" = "$cold" ]; then
			NOTE_LINKEQ="PASS: warm == cold (guest.nso, addrmap.tsv sha256; $hits cached compiles)"
			rm -rf "$F/build/link.warm" "$F/build/guest.warm"; cp "$F/linkeq.txt" "$F/linkeq.done"
		else
			NOTE_LINKEQ="FAIL: warm != cold (see linkeq.txt; link.warm and guest.warm kept)"
		fi
		echo "$NOTE_LINKEQ" >> "$F/linkeq.txt"; log "LINKEQ: $NOTE_LINKEQ"
	else
		NOTE_LINKEQ="not run: this link took nothing from the compile cache (a cold link); the next head checks"
	fi
fi
# step-4a-end
# the cache back to the artifacts repository after a link that compiled something new (LINKCACHE_PUBLISH=0: not;
# quick_job keeps it local like its other caches); unchanged caches are not pushed
if [ "${LINKCACHE:-1}" = 1 ] && [ "${LINKCACHE_PUBLISH:-1}" = 1 ] && [ -f "$F/build/link/out/aliases.ld" ]; then
	LINKCACHE_NOTE="full $C12" bash "$HERE/linkcache.sh" publish "$FNVNX_LINK_OBJCACHE" >> "$F/linkcache.log" 2>&1 || true
fi
# the units' cache back to the artifacts repository (only when it changed; never from a WIP build)
if [ -n "${FNVNX_UNIT_OBJCACHE:-}" ] && [ -z "${FARM_WIP_BRANCH:-}" ] && [ "${LINKCACHE_PUBLISH:-1}" = 1 ]; then
	LINKCACHE_BRANCH=unitobj/cache LINKCACHE_PUBLISH_MB=${UNITCACHE_PUBLISH_MB:-1500} LINKCACHE_NOTE="units full $C12" \
		bash "$HERE/linkcache.sh" publish "$FNVNX_UNIT_OBJCACHE" >> "$F/linkcache.log" 2>&1 || true
fi

# 4b. S5's stub-fault boot: the same guest on the same data image with tests/s5/known_faults.txt, which runs past the
# known faults (docs/TOOLCHAIN.md section 18), so the frontier beyond them is published next to the raw boot's
# (boot/stub/). tests/s5/farm_boot.sh FARM_BOOTS=stub on the pipeline's build directory; under a minute.
# step-4b-begin
STUB=$F/build/boot/stub
rm -rf "$STUB"
if [ $INPUTS = 1 ] && stage_ok "$F/build" boot && [ -f "$F/build/guest/guest.nso" ]; then
	log "stub-fault boot (tests/s5/farm_boot.sh)"
	IMG=${GAME_EXE:-$F/build/boot/synth/synth.exe}; [ -f "$IMG" ] || IMG=""
	# EMU_EXTRA=--plat (R4, recon4-013): the platform lane's data (unimplemented calls, failed opens, decode and input
	# failures) from every full job's boot path
	# FNVNX_STUB_NULLDEV=1 (S5 a3b368ed): CreateDevice succeeds on the null device in a logged measurement mode, so
	# the stub boot's frontier goes past the renderer. This boot only, never the raw boot or the product;
	# FULL_STUB_NULLDEV=0 runs it without. boot/stub/nulldev records the value (the report and the ratchet name it).
	NULLDEV=${FULL_STUB_NULLDEV:-1}; mkdir -p "$STUB"; echo "$NULLDEV" > "$STUB/nulldev"
	FNVNX_STUB_NULLDEV=$NULLDEV \
	FARM_LINK_DIR=$F/build/link FARM_GUEST_DIR=$F/build/guest FARM_BOOTS=stub EMU_EXTRA="${FULL_EMU_EXTRA:---plat}" \
		FARM_UNITS="${TREE:+$TREE/build/units.json}" PY="${PY:-python3}" EMU_TIMEOUT="${EMU_TIMEOUT:-3000}" \
		bash "$P/tests/s5/farm_boot.sh" "$COMMIT" "$STUB" $IMG > "$F/stub_boot-$C12.log" 2>&1 \
		|| log "farm_boot.sh (stub boot) exited $?"
	ls -1t "$F"/stub_boot-*.log | tail -n +6 | xargs -r rm -f
fi
# step-4b-end
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
# 4d. V1-link's audits of this link (tools/farm/v1_job.sh, D14/D17): on the link directory as built, with this job's
# tree, objects, pristine MSVC objects and pinned checkouts, nothing fetched; the detail tables go to build/v1/ and are
# published as link/<commit12>/v1/, the audit table into the report. About 6 minutes; V1_BUDGET seconds (default
# 1200) bound it, the slow audits (bridge runs, C5) are skipped once it is used up. FARM_V1=0 skips the step.
NOTE_V1="not run (no link outputs)"
if [ "${FARM_V1:-1}" = 0 ]; then NOTE_V1="not run (FARM_V1=0)"
elif [ $INPUTS = 1 ] && [ -f "$F/build/link/fixed/zz_bridges.o" ] && [ -f "$F/build/link/out/aliases.ld" ]; then
	log "V1-link audits (tools/farm/v1_job.sh)"
	XB=$(art cross_bin)   # the pinned ILP32 cross tools (the harness compiles its cases with them)
	V1_LINK_DIR=$F/build/link V1_TREE=$TREE V1_OBJ_DIR=$(dirname "$OBJS") V1_X86OBJ=${PR:-} V1_EXT=$EXT V1_NO_FETCH=1 \
		V1_PUBLISH=0 V1_OUT_DIR=$V1 V1_RUN=$F/v1-run V1_BUDGET=${V1_BUDGET:-1200} \
		PATH="${XB:+$XB:}$PATH" \
		bash "$HERE/v1_job.sh" "$COMMIT" "$F/v1-report.md" > "$F/v1-$C12.log" 2>&1 || log "v1_job.sh exited $?"
	ls -1t "$F"/v1-*.log | tail -n +6 | xargs -r rm -f
	# its summary line, or why it did not run (v1_job.sh's report without audits)
	NOTE_V1=$(cat "$F/v1-run/summary.txt" 2>/dev/null || sed -n 's/^- //p' "$F/v1-report.md" 2>/dev/null | tail -1)
fi
t1=$(date +%s)

# 5. Report, then the link outputs again with the guest ELF and the boot reports.
# The report always renders (try1of1, 2026-11-07: the commit under test's older full_report.py rejected --since and the
# job lost its whole report): the tool is this job's own copy ($HERE, the same checkout as full_job.sh, not the
# commit's $P); an option it rejects ("unrecognized arguments") is dropped and it runs again; if it still fails, a
# minimal report (commit, make exit, the stage table from the build's stage.json files, the tool's error) stands in.
# Everything after this step appends to whichever report there is.
# step-5-begin
REPORT_TOOL=${FULL_REPORT_TOOL:-$HERE/full_report.py}
RARGS=("$F/build" "$REPORT" --commit "$COMMIT" --make-rc "$rc" --since "$(date -u -d "@$t0" +%Y%m%d-%H%M%S)"
	--seconds $((t1 - t0)) --inputs "tree: $NOTE_TREE" "objects: $NOTE_OBJS"
	"pristine MSVC objects: $(if [ -n "$PR" ]; then echo "branch msvc/pristine/$D12"; else echo "none (zero data image)"; fi)"
	--hide FARM_HOME="$FARM_HOME" SRC="$SRC" EXT="$EXT" TOOLCHAIN="$TCB" ARTIFACTS="$ARTIFACTS"
	${FNVNX_TC:+FNVNX_TC="$FNVNX_TC"} ${DEVKITPRO:+DEVKITPRO="$DEVKITPRO"} ${PRISTINE_OBJS:+PRISTINE_OBJS="$PRISTINE_OBJS"}
	HOME="$HOME")
REPORT_RC=0
python3 -I "$REPORT_TOOL" "${RARGS[@]}" > "$F/full_report.log" 2>&1 || REPORT_RC=$?
if [ "$REPORT_RC" != 0 ] && grep -q 'unrecognized arguments' "$F/full_report.log"; then
	drop=$(sed -n 's/.*unrecognized arguments: //p' "$F/full_report.log" | tr ' ' '\n' | grep '^--' || true)
	KEEP=(); skipval=0
	for a in "${RARGS[@]}"; do
		if [ "$skipval" = 1 ]; then skipval=0; case "$a" in --*) ;; *) continue ;; esac; fi
		if grep -qxF -- "$a" <<<"$drop"; then skipval=1; continue; fi
		KEEP+=("$a")
	done
	log "full_report.py rejected: $(echo $drop); running it without them"
	REPORT_RC=0; python3 -I "$REPORT_TOOL" "${KEEP[@]}" > "$F/full_report.log" 2>&1 || REPORT_RC=$?
fi
if [ "$REPORT_RC" != 0 ] || [ ! -s "$REPORT" ]; then
	log "full_report.py failed (exit $REPORT_RC): minimal report"
	python3 -I - "$F/build" "$REPORT" "$COMMIT" "$rc" "$((t1 - t0))" "$F/full_report.log" <<'PY' || printf '# full build %s\n\n- make exit %s; the report tool failed\n' "${COMMIT:0:12}" "$rc" > "$REPORT"
import glob, json, os, sys
b, out, commit, rc, secs, logf = sys.argv[1:7]
L = ['# full build %s' % commit[:12], '', '- commit: `%s`; make exit %s; %d min' % (commit, rc, int(secs) // 60),
     '- the report tool failed, this is the minimal report: %s' % (open(logf, errors='replace').read().strip().splitlines() or ['?'])[-1][:300],
     '', '| stage | status | summary |', '|---|---|---|']
for p in sorted(glob.glob(os.path.join(b, '*', 'stage.json'))):
    try:
        r = json.load(open(p))
    except (OSError, ValueError):
        continue
    L.append('| %s | %s | %s |' % (os.path.basename(os.path.dirname(p)), r.get('status'), str(r.get('summary') or r.get('error') or '')[:160].replace('|', '/')))
open(out, 'w').write('\n'.join(L) + '\n')
PY
fi
# step-5-end
{
	if [ -f "$V1/report.md" ]; then
		printf -- '- V1-link audits: %s (details: link/%s/v1/report.md)\n' "$NOTE_V1" "$C12"
		[ -f "$V1/watched.txt" ] && sed 's/^- /  - /' "$V1/watched.txt"
		echo
		sed -n '/^| audit /,/^$/p' "$V1/report.md"
	else
		echo "- V1-link audits: $NOTE_V1"
	fi
} >> "$REPORT"
if [ -n "$NOTE_LINKEQ" ]; then echo "- incremental link check (LINKEQ): $NOTE_LINKEQ" >> "$REPORT"; fi
# 5c. The prepared tree as t/<commit12> when no prepare farm published one (the fuzz farms fetch it for the link
# commit; T1), before link/<commit12>, so a fuzz farm that sees the link finds its tree. Never fails the job.
if [ -n "$TREEPUB_PID" ]; then
	wait "$TREEPUB_PID" 2>/dev/null || true
	NOTE_TREEPUB="$(cat "$F/treepub-$C12.txt" 2>/dev/null || echo "the early publish wrote no note (see $F/treepub-$C12.log)") (right after prepare)"
elif [ $INPUTS = 1 ]; then publish_tree "$F/build" || NOTE_TREEPUB="publish_tree exited $?"; else NOTE_TREEPUB="not published (no tree or no objects)"; fi
echo "- prepared tree: $NOTE_TREEPUB" >> "$REPORT"
log "prepared tree: $NOTE_TREEPUB"
if [ $INPUTS = 1 ]; then publish_link "$F/build"; else NOTE_PUB="not published (no tree or no objects)"; fi
echo "- link outputs: $NOTE_PUB" >> "$REPORT"
# 6. The device package (NX1): fnv.nro + the SD tree on the private branch nro/<commit12>.
# A refused publish (an earlier commit's package left in the build directory, V1 NX1 P1) is reported; a push that
# did not land (P2) fails the job, after the report and the log say so.
NRO_RC=0
if [ $INPUTS = 1 ]; then publish_nro "$F/build" || NRO_RC=$?; else NOTE_NRO="not published (no tree or no objects)"; fi
echo "- device package: $NOTE_NRO" >> "$REPORT"
log "device package: $NOTE_NRO"
# 6b. D25 #11: docs/NRO_CHECKLIST.md as checks on this package (tools/report/nro_check.py). On the build directory,
# so the guest ELF and this boot's summary make memory.peak measurable; --commit makes a package left by an earlier
# commit fail `provenance`. The table goes into the report, fnvnx-nrocheck/1 into the report's sidecar under
# "nrocheck" (zero_targets.py #11 reads <h>-full.json; runner.sh publishes the sidecar). Seconds; never fails the job.
# step-6b-begin
rm -f "$F/nrocheck.md" "$F/nrocheck.json"
if [ -d "$F/build/package/sd" ]; then
	python3 -I "$P/tools/report/nro_check.py" "$F/build" --commit "$COMMIT" --out "$F/nrocheck.md" > /dev/null 2>&1 \
		|| log "nro_check.py exited $?"
	# (if, not `[ -f ] &&`: the block must end with status 0 under set -e whatever the checker left)
	if [ -f "$F/nrocheck.md" ]; then { echo; sed '1s/^# /## /' "$F/nrocheck.md"; } >> "$REPORT"; fi
	if [ -f "$F/nrocheck.json" ]; then python3 -I - "$F/nrocheck.json" "${REPORT%.md}.json" <<'PY' || log "nrocheck sidecar merge failed"
import json, sys
try:
    d = json.load(open(sys.argv[2]))
except (OSError, ValueError):
    d = {}
d['nrocheck'] = json.load(open(sys.argv[1]))
json.dump(d, open(sys.argv[2], 'w'), indent=1)
PY
	fi
fi
# step-6b-end
echo "$REPORT"
[ "$NRO_RC" -ne 2 ] || { echo "full_job.sh: publishing nro/$C12 failed" >&2; exit 3; }
