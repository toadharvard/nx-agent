# Workflows

## Unlock after boot
The HOME lock screen asks for the same button three times: `nx_sequence` with three short `LEFT` presses
(150 ms apart). Screenshot to confirm the HOME menu.

## Launch a game
HOME menu: the first tile is the most recently played title. `nx_press A`, wait 5-10 s, screenshot. A user
picker may appear: `nx_press A` on the selected user. Games take 30-90 s to load; poll with screenshots.
Hold `R` while pressing A on a game tile to open the homebrew menu in title-takeover mode
(`nx_sequence [{"buttons":"R","ms":300},{"buttons":"R+A","ms":150},{"buttons":"R","ms":4000}]`).

## Walk a character
`nx_sequence` steps with empty buttons and stick values, e.g. `{"buttons":"","ms":1500,"lx":30000}` walks right,
`"ly":-30000` walks down. Screenshot after each move.

## Close the running app
`nx_press HOME`, wait 2 s, `nx_press X`, wait 1.5 s, `nx_press A`. Confirm with `nx_current_app`
(`0000000000000000`).

## Edit an ini safely
`nx_read_file` -> modify -> `nx_write_file` (allow-listed `.ini` only) -> `nx_read_file` to verify.
Keep the original text in the conversation so it can be restored.

## Files anywhere on the SD
In HOS (`nx_status` lists `file_tree` in `worker_features`) the file tools cover the whole SD except keys,
saves, Nintendo, emuMMC and the nx-agent modules: `nx_list_dir`, `nx_read_file`, `nx_write_file`,
`nx_move_path`, `nx_delete_path`, `nx_make_dir`. Writes are transactional with SHA-256 readback; replaced and
deleted files are copied to the host journal (`journal` in the result). Example, disable a kip:
`nx_move_path source=sdmc:/atmosphere/kips/loader.kip dest=sdmc:/atmosphere/kips/loader.kip.disabled`.
Files up to 4 GiB - 1 stream over USB in large blocks when `worker_features` lists `bulk_stream` (Rescue
feature 2); otherwise writes go in 4 KiB frames (slow, 256 MiB limit). Large transfers hold a sleep lease, so
the console does not fall asleep mid-copy. Kips and sysmodules change on the next boot. If `file_tree` is
missing, update the Worker with
`nx_console_update` (ask the human) or use one of the paths below.

## Files over DBI MTP
While DBI runs "Run MTP responder", `nx_status` reports `mtp` and the same tools go through MTP; buttons still
work through the Worker mailbox. On macOS the daemon releases the interface from Image Capture (`ptpcamerad`);
another owner is reported as `busy`. Host setup once: `brew install libmtp && nxctl setup mtp`.

## Copy files offline (UMS)
`nx_sd_put_offline files=[{"local": "/abs/path", "remote": "switch/.packages/x/config.ini"}]` returns a job id.
Poll `nx_job_status` until `done`. The console reboots into Remote Hekate, exposes the SD over UMS, copies and
verifies sha256, ejects, and boots HOS. Unlock the HOME screen afterwards.

## Worker not answering
`nx_status` shows `worker_down`: wait 10 s (the daemon restarts it through Rescue), then `nx_status` again.
If it stays down: `nx_console_update action=status`, then ask the human before `rollback`.

## Remote Hekate
`nx_reboot target=remote` -> state `hekate_remote`. `nx_screenshot` shows the Nyx GUI (1280x720 PNG).
`nx_hekate_tap x y` to press buttons. `nx_hekate_action launch` continues booting HOS. The watchdog (default 90 s)
boots HOS automatically if the host goes silent.
