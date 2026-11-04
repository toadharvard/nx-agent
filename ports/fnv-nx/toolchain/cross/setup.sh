#!/usr/bin/env bash
# setup.sh - the AArch64 cross tools of the decomp compile and the link step (aarch64-linux-gnu-g++/gcc/cpp/as/ld/
# objcopy/objdump/nm/readelf/c++filt: GCC 13.3.0 and binutils 2.42 of Ubuntu 24.04), unpacked from the pinned
# Ubuntu packages of packages.tsv into a prefix. No root and no apt: the .deb files are fetched by their pool path
# and checked against their SHA-256, then unpacked with dpkg-deb (or ar + tar).
#
#   toolchain/cross/setup.sh <prefix>          -> <prefix>/bin/aarch64-linux-gnu-* (CROSS_BIN of the pipeline)
#
# This is what S2c's private copy s2c/xtc was (two of these packages unpacked by hand, the rest taken from the
# installed system packages); here every package is pinned, so another machine gets the same compiler and the
# same objects. Host libraries the cross tools need (isl, mpc, mpfr, gmp, zstd, zlib, jansson, sframe) are
# unpacked from their pinned packages too, only when the host lacks them; the tools then run through wrappers that
# set LD_LIBRARY_PATH. The units are compiled with -mabi=ilp32 -c: Ubuntu's glibc headers, no ILP32 libraries
# (the guest links against fnvnx-a64's newlib, toolchain/gcc/build.sh).
#
# Mirrors: $CROSS_MIRRORS (space separated), default archive.ubuntu.com, then a snapshot.ubuntu.com date that has
# every pinned version (superseded -updates versions leave the main archive). Rerunning with the same
# packages.tsv does nothing. KEEP_DEBS=1 keeps the downloads in <prefix>/dl.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=$(mkdir -p "${1:?prefix}" && cd "$1" && pwd)
LIST=$HERE/packages.tsv
MIRRORS=${CROSS_MIRRORS:-"http://archive.ubuntu.com/ubuntu https://snapshot.ubuntu.com/ubuntu/20261101T000000Z"}
ID=$(cat "$LIST" "$0" | sha256sum | cut -c1-16)
if [ -f "$PREFIX/.setup-id" ] && [ "$(cat "$PREFIX/.setup-id")" = "$ID" ] && [ -x "$PREFIX/bin/aarch64-linux-gnu-g++" ]; then
	echo "cross tools up to date in $PREFIX: $("$PREFIX/bin/aarch64-linux-gnu-g++" --version | head -1)"
	exit 0
fi
for t in curl sha256sum tar; do command -v $t >/dev/null || { echo "setup.sh needs $t"; exit 1; }; done
if ! command -v dpkg-deb >/dev/null; then
	command -v ar >/dev/null && command -v zstd >/dev/null || { echo "setup.sh needs dpkg-deb, or ar + zstd"; exit 1; }
fi
rm -rf "$PREFIX/root" "$PREFIX/hostroot" "$PREFIX/bin" "$PREFIX/.setup-id"
mkdir -p "$PREFIX/dl" "$PREFIX/root" "$PREFIX/bin"

fetch() {   # pool path, sha256 -> the .deb in dl/
	local f=$PREFIX/dl/$(basename "$1") m
	if [ -f "$f" ] && echo "$2  $f" | sha256sum -c --quiet - 2>/dev/null; then echo "$f"; return; fi
	for m in $MIRRORS; do
		if curl -fsSL --retry 3 -o "$f.part" "$m/$1" && echo "$2  $f.part" | sha256sum -c --quiet - 2>/dev/null; then
			mv "$f.part" "$f"; echo "$f"; return
		fi
		rm -f "$f.part"
	done
	echo "cannot fetch $1 with SHA-256 $2 from: $MIRRORS" >&2; return 1
}
unpack() {  # .deb, destination
	if command -v dpkg-deb >/dev/null; then dpkg-deb -x "$1" "$2"; return; fi
	local t; t=$(mktemp -d); (cd "$t" && ar x "$1")
	case $(ls "$t"/data.tar.*) in
		*.zst) zstd -dc "$t"/data.tar.zst | tar -x -C "$2" ;;
		*) tar -xf "$t"/data.tar.* -C "$2" ;;
	esac
	rm -rf "$t"
}
while IFS=$'\t' read -r pkg ver role pool sum; do
	case "$pkg" in ''|'#'*) continue ;; esac
	if [ "$role" = target ]; then unpack "$(fetch "$pool" "$sum")" "$PREFIX/root"; echo "  $pkg $ver"; fi
