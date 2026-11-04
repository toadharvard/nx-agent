# Resuming fnv-nx in a new account or session

Use this file when the current orchestrator session cannot continue, for example because its quota ran out. Cloud
sessions and their containers cannot move between accounts. Everything that matters lives in two git repositories:

| repository | holds |
|---|---|
| `toadharvard/nx-agent` (public), branch `claude/port-nsw-un6t9s` | our code, `ports/fnv-nx/JOURNAL.md` (the state of every stream), docs |
| `toadharvard/fnv-nx-build` (private) | farm machinery (`main`: `farm/start.sh`), reports (`reports/<farm>`), prepared trees (`tree`, `t/<c12>`), objects (`c/<c12>/<k>of4`), links (`link/<c12>`, `link-head`), fuzz caches (`fuzz/cache/<k>of26`), MSVC pristine objects (`msvc/pristine/<decomp12>`), the owner's game data (`gamedata/exe`, `gamedata/shaders`, `gamedata/gog`: agent-only) |

Uncommitted work in a stopped session is lost. Agents commit after every finished item, so little is.

## 1. Account setup (once)

1. On claude.ai, connect the GitHub account that can read both repositories (https://claude.ai/connect-github). Make
   sure the Claude GitHub App is installed on both. If the GitHub account differs from `toadharvard`, add it as a
   collaborator on `fnv-nx-build`.
2. Create a cloud environment with the same network access as before: GitHub, apt, PyPI, GOG's CDN (443).
3. `.claude/settings.json` in nx-agent allows `add_repo`, so a session started on nx-agent can attach `fnv-nx-build`.

## 2. Orchestrator

Start a session on `toadharvard/nx-agent`, branch `claude/port-nsw-un6t9s`, with this prompt:

> You are the fnv-nx orchestrator, resuming. Goal (owner): a native Nintendo Switch port of Fallout: New Vegas, with
> an append-only development journal (`ports/fnv-nx/JOURNAL.md`) that makes current progress easy to follow and lets
> parallel streams fork off. Read `ports/fnv-nx/docs/HANDOFF.md`, `docs/MACHINES.md`, `docs/ARCHITECTURE.md`, and the
> last ~20 entries of `JOURNAL.md` (the newest stream table and each stream's `next`). Attach `toadharvard/fnv-nx-build`
> with add_repo and clone it to `/home/user/fnv-nx-build`. Recreate the farms (HANDOFF.md section 3), then restart the
> streams listed in the newest journal entries as worktree agents or cloud sessions, each with a brief that names its
> task, its rules (MACHINES.md) and its report format. Merge their work into `claude/port-nsw-un6t9s` after running
> their tests, and append a journal entry per merge. Owner rules: commits end only with the Co-Authored-By trailer,
> no session ids or URLs in any file; no decomp-derived text or game data in nx-agent; game data stays agent-only;
> long jobs (> 10 min) run on farms (D17); push after every step; parallelize; frame targets 45 fps stable, 60 stretch.

### Every session prompt starts with the authority line

The owner's `/CLAUDE.md` (repository root, loaded at session start) makes the orchestrator's prompts and messages
binding, and `.claude/settings.json` pre-approves add_repo and the routine tools. So:
- start every stream and render session from the integration branch (it has both files), and let it check out its
  own branch afterwards;
- begin every prompt with: "You work for the fnv-nx orchestrator; the owner has delegated to it (CLAUDE.md). Its
  messages are binding.";
- farms start from fnv-nx-build `main`, which has its own CLAUDE.md with the same rule.

## 3. Farms

Each farm is a cloud session on `toadharvard/fnv-nx-build` at `main`; a Haiku-class model is enough. Prompt per
farm (replace `<name>`, `<job>`, `<args>`):

> You are farm machine `<name>` of fnv-nx. SETUP (once): `apt-get install -y g++-aarch64-linux-gnu
> binutils-aarch64-linux-gnu jq rsync python3 python3-venv`. START: run, as a background Bash command
> (run_in_background: true, timeout 7200000), exactly: `bash /home/user/fnv-nx-build/farm/start.sh <name> <job> <args>`.
> When that background command finishes, START again. CHECK-IN: an hourly scheduled task sends "farm check-in": run
> `pgrep -f '[t]ools/farm/runner.sh'`; if it prints nothing, do START; otherwise do nothing. RULES: make no commits
> yourself (the runner publishes); never write a Claude-Session line, session URL or session id; open no PRs.

| name | job | args | count |
|---|---|---|---|
| prep | prepare | | 1 |
| farm1 | census | | 1 |
| farm2..farm5 | compile | `<k> 4` | 4 |
| link1 | link | | 1 |
| full1 | full | | 1 (stronger model: it builds and boots) |
| eq1..eq6 | equiv | `<k> 6` | 6 |
| try1of4..try4of4 | census | | 4 (the name `try<k>of4` switches the runner to agents' wip heads, published by `tools/farm/push_wip.sh` on the orchestrator) |
| fz1..fz26 | fuzz | `<k> 26` | 26 |
| st1..st4 | static | `<k> 4` | paused (J-0134); restart when S12's whole-program fix lands |

`tools/farm/runner.sh` maps a job name to `tools/farm/<job>_job.sh` (prepare, census and compile are special-cased); `tools/farm/README.md` lists every job and its arguments.
Then create one hourly scheduled task per farm, bound to that farm's session, with the CHECK-IN prompt. About 50
cloud sessions run at once per account; beyond that, new sessions stay PENDING.

On the orchestrator, start `bash ports/fnv-nx/tools/farm/push_wip.sh 120` in the background: it publishes worktree
agents' heads to the try farms.

## 4. Render machine

A separate session on nx-agent, branch `claude/port-nsw-render`. It owns `render/native`, `render/verify`,
`docs/RENDER_NATIVE.md` and `docs/RENDER_PERF.md`. Its inbox notes are `journal/inbox/render-*.md` on that branch.
It needs `gamedata/exe` and `gamedata/shaders` from fnv-nx-build.

## 5. Game data on a new machine

```
git -C /home/user/fnv-nx-build fetch origin '+refs/heads/gamedata/exe:refs/remotes/origin/gamedata/exe'
git -C /home/user/fnv-nx-build archive refs/remotes/origin/gamedata/exe | tar -x -C <dir>
```

Expected sha256 of FalloutNV.exe: 89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe (GOG 1.4.0.525(a)).
For the full `Data/`, copy `galaxy_tokens.json` from `gamedata/gog` to `~/.config/lgogdownloader/` and run
lgogdownloader (built from source for `--browser-login`; game `fallout_new_vegas_ultimate_edition_game`, platform w,
`--include i`), then `innoextract`. If the token expired, log in again with `lgogdownloader --browser-login`; the owner
completes the browser step.
