#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Host tests of the Remote Hekate core (no device access), run against the tree
# produced by scripts/fetch-and-apply.sh.
#
# Env:
#   HEKATE_TREE  patched tree (default: <remote-hekate>/hekate)
#   CC           host C compiler (default: cc)
#   FUZZ         auto (default: native on Linux, skipped on macOS where the ASan
#                runtime can hang during shadow-memory init), native, docker, off
#   FUZZ_N       fuzz iterations (default: 200000)
#   FUZZ_IMAGE   image for FUZZ=docker (default: the pinned devkitARM image, Debian gcc)
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/.." && pwd)
tree=${HEKATE_TREE:-"$root/hekate"}
core="$tree/nyx/nyx_gui/frontend/remote_core.c"
cc=${CC:-cc}
fuzz=${FUZZ:-auto}
fuzz_n=${FUZZ_N:-200000}
fuzz_image=${FUZZ_IMAGE:-devkitpro/devkitarm@sha256:116afba8df8453961de2936ffab20dd441edf4d682856c1ec8b0e53d7ed0bbf5}
worker="$root/../worker/source"

if [ ! -f "$core" ]; then
	echo "run.sh: $core not found; run scripts/fetch-and-apply.sh first" >&2
	exit 1
fi

case $(uname -s) in
Darwin) lib="$here/core.dylib" ;;
*) lib="$here/core.so" ;;
esac

"$cc" -std=gnu11 -O2 -Wall -Wextra -Werror -shared -fPIC -o "$lib" "$core"
REMOTE_CORE_LIB="$lib" python3 "$here/test_core_v1.py"
REMOTE_CORE_LIB="$lib" python3 "$here/test_core_v2.py"

"$cc" -std=gnu11 -O1 -Wall -Wextra -Werror -I"$tree/bdk" \
	-o "$here/test_r2c_crosscheck" "$here/test_r2c_crosscheck.c" "$worker/logic.c"
"$here/test_r2c_crosscheck"

if [ "$fuzz" = auto ]; then
	case $(uname -s) in
	Linux) fuzz=native ;;
	*) fuzz=off ;;
	esac
fi

case $fuzz in
native)
	"$cc" -std=gnu11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all \
		-I"$tree/nyx/nyx_gui/frontend" -o "$here/fuzz_core" "$here/fuzz_core.c" "$core"
	"$here/fuzz_core" "$fuzz_n"
	;;
docker)
	docker=$(command -v docker || true)

	if [ -z "$docker" ]; then
		echo "run.sh: FUZZ=docker but docker is not in PATH" >&2
		exit 1
	fi

	"$docker" run --rm -v "$tree":/src:ro -v "$here":/tests:ro "$fuzz_image" sh -c \
		"gcc -std=gnu11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all \
		 -I/src/nyx/nyx_gui/frontend -o /tmp/fuzz_core /tests/fuzz_core.c /src/nyx/nyx_gui/frontend/remote_core.c \
		 && /tmp/fuzz_core $fuzz_n"
	;;
off)
	echo "fuzz_core: skipped (FUZZ=native or FUZZ=docker to run it)"
	;;
*)
	echo "run.sh: unknown FUZZ=$fuzz" >&2
	exit 1
	;;
esac