done < "$LIST"

R=$PREFIX/root/usr
ELFS=$(ls "$R"/bin/aarch64-linux-gnu-* "$R"/libexec/gcc-cross/aarch64-linux-gnu/13/{cc1,cc1plus,collect2,lto-wrapper} \
	"$R"/aarch64-linux-gnu/bin/* 2>/dev/null)
missing() { for e in $ELFS; do ldd "$e" 2>/dev/null | awk '/not found/ {print $1}'; done | sort -u; }
LIBDIR=""
if [ -n "$(missing)" ] || [ "${CROSS_HOSTLIBS:-0}" = 1 ]; then   # CROSS_HOSTLIBS=1: always (tests the path)
	echo "host lacks: $(missing | tr '\n' ' ')- unpacking the pinned host libraries"
	mkdir -p "$PREFIX/hostroot"
	while IFS=$'\t' read -r pkg ver role pool sum; do
		case "$pkg" in ''|'#'*) continue ;; esac
		if [ "$role" = hostlib ]; then unpack "$(fetch "$pool" "$sum")" "$PREFIX/hostroot"; echo "  $pkg $ver (host)"; fi
	done < "$LIST"
	LIBDIR=$PREFIX/hostroot/usr/lib/x86_64-linux-gnu:$PREFIX/hostroot/lib/x86_64-linux-gnu
	still=$(export LD_LIBRARY_PATH=$LIBDIR; missing)
	[ -z "$still" ] || { echo "still missing on this host: $still"; exit 1; }
fi

# bin/: the drivers (GCC finds cc1plus, its headers and the sysroot relative to the resolved driver path) and
# binutils, by their unversioned names
link() {    # name, target
	if [ -z "$LIBDIR" ]; then ln -sf "$2" "$PREFIX/bin/$1"; return; fi
	printf '#!/bin/sh\nLD_LIBRARY_PATH="%s${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" exec "%s" "$@"\n' "$LIBDIR" "$2" \
		> "$PREFIX/bin/$1"
	chmod 755 "$PREFIX/bin/$1"
}
for t in gcc g++ cpp gcc-ar gcc-nm gcc-ranlib; do link "aarch64-linux-gnu-$t" "$R/bin/aarch64-linux-gnu-$t-13"; done
for f in "$R"/bin/aarch64-linux-gnu-*; do
	n=$(basename "$f")
	case "$n" in *-13) continue ;; esac
	[ -e "$PREFIX/bin/$n" ] || link "$n" "$f"
done
if [ "${KEEP_DEBS:-0}" != 1 ]; then rm -rf "$PREFIX/dl"; fi

# check: the census flag set on a small ILP32 C++ unit, assembled, and an ILP32 relocatable link
T=$(mktemp -d)
printf '#include <cstdio>\n#include <cstring>\n#include <new>\n#include <cstdint>\nstruct A { virtual ~A(); int x; };\nA::~A() {}\nint f(const char *s) { char b[16]; std::strncpy(b, s, 15); b[15] = 0; return (int)sizeof(void *) + std::printf("%%s", b); }\n' > "$T/t.cpp"
"$PREFIX/bin/aarch64-linux-gnu-g++" -mabi=ilp32 -std=gnu++17 -c -O2 -fms-extensions -fshort-wchar -fsigned-char \
	-fno-strict-aliasing -fwrapv -fno-delete-null-pointer-checks -ffp-contract=off -fPIC "$T/t.cpp" -o "$T/t.o"
"$PREFIX/bin/aarch64-linux-gnu-ld" -m aarch64linux32 -r "$T/t.o" -o "$T/r.o"
"$PREFIX/bin/aarch64-linux-gnu-readelf" -h "$T/r.o" | grep -q 'ELF32' || { echo "ILP32 check failed"; exit 1; }
rm -rf "$T"
echo "$ID" > "$PREFIX/.setup-id"
echo "cross tools in $PREFIX/bin: $("$PREFIX/bin/aarch64-linux-gnu-g++" --version | head -1)${LIBDIR:+ (host libraries from the pinned packages)}"
