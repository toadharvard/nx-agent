#!/bin/bash
# One-command build of the Sail/isla proof toolchain on a CLEAN Ubuntu 24.04 machine (run as root), ending in the proof IR.
#   bash proof/sail_setup.sh                      build everything from the network (all pins in proof/sail_pins.txt)
#   MIRROR=<dir> bash proof/sail_setup.sh         same, every download taken from <dir> (made by --make-mirror); apt is still needed
#   bash proof/sail_setup.sh --make-mirror <dir>  fill <dir> from the network (opam binary, opam-repository / isla / sail-x86-from-acl2
#                                                 tars of the pinned commits, z3-solver wheel, cargo vendor tar of isla, opam download cache)
# Env: REMS (default /home/user/rems-project: isla/ sail-x86-from-acl2/), TOOLS (/home/user/l3tools: opam, opam-root, opam-repository,
# cargo-home, isla-vendor), WORK (/home/user/l3work/proof), JOBS (2: opam/dune/cargo parallelism).  Idempotent: finished steps are skipped.
# Phases: apt -> opam binary -> sources -> opam switch+packages -> z3 -> isla (cargo --locked) -> isla-sail -> sail_build.sh -> check.
# The result (sha256 of x86_proof.ir) is compared with the pin in sail_pins.txt; a mismatch exits 1.  No step may fail silently.
set -euo pipefail
FARM=$(cd "$(dirname "$0")" && pwd); HERE=$(cd "$FARM/../lift/proof" && pwd); PINS=$HERE/sail_pins.txt
REMS=${REMS:-/home/user/rems-project}; TOOLS=${TOOLS:-/home/user/l3tools}; WORK=${WORK:-/home/user/l3work/proof}
JOBS=${JOBS:-2}; MIRROR=${MIRROR:-}; MAKE_MIRROR=""
if [ "${1:-}" = "--make-mirror" ]; then MAKE_MIRROR=${2:?usage: --make-mirror <dir>}; MIRROR=""; mkdir -p "$MAKE_MIRROR"; MAKE_MIRROR=$(cd "$MAKE_MIRROR" && pwd); fi
[ -z "$MIRROR" ] || MIRROR=$(cd "$MIRROR" && pwd)
export OPAMROOT=$TOOLS/opam-root OPAMYES=1 OPAMJOBS=$JOBS OPAMNODEPEXTS=1 OPAMCOLOR=never CARGO_HOME=$TOOLS/cargo-home CARGO_BUILD_JOBS=$JOBS
CARGO=/usr/bin/cargo; OPAM=$TOOLS/opam; export RUSTC=/usr/bin/rustc RUSTDOC=/usr/bin/rustdoc   # the apt toolchain, never a rustup shim on PATH
mkdir -p "$REMS" "$TOOLS" "$WORK"

die() { echo "sail_setup: $*" >&2; exit 1; }
pin() { awk -v n="$1" -v f="$2" '$1==n{print $f; f=0; exit} END{if(f) exit 1}' "$PINS" || die "no pin '$1' field $2"; }
check_sha() { echo "$2  $1" | sha256sum -c --quiet - || die "sha256 mismatch: $1"; }
PH=""; T0=$(date +%s)
phase() { local n; n=$(date +%s); [ -z "$PH" ] || echo "[phase] $PH $((n-T0))s"; PH=${1:-}; T0=$n; [ -z "$PH" ] || echo "[phase] begin $PH"; }

# get_file <pin-name> <dest>: the pinned file from the mirror or the network, sha256-checked
get_file() { local u; u=$(pin "$1" 2)
  if [ -n "$MIRROR" ]; then cp "$MIRROR/$(basename "$u")" "$2"; else curl -fsSL --retry 3 -o "$2" "$u"; fi
  check_sha "$2" "$(pin "$1" 4)"; }

