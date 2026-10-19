# nx-agent USB control protocol v7

Single source of truth for Worker v7, Rescue v7, Remote Hekate v2 and the host daemon `nxagentd`.
Host clients live in `host/src/nxagent/proto/` (`worker.py`, `rescue.py`, `hekate.py`, `transport.py`).

## Transport (unchanged)

USB 057e:3000, two vendor interfaces (class 0xff, subclass 0x52):
- iface 0 protocol 0: Rescue forwards each 4096-byte frame to Worker over TCP 127.0.0.1:22226.
  Rescue answers bridge errors itself (devkitPro newlib numbers): ECONNREFUSED(111) means Worker
  did not run the request; ECONNRESET(104) or ETIMEDOUT(116) may arrive after the request reached
  Worker, so a mutation then needs read-only verification.
- iface 1 protocol 1: Rescue itself.

Frame (both directions, exactly 4096 bytes, little endian):
- request: `u32 magic=0x31425255, u32 op, u32 len (<=4080), u32 0, u8 data[4080]`
- reply:   `u32 magic, u32 error (guest errno or HOS Result), u32 size, u32 retry_hint, u8 data[4080]`

Host rules:
- A transfer timeout makes the stream state uncertain. The host drains late replies
  (read-only, `UsbSocket.drain_pending_replies`) and reconnects before any further command.
- READ-ONLY ops may be retried automatically after drain + reconnect. MUTATING ops are never
  replayed; their outcome is determined by a read-only check.

## Worker v7 (iface 0)

`op6` -> `u32[4] {7, 0xfffffffe, 0, command_count}` (version 7).
`op9` -> `u32[6] {7, op_mask_legacy=0x3f, write_limit, 1000, transaction_pending, features}`
(`write_limit` 256 MiB with `file_tree`, 16 MiB before; larger files go through the Rescue bulk ops).
features bits: `1` rail pair, `2` sequences (op52), `4` R2C (op53), `8` wake alarm (op55),
`16` wakes-at-power-change in sleep lease, `32` extended state (op54), `64` autohold, `128` power telemetry,
`256` file tree (ops 36-39 and the whole-SD policy below).

Unchanged ops: 1-5 (debug relay), 7, 8, 16, 17, 18, 32-35 (transactional file write),
43/44 (screenshot), 45 (application pid/title), 46 (`"REBOOT"`), 47 (boot2.flag rename),
48 (`u32[4] pad_ready, release_pending, last_control_result, reboot_pending` — exactly 16 bytes),
49, 50, 51.

### Controller
- `op40` attach, data len 0 or 1:
  - `0` (default, also what old hosts send for "pro") and `1` (old "rail"): **rail pair** —
    JoyRight1 + JoyLeft2, `HidNpadInterfaceType_Rail`, merged into the built-in handheld pad.
  - `2`: dedicated FullKey3 (Bluetooth interface). Worker records npads with
    `hidGetNpadStyleSet(i)!=0` before attaching, then polls every 10 ms up to 1 s for a new npad.
    If none appears or the first neutral `SetHdlsState` fails, it detaches and falls back to the
    rail pair. Reply then reports the mode actually active.
  - Reply: `u32 mode_active (0 rail pair, 2 fullkey), u32 fullkey_result (0 or failure Result)`.
- `op41` press (unchanged layout, 28 bytes): `u64 buttons, s32 lx, ly, rx, ry, u32 ms (1..1000)`.
  Rail-pair routing — right device: A,B,X,Y,RSTICK,R,ZR,PLUS,HOME(bit18), right stick;
  left device: LSTICK,L,ZL,MINUS,LEFT,UP,RIGHT,DOWN,CAPTURE(bit19), left stick.
  Buttons outside bits 0..19 -> EINVAL. Attaches implicitly if needed.
