---
name: nx-agent
description: Control a CFW (Atmosphère) Nintendo Switch over USB with the nx-agent MCP server or the nxctl CLI - screenshots, buttons and sticks, SD file read/write, launching games, reboot into Remote Hekate or UMS, offline SD edits, sysmodule recovery. Use when the user asks to look at, play, configure or fix their Switch.
---

# nx-agent: driving a real Switch

The console is real hardware with the user's data on it. Work in small observed steps.

## Loop
1. `nx_status` first. States: `hos`, `hekate_remote`, `ums`, `mtp`, `absent`.
2. `nx_screenshot` (use `max_width=640` when detail is not needed), act, screenshot again.
3. Report what you saw, not what you expected.

## Hard rules
- After an `uncertain` error never repeat a mutation; verify with read-only tools first.
- No sleep or power-button emulation exists. For sleep tests ask the human to press POWER.
- No reboot within 60 s after boot (the daemon refuses unless forced).
- Whole SD without reboot or DBI: `nx_list_dir`, `nx_read_file`, `nx_write_file`, `nx_move_path`,
  `nx_delete_path`, `nx_make_dir` (Worker feature `file_tree`; the same tools work in DBI MTP mode). Keys, saves,
  Nintendo, emuMMC and the nx-agent modules are always refused. Results carry a `journal` path with the old files.
- Keys, saves, NAND, emuMMC: never read or copy them.
- `absent` with the cable plugged in: likely a USB-C role flip or sleep. Ask the human to replug or wake it.
- Ask the human before: first install, `poweroff`, `nx_console_update install`, editing `hekate_ipl.ini`,
  overclock/voltage changes, replacing boot files (`package3`, kips), anything that risks save data.

## Recipes
See `references/workflows.md` for: unlocking the HOME screen, launching a game, walking a character,
closing an app, editing an ini, full SD access over MTP, copying to `/switch`, recovering a dead Worker,
Remote Hekate navigation.

## CLI equivalents
`nxctl status | screen out.jpg | press A | seq steps.json | ls REMOTE | get REMOTE LOCAL | put REMOTE LOCAL |
mv SRC DST | rm [-r] REMOTE | mkdir [-p] REMOTE | reboot-to remote | hekate launch | ums-put REMOTE=LOCAL |
console status | events -f`
