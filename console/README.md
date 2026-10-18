# Console modules

Two Atmosphère sysmodules that expose the Switch to the host over USB. Wire format, frame layout
and per-op payloads are specified in [docs/PROTOCOL.md](../docs/PROTOCOL.md); this file is an
overview. Build and test instructions: [BUILD.md](BUILD.md).

| Component | Title ID | Role |
|---|---|---|
| Rescue | `420000000000D06C` | Owns `usb:ds` (VID:PID 057e:3000, two vendor interfaces). Interface 1 is Rescue itself; interface 0 forwards each 4096-byte frame to Worker over TCP `127.0.0.1:22226`. Starts, stops and replaces Worker; boots via `boot2.flag`. |
| Worker | `420000000000D06B` | Input, screen, files, sleep, reboot and telemetry. Has no USB access of its own and is reachable only through Rescue or the SD mailbox (`sdmc:/config/usb-worker-mailbox`). |

Keeping USB in Rescue means a crashed or bad Worker can always be stopped and replaced from the host.

## Ops

Worker (interface 0):

| Ops | Purpose |
|---|---|
| 6, 9 | Version and capability/feature bits |
| 1-5 | Legacy debug socket relay |
| 7 | Stop Worker (EBUSY during a file transaction) |
| 16, 17, 18 | List a directory entry, read a file chunk, SHA-256 of a file |
| 32-35 | Transactional file write: begin, chunk, commit, abort |
| 40, 41, 42, 52 | Virtual controller: attach, press, detach, timed sequence |
| 43, 44 | Screenshot |
| 45, 48, 54 | Current application, short state, extended state |
| 46, 47, 53 | Reboot, toggle a module's `boot2.flag`, reboot into a bootloader entry (R2C) |
| 49, 50, 56, 57 | Sleep settings, sleep lease, autohold, lease restore target |
| 58-62 | Telemetry: RTC alarm registers, memory pools, per-process memory, power; charger HiZ (62) |

Rescue (interface 1):

| Ops | Purpose |
|---|---|
| 6, 9 | Version, launch readiness, boot-guard feature bit |
| 17, 18 | Read and hash its own files |
| 32-35 | Replace the Worker image (only while Worker is stopped; commit re-checks) |
| 60, 61, 62 | Query, stop, start Worker (synchronous start kept for old hosts) |
| 64, 65, 66 | Asynchronous Worker start: begin, poll, acknowledge |
| 70, 71, 72 | Boot guard: arm, confirm, status |

## File-write policy

Writes (ops 32-35) are an allow-list. Worker may write only:

- `*.ini` under `sdmc:/atmosphere/config/` and `sdmc:/config/4IFIR/`;
- `sdmc:/SaltySD/exceptions.txt`;
- `sdmc:/bootloader/hekate_ipl.ini`, `sdmc:/bootloader/payloads/Remote-Hekate.bin`, and files
  directly inside `sdmc:/bootloader/remote/`;
- `sdmc:/atmosphere/contents/<16 hex>/flags/boot2.flag`.

Paths containing `..`, `//`, `\`, control characters or a second `:` are rejected, as is anything
matching `.keys`, `/saves`, `/backup`, `/automatic_backups`, `/emummc` or `/nintendo`
(case-insensitive). Worker can never write either module's directory or `sdmc:/config/usb-rescue`,
so it cannot update or disable itself or Rescue. Reads are limited to `atmosphere`, `config`,
`SaltySD`, `switch` and `bootloader`.

Rescue may write only `atmosphere/contents/420000000000D06B/exefs.nsp` and
`config/usb-rescue/known-good.nsp`. Every transaction carries SHA-256 of the old and new content,
keeps a backup, and is committed by rename; a partial or corrupt upload never replaces the original.

## Safety notes

- op51 (power-button emulation) was removed and returns `ENOSYS`. Sleep comes from the real power
  button or the sleep lease (op50).
- op55 (wake alarm) is disabled: any non-zero interval returns `ENOSYS`, and `0` only disarms. On
  device the armed alarm left the console unable to wake and it needed a cold power-on.
- op53 (R2C) accepts only bootloader entries 1-8, the menu, or SD UMS, and requires the PMIC
  reboot path (`pmic=1`); the regular reboot clears the RTC alarm registers. The write is read
  back and cleared on failure.
- op62 (charger HiZ) and op46/op53 reboots are mutating; hosts must never replay them after a
  timeout, only verify with a read-only op.
- Rescue's boot guard records `boot2.flag` renames before a risky change; after two boots without
  confirmation it reverts them once.
