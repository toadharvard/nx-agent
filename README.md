# nx-agent

Let an AI agent (or you) drive a Nintendo Switch running Atmosphère CFW over a USB cable:
see the screen, press buttons, read and write SD files, launch games, reboot into hekate, edit the SD card
offline, and recover its own sysmodule - all through an MCP server, a CLI, or an agent skill.

```
agent ──MCP──▶ nxagent-mcp ──┐
you ──────────▶ nxctl ───────┼─unix socket─▶ nxagentd ──USB──▶ Rescue ─▶ Worker   (HOS)
                             │                         └─USB──▶ Remote Hekate      (bootloader)
```

| Part | Where | What it does |
|---|---|---|
| `nxagentd` | host | The only USB owner. Retries reads, never replays writes, tracks console state. |
| `nxctl` | host | CLI over the daemon. |
| `nxagent-mcp` | host | MCP server (stdio) with read-only / interactive / full profiles. |
| Worker sysmodule | console | Screenshots, virtual controller, SD file access with an allow-list, app info, R2C reboot. |
| Rescue sysmodule | console | Owns USB, starts/stops/replaces Worker, boot guard for risky flag changes. |
| Remote Hekate | console | hekate 6.5.2 with a USB remote: screen, touch, boot entries, UMS, launch HOS. |
| Skill | agent | `skill/nx-agent/SKILL.md`: the operating rules learned on real hardware. |

Tested on a Switch Lite, HOS 22.1, Atmosphère 1.11.x, hekate 6.5.2, macOS host. Linux host support is
implemented but not yet verified on hardware.

## Install

**Host** (macOS or Linux, Python 3.11+, libusb):

```sh
brew install libusb            # Linux: apt install libusb-1.0-0 (and install host/packaging/99-nx-agent.rules)
pipx install './host[mcp]'     # or: uv tool install './host[mcp]'
nxctl skill install            # copies the skill to ~/.claude/skills/nx-agent
```

**Console** (once, with the SD card in a reader or exposed by hekate UMS):

```sh
nxctl install --sd /Volumes/SWITCH --bundle nx-agent-sd-0.1.0.zip          # dry run: shows the plan
nxctl install --sd /Volumes/SWITCH --bundle nx-agent-sd-0.1.0.zip --apply  # writes, backs up, adds the hekate entry
```

The installer adds `[Remote USB Control]` to `bootloader/hekate_ipl.ini` without touching `[config]` or
`autoboot`, refuses to overwrite foreign sysmodules that use the same title IDs, and backs up every replaced file.

**Agent** (Claude Code example, `.mcp.json` in your project):

```json
{ "mcpServers": { "nx-agent": { "command": "nxagent-mcp", "args": ["--profile", "interactive"] } } }
```

Use `--profile full` only when you want the agent to reboot, write files or update sysmodules.

**ChatGPT** (remote MCP over HTTPS). ChatGPT connects from the internet, so expose the local HTTP endpoint
through a tunnel and keep the path secret - with "No authentication" the URL is the only key:

```sh
TOKEN=$(openssl rand -hex 16)
cloudflared tunnel --url http://127.0.0.1:8765          # prints https://<name>.trycloudflare.com
nxagent-mcp --profile interactive --transport streamable-http --port 8765 \
  --path /mcp-$TOKEN --allowed-host <name>.trycloudflare.com
```

In ChatGPT: Settings -> Apps & Connectors -> Advanced -> Developer mode, then add a connector with URL
`https://<name>.trycloudflare.com/mcp-$TOKEN`. Prefer `--profile readonly` or `interactive` over the network;
anyone with the URL controls the console within that profile. Stop the tunnel when you are done.

## Use

```sh
nxctl status
nxctl screen shot.jpg
nxctl press HOME
nxctl get sdmc:/atmosphere/config/system_settings.ini ./system_settings.ini
nxctl reboot-to remote && nxctl hekate launch
nxctl ums-put switch/.packages/x/config.ini=./config.ini --wait
nxctl events -f
```

Docs: [Safety](docs/SAFETY.md) · [Protocol](docs/PROTOCOL.md) · [Console build](console/BUILD.md) ·
[Remote Hekate](console/remote-hekate/README.md)

## Safety

The console enforces a file allow-list: keys, saves, emuMMC and Nintendo folders are never readable or
writable over USB. Power-button emulation is deliberately absent. Read [docs/SAFETY.md](docs/SAFETY.md) before
giving an agent the `full` profile. Anyone who can reach the daemon socket controls the console; it is created
with mode 0600 in a per-user runtime directory.

## License

MIT, except `console/remote-hekate/` (GPLv2, derived from [hekate](https://github.com/CTCaer/hekate) by CTCaer).
Not affiliated with Nintendo.