- `op42` detach (cancels any running sequence).
- `op52` sequence: `u32 count (1..64)` then `count` steps of 28 bytes
  `{u64 buttons, s32 lx, ly, rx, ry, u32 ms (1..30000)}`; total duration <= 60 000 ms.
  Each step sets the state for `ms`, steps run back to back from the Worker tick, a final neutral
  state is applied at the end. `count=0` cancels a running sequence. EBUSY if a sequence runs.
  op41 while a sequence runs -> EBUSY.
- Pad lifetime: the pad stays attached until op42, until 600 s pass without any controller op
  (40/41/52) or sleep-lease op50, or until Worker shutdown. No 10 s idle detach.
- On any `hiddbgSetHdlsState` failure: detach, re-attach once, re-apply; if it still fails,
  return the Result and leave the pad detached.

### Extended state
- `op54` (len 0) -> `u32[12] {1 (layout), mode_active, pad_ready, seq_active, seq_index,
  seq_count, last_control_result, pad_idle_ms_left, lease_active, wake_alarm_s,
  last_wake_reason (bpc cmd 2, 0xffffffff unknown), r2c_pending}`.

### Sleep
- `op50 u32 seconds` (15..600 arm/renew, 0 restore) as before; additionally while armed the
  sleep flags get `WakesAtPowerStateChange` (bit 1). The journal `sleep-lease.bin` keeps the
  full original `SetSysSleepSettings` and restore puts it back exactly.
- `op55 u32 seconds` (0 disable, 60..3600): while a lease is active and the console is about to
  sleep (PSC ReadySleep), arm a `time:al` WakeupAlarm for that interval. Experimental: failures
  are recorded in `last_control_result`, never block sleep.
  Field result: with the alarm armed a Switch Lite did not come back from sleep and needed a cold
  power-on (hekate `autohosoff=1` powers off on RTC-alarm power-on). The shipped Worker therefore
  rejects any non-zero interval; op55 only disarms.
- `op56 u8` autohold (1 on / 0 off): flag `sdmc:/config/usb-sidecar/autohold`; while set, Worker holds the
  sleep lease from its first tick on every start and re-arms it within 1 s if anything releases it.
- `op57 SetSysSleepSettings(12 bytes)`: replace the restore target (journal) with a validated value.

### R2C reboot
- `op53` data exactly 6 bytes: `"R2C" , u8 reason, u8 idx, u8 pmic`. `pmic=1` reboots through
  `splSetConfig(65001, 4)` (secmon PMIC reboot) and is REQUIRED for R2C: `pmic=0` uses the bpc reboot,
  which wipes the RTC alarm registers, so hekate silently performs its normal autoboot.
  - reason 1 SELF: idx 1..8 = hekate_ipl.ini boot entry (autoboot_list=0).
  - reason 2 MENU: idx must be 0.
  - reason 3 UMS: idx 0 = SD card (hekate ums_idx 0).
  Encoding (hekate `rtc_rr_decoded_t`): `v = reason | idx<<4` for SELF, `v = reason | idx<<9`
  for UMS, `v = reason` for MENU; `val1 = v & 0x3f`, `val2 = (v >> 6) & 0x3f`.
  Write sequence on `I2cDevice_Max77620Rtc`: UPDATE0(0x04)=0x10, sleep 16 ms; for regs
  0x0E..0x1B clear bit 7; 0x13=val1; 0x1A=val2; 0x11=0x77; 0x18=0x77; UPDATE0=0x01; sleep 16 ms;
  read back 0x11/0x18/0x13/0x1A (after UPDATE0=0x10) and verify. Then the same delayed reboot as
  op46 (1 s). EBUSY if a file transaction or reboot is pending.

### Mailbox (`sdmc:/config/usb-worker-mailbox`) allowed ops
40 (len<=1), 41 (28), 42/45/48/49/54 (0), 50 (4), 52 (4+28*n; the mailbox data area is
80 bytes, so count<=2). op40 while a sequence runs -> EBUSY.

