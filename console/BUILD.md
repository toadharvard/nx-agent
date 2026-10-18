# Building the console modules

## Toolchain

Worker and Rescue are libnx sysmodules built with devkitA64 inside the official devkitPro image,
pinned by digest:

```
devkitpro/devkita64@sha256:1fc388c3a0d34bd2045a6dadcb1020e069d5f876a187fd705de14b4440c00282
image id sha256:146025c8997f3effc12ad2e2acb8633b1a167d98bb6a551cbd6760f2aac3c892 (created 2026-02-19)
```

`worker/Makefile` and `rescue/Makefile` are the stock libnx sysmodule template, byte-identical to
`/opt/devkitpro/examples/switch/templates/sysmodule/Makefile` in that image. All module-specific
settings live in `config.json` (NPDM: title ID, service access, kernel capabilities).

## Build

The Makefile names the output after the directory (`TARGET := $(notdir $(CURDIR))`), so mount each
module at `/worker` or `/rescue`; any other mount point changes the module name and the hashes.

```sh
IMG=devkitpro/devkita64@sha256:1fc388c3a0d34bd2045a6dadcb1020e069d5f876a187fd705de14b4440c00282
docker run --rm -v "$PWD/worker:/worker" -w /worker "$IMG" sh -c "make clean && make"
docker run --rm -v "$PWD/rescue:/rescue" -w /rescue "$IMG" sh -c "make clean && make"
```

Outputs: `worker/worker.nsp` and `rescue/rescue.nsp` (install as
`atmosphere/contents/<TID>/exefs.nsp`), plus `.nso`, `.npdm`, `.elf` and `build/`, all git-ignored.

## Reproducibility against the device builds

Device-tested baselines: Worker `21da756c150edf6919fa1197ca1de74f7ca212f307bf5d3350fb1a50b7f75757`,
Rescue `9f5ff246a30b1698bd6653fec8236b56ffdfdb0533ed38288cefa87159970854`.

- The original sources, built with the image above, reproduce both baselines byte for byte.
- The sources in this repository build to Worker
  `c6c17e0b6618c71d77607d3f9c43a6bd9025d890e87c72af39be8c802998f392` and Rescue
  `933c1bebbfea8b03e133e8e2816579b3a6c4687f53c5901fc273afb2dc1d11f7`.
- The only difference is the `.note.gnu.build-id` note. Every loaded section (`.text`, `.rodata`,
  `.data`, `.dynamic`, `.got`, `.nx-module-name`, ...) and both NPDMs are byte-identical to the
  baselines. The linker computes the build ID over the whole ELF including DWARF line tables, which
  shift by one line because of the SPDX header; the unused `r2c_decode` is discarded by
  `--gc-sections`.

To compare one section of two builds, run inside the image:

```sh
docker run --rm -v "$PWD:/c" "$IMG" sh -c '
  OC=/opt/devkitpro/devkitA64/bin/aarch64-none-elf-objcopy
  $OC -O binary --only-section=.text /c/worker/worker.elf /tmp/a && sha256sum /tmp/a'
```

## Host tests

The pure logic, file-write policy, Rescue dispatcher, boot guard, mailbox and launch job are
compiled for the host (`-DSIDECAR_HOST_TEST`) and exercised by C programs and Python ctypes tests.
Requirements: a C compiler (`cc`), OpenSSL libcrypto (found via `pkg-config libcrypto`, else
`brew --prefix openssl@3`), Python 3.11+; `test_host_interfaces.py` and
`test_async_launch_host.py` import `nxagent.proto` from `host/src` and need `pyusb`.

```sh
sh tests/build_tests.sh                     # -> tests/bin/ (.dylib on macOS, .so on Linux)
for t in test_launch_job test_listener test_mailbox test_mailbox_runtime test_worker_logic; do
  tests/bin/$t || exit 1
done
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: 5 C programs print `PASS`, 25 Python tests pass.
