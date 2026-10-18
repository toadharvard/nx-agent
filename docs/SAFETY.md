# Driving a real Switch safely

These rules come from bricking-free but painful lessons on real hardware. The MCP server sends them to the
agent as instructions; the skill repeats them.

1. **Start with `nx_status`.** States: `hos` (Worker answers), `hekate_remote` (Remote Hekate), `ums`
   (SD exposed to the host), `mtp` (DBI MTP replaced the USB interface), `absent`.
2. **Observe, act, observe.** Take a screenshot before and after input. Prefer small steps.
3. **Never retry a mutation reported as `uncertain`.** Check the result with read-only tools first
   (`nx_read_file`, `nx_status`, `nx_current_app`).
4. **Sleep only through the real POWER button or the HOS auto-sleep timer.** There is no power-button
   emulation: on a Switch Lite it powered the console off. Ask the human to press POWER.
5. **Keep `autohold` off for normal play.** With it the Worker holds a sleep lease from boot (auto-sleep off,
   wake on power-state change); on a Switch Lite this turned POWER-button sleep into reboots. Use sessions
   (`nx_session_start`) for temporary leases; `nxctl autohold off` restores the original sleep settings.
6. **Do not reboot within 60 s of boot.** Boot watchdogs (for example 4IFIR's alarm flag) treat quick reboots as
   crashes and start the next boot in a safe mode. The daemon refuses such reboots unless forced.
7. **R2C reaches hekate entries 1..8 only.** `reboot remote` needs `[Remote USB Control]` in `hekate_ipl.ini`.
8. **The Worker refuses writes outside its allow-list** (`/switch`, kips, keys, saves, emuMMC, Nintendo folders).
   Use `nx_sd_put_offline` (UMS) for `/switch` and kips; it closes the running game. Never try to bypass.
9. **USB-C role flips.** If the console starts powering the host instead of charging, USB data may drop
   (`absent`). Ask the human to replug or flip the cable; do not loop.
10. **After boot the HOME lock screen** needs the same button pressed three times (e.g. LEFT x3).
11. **Overlays.** Tesla/Ultrahand overlays appear in screenshots; input goes to the overlay while it is open.
    Do not close a homebrew menu (hbmenu in title takeover) while an overlay is closing; it can crash hbloader.
12. **Ask the human first** for: first install, `poweroff`, `nx_console_update install`, any change to
    `hekate_ipl.ini`, overclock or voltage settings, and anything that could lose save data.
13. **Keys, saves and NAND are off limits.** The console refuses them; do not copy them to the host.