# git_src <pin-name> <dir>: checkout of the pinned commit at <dir> (mirror: tar, sha256 of the uncompressed tar; network: shallow
# fetch of the commit, `git rev-parse` checked).  <dir>/.pinned_commit marks a finished checkout; an existing other dir is an error.
git_src() { local n=$1 d=$2 c; c=$(pin "$n" 3)
  [ "$(cat "$d/.pinned_commit" 2>/dev/null)" = "$c" ] && return 0
  [ "$(git -C "$d" rev-parse HEAD 2>/dev/null)" = "$c" ] && return 0
  [ ! -e "$d" ] || die "$d exists but is not $n at $c (remove it)"
  rm -rf "$d.tmp"; mkdir -p "$d.tmp"
  if [ -n "$MIRROR" ]; then
    local t=$MIRROR/$n-${c:0:12}.tar.gz; [ "$(gzip -dc "$t" | sha256sum | cut -d' ' -f1)" = "$(pin "$n" 4)" ] || die "sha256 mismatch: $t"
    tar -xzf "$t" -C "$d.tmp" --strip-components=1
  else
    git -C "$d.tmp" init -q; git -C "$d.tmp" fetch -q --depth 1 "$(pin "$n" 2)" "$c"; git -C "$d.tmp" checkout -q FETCH_HEAD
    [ "$(git -C "$d.tmp" rev-parse HEAD)" = "$c" ] || die "$n: fetched commit differs from the pin"
  fi
  echo "$c" > "$d.tmp/.pinned_commit"; mv "$d.tmp" "$d"; }

det_tar() { tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -cf - -C "$1" .; }   # deterministic tar stream of a dir

phase apt
PKGS="build-essential git curl ca-certificates unzip bzip2 xz-utils patch m4 pkg-config rsync bubblewrap libgmp-dev python3 python3-pip cargo rustc"
if ! dpkg -s $PKGS >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive; apt-get update -q; apt-get install -y -q --no-install-recommends $PKGS
fi
[ -x $CARGO ] || die "apt cargo missing"
echo "cargo: $($CARGO --version)  rustc: $(/usr/bin/rustc --version)  python: $(python3 --version)"

phase "opam binary"
if [ ! -x "$OPAM" ] || [ "$($OPAM --version)" != "$(pin opam-bin 3)" ]; then
  get_file opam-bin "$OPAM.tmp"; chmod +x "$OPAM.tmp"; mv "$OPAM.tmp" "$OPAM"
fi
echo "opam $($OPAM --version)"

phase "sources (opam-repository, isla, sail-x86-from-acl2)"
git_src opam-repository "$TOOLS/opam-repository"; git_src isla "$REMS/isla"; git_src sail-x86-from-acl2 "$REMS/sail-x86-from-acl2"
check_sha "$REMS/isla/Cargo.lock" "$(pin isla-cargo-lock 4)"

phase "z3-solver wheel"
Z3V=$(pin z3-solver 3)
if [ "$(python3 -c 'import importlib.metadata as m;print(m.version("z3-solver"))' 2>/dev/null || true)" != "$Z3V" ]; then
  mkdir -p "$WORK/wheel"; get_file z3-solver "$WORK/wheel/$(basename "$(pin z3-solver 2)")"
  python3 -m pip install -q --break-system-packages --no-index --no-deps --find-links "$WORK/wheel" "z3-solver==$Z3V"
fi
Z3=$(python3 -c 'import z3,os;print(os.path.dirname(z3.__file__))'); [ -f "$Z3/lib/libz3.so" ] || die "no libz3.so in $Z3"
echo "z3-solver $Z3V at $Z3"

# opam: root on the pinned opam-repository (local dir), switch "sail" on OCaml 4.14.2, exactly the pinned package set
PKGLIST=$(awk '$1=="opam-pkg"{print $2}' "$PINS" | sort)
opam_prepare() {
  if [ -n "$MIRROR" ]; then mkdir -p "$OPAMROOT/download-cache"; cp -rn "$MIRROR/opam-download-cache/." "$OPAMROOT/download-cache/"; fi
  # opam.ocaml.org serves this archive mirror in its repo file; the git tree lacks it and github.com source tarballs are not
  # reachable everywhere.  Only a download location: every source is still checked against the checksum in its opam file.
  [ -n "$MIRROR" ] || grep -q '^archive-mirrors' "$TOOLS/opam-repository/repo" || echo 'archive-mirrors: "https://opam.ocaml.org/cache"' >> "$TOOLS/opam-repository/repo"
  [ -f "$OPAMROOT/config" ] || $OPAM init --bare --disable-sandboxing --no-setup --yes default "$TOOLS/opam-repository"
}
installed() { $OPAM list --switch=sail --installed --columns=name,version 2>/dev/null | awk '!/^#/ && NF>=2{print $1"."$2}' | sort; }