### File policy (feature `file_tree`)
Paths are `sdmc:/...` without `..`, `//`, `\\`, control characters or a second `:`. Compared case-insensitively:
- never readable or writable: anything containing `.keys`, `/saves`, `/nintendo`, `/emummc`, `/backup` or
  `/automatic_backups`;
- readable only: `sdmc:/config/usb-sidecar` (transaction area);
- never writable: `sdmc:/config/usb-rescue`, `sdmc:/atmosphere/contents/420000000000d06b` and `...d06c`
  (the Worker and Rescue images), the SD root itself and paths ending in `/`;
- everything else on the SD is readable and writable (ops 16-18, 32-35 up to 256 MiB, 36-39).
Before `file_tree` the Worker read only `atmosphere`, `config`, `switch`, `bootloader`, `SaltySD` and wrote a
short allow-list (`*.ini` in `atmosphere/config` and `config/4IFIR`, `hekate_ipl.ini`, Remote-Hekate files,
`boot2.flag`).

### File tree ops (feature `file_tree`, mutating)
- `op36 from\0to` rename or move a file or directory; `EEXIST` if `to` exists (no replace).
- `op37 path` delete a regular file (`EISDIR` for a directory).
- `op38 path` delete an empty directory (`ENOTEMPTY` otherwise; recursion is done by the host).
- `op39 path` create a directory (`EEXIST` if present).
36-38 refuse (`EACCES`) any path that is, or contains, a protected directory, so `sdmc:/config` or
`sdmc:/atmosphere/contents` cannot be renamed or removed with the modules inside. 36-38 return `EBUSY` while a
write transaction is pending. The host checks every step with a listing, copies deleted files to its journal
first and never resends after a lost reply.

## Rescue v7 (iface 1)

`op6` -> `u32[4] {7, 0x52534355, 1, 0}`; `op9` -> `u32[6] {7, launch_ready, 16 MiB, 0, pending,
features}` features bit `1` boot guard, `4` direct bulk file transfers (ops 80-81).

### Bulk file transfers (Rescue feature `4`)
Large files move like in DBI or nxdumptool: raw bytes in 128 KiB USB transfers straight between the Rescue
channel and the SD card, without the Worker or its TCP bridge. Paths follow the Worker `file_tree` policy
(same rules, checked by a console test). A reply whose reserved word 3 is `STREAM_MAGIC` (`0x4d525453`) carries
`u32 direction, u32 0, u64 length` and is followed by exactly `length` raw bytes on interface 1.
- `op80` (mutating) payload as `op32` (`u8 exists, u64 size, old_sha256[32], new_sha256[32], path`), up to
  4 GiB - 1. Opens a journaled transaction (`config/usb-rescue/bulk-pending.bin`, temp `bulk.tmp`, which grows
  as the stream is written), replies `STREAM_IN`; the host sends `size` bytes, then reads one final frame whose
  error word is the commit result: SHA-256 computed on the fly must match, the original must still match
  `old_sha256`, then the target is replaced. `EBUSY` while another bulk or module transaction is open. A failed
  or interrupted transfer leaves the original; Rescue repairs a half-done rename at boot.
- `op81 path` (read-only) replies `STREAM_OUT` plus `sha256[32]`, then sends `size` bytes; the host checks the
  hash. After a local read error the remaining bytes are zero-filled.

Before replying, the console hashes the whole file (`op81`) or hashes the old file (`op80`), so the host sizes
the reply timeout by the file: `op81` 600 s, `op80` 30 s plus 1 ms per 10 kB of the new and old file. A fixed
short timeout declares a large transfer lost while the console goes on to wait for the stream.

Rescue always consumes or sends exactly `length` bytes so frames stay in step; a host that fails mid-upload pads
with zeros (the commit then fails on the hash). An upload that receives no data for 30 s is rolled back at once
(temp file closed and removed, journal cleared, stage `bulk-idle-rollback` in `status.txt`, final error
`ETIMEDOUT`), but the remaining bytes are still consumed: usbComms cannot cancel the pending read.

