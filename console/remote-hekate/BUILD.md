# Build record

Reproduction of the reference Remote Hekate v2 build from this patch series.

## Inputs

- Upstream: `https://github.com/CTCaer/hekate`, tag `v6.5.2`, commit
  `e26b941f905e2d8d0eb22da970a41f81e5b8e030` (`UPSTREAM`).
- `patches/0001..0003` and `src/nyx/nyx_gui/frontend/remote{,_core}.{c,h}`.
- Toolchain image: `devkitpro/devkitarm@sha256:116afba8df8453961de2936ffab20dd441edf4d682856c1ec8b0e53d7ed0bbf5`.
  This is an OCI index. Its platform manifests:
  - `linux/arm64`: `sha256:d532dbc32d4ed7412ecf2df2a9710070d0f11a01cc7334e28d102871746533cf`,
    image ID `sha256:23e9c452eafb…`. The reference build used this same image.
  - `linux/amd64`: `sha256:15b79ce75822c289538d8153da5fa7aafe5e6adc32ad8a575a197beca0f0761b`.
  - The image was created 2026-06-10.
- Compilers in the image: `arm-none-eabi-gcc (devkitARM) 16.1.0`, and host
  `gcc (Debian 12.2.0-14+deb12u1)`, which the docker fuzz uses.
- Build command (`scripts/build.sh`): `make clean && make -j4 && make -C nyx -j4` in the patched tree.

## Source identity

The tree produced by `scripts/fetch-and-apply.sh` was compared with `diff -r`
against the reference working tree: the hekate v6.5.2 clone carrying the fork's
uncommitted changes. The comparison excluded:

- `.git`, `build/` and `output/`;
- hekate's own gitignored generated files (`loader/payload_0{0,1}.h`,
  `tools/bin2c/bin2c`, `tools/lz/lz77`);
- the reference tree's local notes, logs, deploy folder and test folder.

Result: **identical**, 442 files. `git status` of the produced tree lists the same
11 modified and 4 new files as the reference tree. The concatenation of the three
patches is byte-identical to the reference `git diff`. The comparison still holds
after a second run, and after a run over a tree with tampered files: the script
resets tracked files to the pin and re-applies.

## Binaries

| Artifact | SD path | Size | sha256 | Reference |
|---|---|---|---|---|
| `build/Remote-Hekate.bin` (`output/hekate.bin`) | `bootloader/payloads/Remote-Hekate.bin` | 110032 | `a835933c917abc284e94c807446d06f233cbf59ae6dcbb6736e211db65e863f8` | match |
| `build/nyx.bin` (`output/nyx.bin`) | `bootloader/remote/nyx.bin` | 475229 | `ef73c3a0997476c95972228e462a0c959021a74bbab4521fae7a8b1ce5dfea0b` | match |

These hashes were reproduced with both platform variants of the pinned index:

- `linux/arm64`, native on an Apple Silicon host;
- `linux/amd64`, emulated: `DEVKITARM_PLATFORM=linux/amd64 DEVKITARM_IMAGE=devkitpro/devkitarm@sha256:15b79ce7…`.

Docker's classic image store cannot hold both variants under the index digest, so
the amd64 run names the platform manifest digest directly.

The build emits one expected warning from the patches:

```
bdk/usb/usbd.c: enumeration value 'USB_GADGET_REMOTE' not handled in switch
```

`usbd.c` is the T210 (Erista) USB 2.0 controller path. The remote gadget is only
started on T210B01, which uses `xusbd.c`.

## Host tests (`tests/run.sh`)

| Check | macOS 26 arm64 (Apple clang 17, Python 3.14) | Linux (`python:3.12-bookworm`, gcc 12.2) |
|---|---|---|
| `test_core_v1.py` | 8/8 OK | 8/8 OK |
| `test_core_v2.py` | 35/35 OK | 35/35 OK |
| `test_r2c_crosscheck` | 20838 checks OK | 20838 checks OK |
| `fuzz_core` (ASan+UBSan), 200000 requests | OK (`FUZZ=docker`) | OK (native) |

`test_r2c_crosscheck` compares the MIT `console/common/r2c_layout.h` and the
`r2c_encode`/`r2c_decode` functions in `console/worker/source/logic.c` against
hekate's `rtc_reboot_reason_t` and its RTC register defines. It covers:

- every (reason, index) pair in 0..15 × 0..15;
- all 4096 values of the two 6-bit registers.

As a negative control, a copy of `logic.c` with the index shifted by one bit fails
the check.

## Device status

Not verified on a device. The reference build record also marks this v2 build as
not device-verified. On-device checks belong to the P7 checklist.