if [ -n "$MAKE_MIRROR" ]; then
  phase "make-mirror"
  M=$MAKE_MIRROR; export OPAMROOT=$WORK/mirror-opamroot; rm -rf "$OPAMROOT" "$WORK/mirror-src"   # a throw-away opam root; mkdir -p "$WORK/mirror-src"
  get_file opam-bin "$M/$(basename "$(pin opam-bin 2)")"; cp "$WORK/wheel/"*.whl "$M/"
  for n in opam-repository isla sail-x86-from-acl2; do   # from fresh network checkouts, so the tar content is exactly the pinned commit
    c=$(pin $n 3); git_src $n "$WORK/mirror-src/$n"
    git -C "$WORK/mirror-src/$n" archive --format=tar --prefix=$n/ "$c" > "$WORK/mirror-src/$n.tar"
    [ "$(sha256sum < "$WORK/mirror-src/$n.tar" | cut -d' ' -f1)" = "$(pin $n 4)" ] || die "$n tar sha256 differs from the pin"
    gzip -n -9 < "$WORK/mirror-src/$n.tar" > "$M/$n-${c:0:12}.tar.gz"; rm "$WORK/mirror-src/$n.tar"
  done
  (cd "$WORK/mirror-src/isla" && $CARGO vendor --locked "$WORK/mirror-src/vendor" > /dev/null)
  det_tar "$WORK/mirror-src/vendor" > "$WORK/mirror-src/vendor.tar"
  VS=$(sha256sum < "$WORK/mirror-src/vendor.tar" | cut -d' ' -f1); [ "$VS" = "$(pin isla-vendor 4)" ] || die "isla-vendor tar sha256 $VS differs from the pin"
  gzip -n -9 < "$WORK/mirror-src/vendor.tar" > "$M/isla-vendor.tar.gz"
  opam_prepare; $OPAM switch create sail --empty
  $OPAM install --switch=sail --download-only $(echo "$PKGLIST")
  rm -rf "$M/opam-download-cache"; cp -r "$OPAMROOT/download-cache" "$M/opam-download-cache"
  rm -rf "$OPAMROOT" "$WORK/mirror-src"
  (cd "$M" && find . -type f ! -name SHA256SUMS | sort | xargs sha256sum > SHA256SUMS)
  phase ""; echo "mirror $M: $(du -sh "$M" | cut -f1), $(find "$M" -type f | wc -l) files"; exit 0
fi

phase "opam switch + packages"
opam_prepare
$OPAM switch list --short | grep -qx sail || $OPAM switch create sail "ocaml-base-compiler.$(pin ocaml-compiler 3)"
[ "$(installed)" = "$PKGLIST" ] || { $OPAM install --switch=sail $(echo "$PKGLIST"); }
[ "$(installed)" = "$PKGLIST" ] || { diff <(installed) <(echo "$PKGLIST") | head -20; die "installed opam packages differ from the pins"; }
$OPAM exec --switch=sail -- sail --version | head -1

phase "isla (cargo --locked)"
CFG=()
if [ -n "$MIRROR" ]; then
  V=$TOOLS/isla-vendor
  if [ ! -d "$V" ]; then
    gzip -dc "$MIRROR/isla-vendor.tar.gz" > "$V.tar"; check_sha "$V.tar" "$(pin isla-vendor 4)"
    mkdir -p "$V.tmp"; tar -xf "$V.tar" -C "$V.tmp"; rm "$V.tar"; mv "$V.tmp" "$V"
  fi
  CFG=(--config 'source.crates-io.replace-with="vendored"' --config "source.vendored.directory=\"$V\"" --offline)
fi
if [ ! -x "$REMS/isla/target/release/isla-execute-function" ]; then
  (cd "$REMS/isla" && LIBRARY_PATH=$Z3/lib LD_LIBRARY_PATH=$Z3/lib C_INCLUDE_PATH=$Z3/include $CARGO build --release --locked "${CFG[@]}")
fi
ls -l "$REMS/isla/target/release/isla-execute-function" | cut -d' ' -f5-

phase "isla-sail (make)"
if [ ! -x "$REMS/isla/isla-sail/plugin.cmxs" ]; then eval "$($OPAM env --switch=sail)"; make -C "$REMS/isla/isla-sail"; fi

phase "sail_build.sh (stock IR, proof IR, smoke test)"
REMS=$REMS TOOLS=$TOOLS WORK=$WORK CARGO=$CARGO bash "$FARM/sail_build.sh"

phase "check"
GOT=$(sha256sum "$WORK/sail/x86_proof.ir" | cut -d' ' -f1); WANT=$(pin x86_proof.ir 4)
echo "x86_proof.ir sha256 $GOT ($(stat -c %s "$WORK/sail/x86_proof.ir") bytes), pinned $WANT"
[ "$GOT" = "$WANT" ] || die "x86_proof.ir differs from the pinned hash"
phase ""; echo "sail_setup: OK"