Recovering a Rescue channel left inside an upload stream (status shows `uncertain_streams: ["rescue"]`, Rescue
commands time out, and on builds without the idle rollback `config/usb-rescue` lists with `EIO` because
`bulk.tmp` is open):
1. `nxctl daemon-stop`; make sure no host transfer is running.
2. Open interface 1 (`UsbSocket(interface_number=1)`) and read late frames until 1 s of silence. A frame with
   error 0 and word 3 = `STREAM_MAGIC` is the late `op80` reply: Rescue waits for `length` (u64 at offset 24)
   bytes. If there is none, send `op6`: a normal reply means Rescue is idle and nothing is pending; no reply
   means Rescue is inside the stream and took those 4096 bytes as data.
3. Send the missing bytes as zeros on the bulk OUT endpoint. Every 4096-byte frame sent after the announcement
   (including probes by the daemon while it reconnected) already counted as data.
4. Read the final frame: error `EBADMSG` (77) means the hash check failed and the transaction rolled back; the
   original file is untouched.
5. `nxagentd --background`; `nxctl status` must show no `uncertain_streams`, and `config/usb-rescue` lists again. The daemon uses these ops for `get`/`put` whenever Rescue
reports feature 4 (`"bulk_stream": false` in its config turns them off) and holds a sleep lease from 8 MiB.
Existing ops 4, 17, 18, 32-35, 60-62, 64-66 unchanged.

Boot guard journal `sdmc:/config/usb-rescue/boot-guard.bin`:
- `op70` arm: `u32 count (1..8)` then entries of 400 bytes `{char from[200], char to[200]}`
  (NUL-padded). Each entry means: "to revert, rename `from` -> `to`". Both paths must be
  `sdmc:/atmosphere/contents/<16 hex>/flags/boot2.flag` or the same with `.sidecar-disabled`
  appended, and they must differ only by that suffix. Arming writes `boots=0`. EEXIST if armed.
- `op71` confirm: deletes the journal (healthy boot). ENOENT if not armed.
- `op72` status -> `u32[6] {armed, boots, threshold=2, entries, last_revert_result,
  last_revert_count}`; last_revert_* persisted in `sdmc:/config/usb-rescue/boot-guard.last`.
- At Rescue start (before starting Worker): if armed, `boots++` (persisted with fsync). If
  `boots >= 2`, apply every revert entry whose `from` exists and `to` does not, record the result,
  delete the journal.

## Remote Hekate v2 (USB 11ec:a7e1)

64-byte requests `u32[16] {magic=0x5258594e, seq, op, args...}`; 4096-byte replies
`u32[8] {magic, seq, error, size, op_echo, generation, 0, 0}` + data. Ops 1-5 unchanged.
- `op6` entries -> text, one line per boot entry of `bootloader/hekate_ipl.ini` in order
  `"<idx>\t<name>\n"` (idx 1-based, same numbering hekate uses for autoboot).
- `op7` continue boot: launch the original hekate (`bootloader/update.bin`) as an update payload
  (b_cfg transferred, `EMC_HEKA_UPD` set) so it skips its own update check and performs its normal
  autoboot. Reply is sent before launching (after a 200 ms delay).
- `op8` UMS SD: reply first, then run the SD UMS gadget; when the host ejects, remote resumes.
- `op9` reboot (hekate `power_set_state(REBOOT_BYPASS_FUSES)` equivalent used by Nyx "Reboot"),
  `op10` power off. Reply first.
- `op11` watchdog: `arg0` seconds (0 disables for this session, 30..3600 re-arms).
- `op12` status -> `u32[4] {2 (version), watchdog_s_left, watchdog_enabled, host_commands}`.
Watchdog: from `bootloader/remote/remote.ini` key `watchdog=<seconds>` (default 90, 0 off).
If no valid request arrives within that time after Nyx start, op7 runs automatically.
Any valid request restarts the countdown.

