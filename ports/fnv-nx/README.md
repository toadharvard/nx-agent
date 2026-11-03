# fnv-nx

Work in progress: a native Nintendo Switch build of FNV PC 1.4.0.525 from the
[fnv_decomp](https://github.com/momusdigital/fnv_decomp) reconstruction. Nothing playable yet.

* Design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
* Progress and parallel work streams: [`JOURNAL.md`](JOURNAL.md) (append-only); `tools/journal.sh status`

This directory holds only our own code. It contains no part of the game or of the decomp, and builds need the
player's own legally owned copy of the game.

## Layout

| Path | What |
|---|---|
| `tools/fetch.sh` | Checks out the pinned decomp, sse2neon and libnx into `ext/` (git-ignored). |
| `tools/prepare.py` | Derives the build tree from the decomp with mechanical rewrites (rule ids in the journal). |
| `tools/census.sh` | Compiles every unit with `-fsyntax-only` for AArch64 ILP32 and summarizes the errors. |
| `tools/journal.sh` | Reads the journal: stream status, open forks, metrics, next entry id. |
| `shim/include/` | Force-included MSVC dialect header and the Win32/D3D9 declaration subset. |

## Quick start (host: Linux)

```sh
apt install g++-aarch64-linux-gnu jq          # stand-in for devkitA64 until S5 lands
tools/fetch.sh                                 # -> ext/
tools/prepare.py ext/fnv_decomp build/tree     # derived tree, local only
SSE2NEON=ext/sse2neon tools/census.sh build/tree build/census
cat build/census/summary.txt
```
