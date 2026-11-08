### J-XXXX | 2026-11-08 | stream: R4 + S4-platform (claude/fnv-recon4) -> two Sonnet workers ahead of the boot frontier | status: wip | parent: recon4-016

Owner order via the orchestrator: Sonnet workers on the plat classes, with every result reviewed before a merge. I
ran two, each in its own worktree. Both used the owner's data in the container only (scratchpad); reports carry
counts only. I re-ran both checks myself.

* **#4 archive and plugin opens** (worker 1). A fake card with the owner's real Data file names (1-byte files) and
  the platform file layer (os_posix + platform/win32 + crt), rooted at the card.
  - Files: the 6 archives of Fallout_default.ini's SArchiveList, plus Update.bsa, FalloutNV.esm and
    FalloutNV_lang.esp.
  - Each through CreateFileA, GetFileAttributesA, fopen and _open; in 5 forms (Data\x, .\Data\x,
    C:\FalloutNV\Data\x, c:/falloutnv/data/x, \FalloutNV\Data\x); in 3 spellings (as in the INI, lower, upper).
  - Result: **540 of 540 open.** FindFirstFileA Data\*.bsa finds 7, *.esm 1. A deliberately absent .esm fails with
    ERROR_FILE_NOT_FOUND.
  - My re-run against claude/fnv-recon4's tree: 540 of 540.
  - Not covered: real sizes and contents, and the game's own parsing of the list (e.g. the INI's double space
    before one name).
* **#6 audio decode** (worker 2). The port's own entry points on the owner's audio:
  - mp3: all 199 Music files, through the dshow graph to EC_COMPLETE.
  - ogg: all 2,232 in Sound.bsa, plus a 1-in-20 sample (2,639) across Voices1.bsa. fnvnx_ov_open_callbacks and
    ov_read to the end; the byte count matches pcm_total.
  - wav: 4,237 files, through DirectSound buffers.
  - Result: **9,307 of 9,307 decode fully, 0 failures.** My tally of its per-file results agrees. Garbage files do
    fail (ogg -132, mp3 VFW_E_CANNOT_RENDER).
  - Findings:
    - a truncated MP3 decodes without an error (it plays about 0.07 s), so #6 would not see a damaged music file;
    - the s7 host build doesn't define FNVNX_HAVE_VORBIS, so the host tests never run the libvorbis path. The
      farm's guest does link libvorbis (ov_open_callbacks and vorbis_synthesis are in d1ed15e74620's ELF).
* **repo changes** - none: neither worker found a platform bug.
* **not done** - input (no real data to test against) and the NRO checklist's memory.peak (needs a boot that reaches
  the first cell). The Wine baseline was done earlier (recon4-016).
