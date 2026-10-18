# Remote Hekate

Remote Hekate is [hekate](https://github.com/CTCaer/hekate) v6.5.2 with one addition:
while its Nyx GUI is running, it exposes a small USB vendor interface so a host
can see and drive the bootloader. Through that interface a host can:

- take screenshots;
- inject touches;
- list the boot entries of the installed hekate;
- continue the normal boot;
- start SD UMS;
- reboot or power off;
- arm a watchdog and read status.

Everything else is stock hekate. All credit for hekate, Nyx and the BDK goes to
CTCaer and the hekate contributors.

This directory is licensed under **GPL-2.0-only** (see `LICENSE`, copied from
hekate). The rest of the nx-agent repository is MIT; nothing here is linked into it.

## Layout

| Path | Contents |
|---|---|
| `UPSTREAM` | Pinned upstream: `https://github.com/CTCaer/hekate`, tag `v6.5.2`, commit `e26b941f905e2d8d0eb22da970a41f81e5b8e030`. |
| `patches/` | Changes to upstream files, applied in order with `git apply`. |
| `src/` | New files, mirrored at their path in the hekate tree (`nyx/nyx_gui/frontend/remote*.{c,h}`). |
| `scripts/fetch-and-apply.sh` | Clones the pin into `hekate/`, verifies the commit, applies `patches/`, copies `src/`. |
| `scripts/build.sh` | Builds in the digest-pinned `devkitpro/devkitarm` image. The results go to `build/Remote-Hekate.bin`, `build/nyx.bin` and `build/SHA256SUMS`. |
| `tests/` | Host tests of the protocol core, the ASan/UBSan fuzzer, and the R2C cross-check. |
| `deploy-template/` | Files for `bootloader/remote/` on the SD card. |
| `BUILD.md` | Reproducibility record: toolchain digest and reference hashes. |

The patch series:

1. `0001` **usb**: a vendor-class remote gadget. It also adds a non-blocking enumerate/poll split in `xusbd.c`.
2. `0002` **paths**: hekate and Nyx read and write `bootloader/remote/*` instead of `bootloader/*`.
3. `0003` **nyx**: GUI hooks (poll, touch injection, suspend around HID/UMS and `hw_deinit`) and the Makefile entries for the new files.

## Build

```sh
scripts/fetch-and-apply.sh   # -> hekate/  (idempotent; resets tracked files to the pin)
scripts/build.sh             # -> build/Remote-Hekate.bin, build/nyx.bin
tests/run.sh                 # host tests against hekate/
```

`build.sh` needs Docker. `run.sh` needs a C compiler and Python 3. On Linux,
`run.sh` also runs the ASan/UBSan fuzzer natively. On macOS the fuzzer is skipped
by default; `FUZZ=docker tests/run.sh` runs it in the devkitARM container.
`tests/test_r2c_crosscheck.c` checks two things against hekate's
`rtc_reboot_reason_t` from `bdk/rtc/max77620-rtc.h`:

- the MIT reboot-to-config code (`console/common/r2c_layout.h`, `r2c_encode` and
  `r2c_decode` in `console/worker/source/logic.c`);
- the register addresses.

## USB identity

| Field | Value |
|---|---|
| VID | `0x11EC` |
| PID | `0xA7E1` |
| Interface | class `0xFF`, subclass `0x52`, protocol `0x01` |
| Endpoints | one bulk OUT/IN pair (UMS endpoint layout) |

The descriptors are a copy of hekate's UMS descriptors with only these fields changed.

VID `0x11EC` and the PID range belong to hekate / CTCaer. Stock hekate uses:

| PID | Gadget |
|---|---|
| `0xA7E0` | UMS |
| `0xA7E1` | HID Joy-Con gamepad |
| `0xA7E2` | HID touchpad |

Remote Hekate reuses this VID because the gadget is part of a hekate build. Its PID
`0xA7E1` is **the same as stock hekate's HID gamepad**. A host must therefore
identify the remote interface by the vendor interface triple (`0xFF/0x52/0x01`),
not by VID:PID alone. A HID gamepad on the same VID:PID is not Remote Hekate.
Anyone redistributing a renamed build should use their own VID/PID.

## Protocol (version 2)

The host sends one fixed **64-byte request**: 16 little-endian `u32`:

```
magic = 0x5258594E, seq, op, arg[0..12]
```

Unused arguments must be zero. Nyx answers every request with a fixed
**4096-byte reply**. The reply starts with an 8-word header:

```
magic, seq, error, data_len, op, generation, 0, 0
```

Up to 4064 data bytes follow. The device never executes host-supplied code and
never reads or writes host-chosen memory.

| Op | Request args | Reply data / effect |
|---|---|---|
| 1 info | – | `version=1, 1280, 720, 4 bytes/px, frame size, max hold 500 ms` |
| 2 snapshot | – | Freezes a copy of the framebuffer. Returns its `generation`. |
| 3 read | `generation, offset, length ≤ 4064` | Snapshot bytes. Fails with `STALE` if the generation changed. |
| 4 touch | `x < 1280, y < 720, hold_ms 1..500` | Presses at (x, y). Releases on its own after `hold_ms`, so a lost host never leaves a finger down. |
| 5 release | – | Releases the touch now. |
| 6 entries | – | `index\tname\n` lines for the boot entries of the **installed** `bootloader/hekate_ipl.ini`. The numbering matches hekate's autoboot / R2C index. |
| 7 continue | – | Chainloads `bootloader/update.bin` (the installed hekate) with a cold-boot config, so the installed hekate applies its own `[config]`. |
| 8 UMS | – | Exposes the SD card as USB mass storage. |
| 9 reboot | `0` = OFW, `1` = normal | Reboots. Returns `UNAVAIL` where Nyx's own power menu would not offer that choice. |
| 10 power off | – | Powers off. |
| 11 watchdog | `0` = off, or `30..3600` s | Re-arms the watchdog. |
| 12 status | – | `protocol version, watchdog seconds left, watchdog enabled, host command count` |

Error codes:

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | unknown op |
| 2 | bad length, magic or argument |
| 3 | no memory |
| 4 | stale snapshot |
| 5 | busy |
| 6 | unavailable |
| 7 | reply too big |

Ops 7–10 have side effects, and their timing is deferred:

- An op is armed when its reply is queued.
- It runs 200 ms after the host has collected the reply, or after 2 s if the host never does.
- Only one action can be pending at a time.
- While a blocking Nyx tool owns the system, ops 6–10 answer `BUSY` and USB stays serviced.

## Watchdog

Remote Hekate must never strand a console in the bootloader. If no host command
arrives for the watchdog period, it does exactly what op 7 does: it continues the
normal boot through the installed hekate. These events restart the countdown:

- any well-formed request;
- a local touch on the panel;
- the end of a UMS session.

The period comes from `bootloader/remote/remote.ini`:

```ini
[config]
watchdog=90
```

How `watchdog` is read:

| Value | Result |
|---|---|
| missing file or key, or anything that isn't a number | default, 90 s |
| `0` | watchdog off |
| 1–29 | 30 s (the minimum) |
| above 3600 | 3600 s (the maximum) |

The host can change the period at runtime with op 11.

## How it is entered

Remote Hekate is a payload that sits next to an existing hekate install. It never
replaces it.

1. Add one entry to the **installed** `bootloader/hekate_ipl.ini`. Its 1-based
   autoboot index must be 1..8 so it can be selected through R2C:

   ```ini
   [Remote USB Control]
   payload=bootloader/payloads/Remote-Hekate.bin
   ```

2. To enter it, the host (for example the nx-agent Worker sysmodule) writes a hekate
   reboot-to-config (R2C) request: reason `SELF` with that entry's index. The request
   goes into the MAX77620 RTC alarm registers, in the layout of hekate's
   `rtc_reboot_reason_t`. Then the host reboots. The installed hekate reads the
   request and chainloads `Remote-Hekate.bin`.
3. Remote Hekate loads `bootloader/remote/nyx.bin`. When remote control is enabled
   (see below), the USB interface comes up once the GUI is running.
4. To leave, use op 7, the watchdog, or the `[Back to hekate]` entry in Nyx's
   *Launch* menu. All three hand over to the installed hekate.

Remote control is active only when both conditions hold:

- the console is a T210B01 (Mariko);
- `bootloader/remote/enable` exists.

Deleting that file gives a stock-behaving hekate. On first run, Remote Hekate also
creates `bootloader/remote/update.bin` from itself. That is the stock hekate
self-update logic, redirected.

## SD layout

| SD path | Source |
|---|---|
| `bootloader/payloads/Remote-Hekate.bin` | `build/Remote-Hekate.bin` |
| `bootloader/remote/nyx.bin` | `build/nyx.bin` |
| `bootloader/remote/enable` | `deploy-template/` (presence enables remote control) |
| `bootloader/remote/remote.ini` | `deploy-template/` (`watchdog=90`) |
| `bootloader/remote/nyx.ini` | `deploy-template/` (upstream Nyx defaults) |
| `bootloader/remote/hekate_ipl.ini` | `deploy-template/` |

The template `hekate_ipl.ini` contains:

- the upstream default `[config]`, with autoboot off;
- a single `[Back to hekate]` entry with `payload=bootloader/update.bin`. That entry
  chainloads the installed hekate's own `update.bin`, which every hekate install
  maintains.

The template `nyx.ini` holds Nyx's built-in defaults: `timeoffset=0`, `timedst=1`,
`bpmpclock=0`, and so on. It carries no per-device clock offset.

The installed `bootloader/hekate_ipl.ini`, `bootloader/update.bin`, `payload.bin`
and `bootloader/sys/nyx.bin` are never modified by Remote Hekate.