## Worker diagnostics (ops 58-62)

- `op58` dump MAX77620 RTC registers (read-only).
- `op59` memory pool info, `op60` per-process memory via Atmosphère pm:dmnt 65000 (read-only).
- `op61` power telemetry: MAX17050 fuel gauge and BQ24193 charger registers plus USB-C role-guard counters.
- `op62 u8` charger high-impedance mode on/off (BQ24193 REG00 bit 7). Mutating; CLI-only, never exposed over MCP.
- `op51` (HOME/POWER button emulation through hiddbg) is removed: on a Switch Lite it powered the console off
  instead of sleeping. It returns ENOSYS.

## DBI MTP file access (USB 057e:201d)

While DBI runs its MTP responder the sidecar interface is gone. The host talks to the console only through
`mtp_fs` (`host/src/nxagent/data/native/mtp_fs.c`, built by `nxctl setup mtp`, needs libmtp). One request per
line on stdin, fields separated by TAB; one JSON reply per line on stdout. The first line after start is
`{"ok":true,"ready":true}` or `{"ok":false,"open":false,...}`.

| Request | Meaning | Reply |
|---|---|---|
| `S` | storages | `storages: [{id, description, access, free, max}]` |
| `C` | capabilities | `edit, move, copy` |
| `L storage parent` | children (`parent` 4294967295 = storage root) | `entries: [{id, parent, storage, size, folder, name}]` |
| `I id` | metadata | `object` |
| `G id local` | download | `ok` |
| `P storage parent name local` | upload a new file | `id` |
| `W id local` | overwrite in place (edit extension, then truncate) | `ok` |
| `R id name` | rename | `ok` |
| `V id storage parent` | move | `ok` |
| `D id` | delete | `ok` |
| `M storage parent name` | create folder | `id` |

Errors: `{"ok":false,"error":"...","stack":["libmtp error text", ...]}`. The host uses only the storage whose
description is `N: SD Card`. Paths are `sdmc:/...`; `..`, the `Nintendo` and `emuMMC` top folders and `*.keys`
are refused before any request. Overwrite: upload `.<name>.nxagent-new-<ns>`, verify SHA-256, rename the
original to `.<name>.nxagent-old-<ns>`, rename the new file into place, delete the old one. If the device
refuses renames, the original is deleted and the file uploaded directly (the original is already journaled).
The Worker mailbox (`status.json`, `request.bin`) uses the same helper.

## Host daemon commands (JSON lines over the Unix socket)

Request `{"cmd": "...", "args": {...}}`, reply `{"ok": true, "result": ...}` or
`{"ok": false, "kind": "absent|worker_down|uncertain|device_error|busy|invalid", "error": "..."}`.
Read-only commands are retried after a stream reset; mutations are never replayed.
Besides the commands that mirror the CLI (`status`, `screen`, `press`, `seq`, `get`, `put`, `ls`, `app`, `reboot`,
`reboot_to`, `hekate.*`, `session.*`, `guard.*`, `bootflag`, `logs.collect`, `mtp.status`):

- `wait_state {state, timeout}` - poll until the console is in `hos|hekate_remote|mtp|ums|absent`.
- `ls`, `get`, `put`, `mv {source, dest}`, `rm {remote, recursive}` and `mkdir {remote, parents}` use the Worker
  in `hos` (tree ops need `file_tree`; `get`/`put` use the Rescue bulk ops when available) and `mtp_fs` in `mtp`.
- `ums.put {files: [{remote, local}], hooks, wait}` - Remote Hekate UMS copy job, returns `job_id`.
- `job.status {job_id?}` - progress of long jobs.
- `console.update {action: status|install|promote|rollback, ...}` - Worker maintenance through Rescue.
- `reboot` / `reboot_to` accept `force`; without it they are refused until HOS has been up
  `min_uptime_before_reboot_s` (default 60) as observed by the daemon.
