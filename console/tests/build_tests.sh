#!/bin/sh
# SPDX-License-Identifier: MIT
# Builds host test binaries and shared libraries from the console sources into tests/bin.
set -eu
cd "$(dirname "$0")"
CC=${CC:-cc}
OUT=bin
mkdir -p "$OUT"
case "$(uname -s)" in
  Darwin) EXT=dylib ;;
  *) EXT=so ;;
esac
if pkg-config --exists libcrypto 2>/dev/null; then
  SSL_CFLAGS=$(pkg-config --cflags libcrypto)
  SSL_LIBS=$(pkg-config --libs libcrypto)
else
  SSL=$(brew --prefix openssl@3 2>/dev/null || echo /usr)
  SSL_CFLAGS="-I$SSL/include"
  SSL_LIBS="-L$SSL/lib -lcrypto"
fi
CF="-O1 -Wall -Wno-deprecated-declarations -DSIDECAR_HOST_TEST ${CFLAGS:-}"
$CC $CF -shared -fPIC $SSL_CFLAGS -I../rescue/source \
  ../rescue/source/rescue.c ../rescue/source/bridge.c ../rescue/source/launch_job.c \
  ../rescue/source/fileops.c ../rescue/source/boot_guard.c process_mock.c $SSL_LIBS -o "$OUT/rescue-test.$EXT"
$CC $CF -shared -fPIC $SSL_CFLAGS ../worker/source/fileops.c $SSL_LIBS -o "$OUT/worker-fileops-test.$EXT"
$CC -Wall -pthread test_launch_job.c ../rescue/source/launch_job.c -o "$OUT/test_launch_job"
$CC -Wall -I../worker/source test_listener.c ../worker/source/listener.c -o "$OUT/test_listener"
$CC -Wall test_mailbox.c ../worker/source/mailbox.c -o "$OUT/test_mailbox"
$CC -Wall -Imailbox-mock test_mailbox_runtime.c -o "$OUT/test_mailbox_runtime"
$CC -Wall -I../worker/source test_worker_logic.c ../worker/source/logic.c -o "$OUT/test_worker_logic"
echo built
