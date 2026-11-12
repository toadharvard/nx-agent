# Shader packages (`Data\Shaders\shaderpackageNNN.sdp`)

The engine (`BSShaderManager`) opens `shaderpackage%03i.sdp`, chosen from the device caps, and looks up
precompiled D3D9 shaders by file name. The format is shared by Bethesda's Gamebryo-era games (Oblivion,
Fallout 3, New Vegas) and is documented by the modding community; this description is taken from those
public sources only, not from game files (we have none).

## Layout

All integers are little-endian `uint32`. No alignment or padding anywhere.

| offset | size | field | notes |
|---|---|---|---|
| 0 | 4 | magic | always `0x64` (100). UESP: "unknown"; the Oblivion notes call it a magic number / version |
| 4 | 4 | count | number of shader entries |
| 8 | 4 | data_size | file size - 12 |
| 12 | ... | entries | `count` entries, back to back |

Entry:

| size | field | notes |
|---|---|---|
| 256 | name | shader file name (e.g. `SLS2000.vso`, `SLS2000.pso`), NUL padded |
| 4 | size | bytecode length in bytes |
| size | bytecode | a complete D3D9 shader token stream: version token (`0xFFFEmmnn` vs, `0xFFFFmmnn` ps), optional comment tokens (the `CTAB` constant table, read by the engine through `D3DXGetShaderConstantTable`), instructions, end token `0x0000FFFF` |

Names ending in `.vso` are vertex shaders, `.pso` pixel shaders; the stage and shader model are taken from
the version token, never from the name.

## Reader (`translator/include/fnvnx_sdp.h`, `src/sdp.c`)

Strict, on a caller-owned buffer (entries point into it):

* magic must be 100; `data_size` must equal the file size - 12;
* `count` is bounded by `(size - 12) / 260` before anything is allocated;
* every entry header and body must fit; the entries must end exactly at the end of the file;
* names are copied into a 257-byte buffer, so a 256-character name without a terminator is accepted.

`fnvnx_sdp_write` produces the same format (used by the tests and by `sdp2dksh --pack`). Duplicate
bytecode across entries or packages is translated once (the cache is keyed by hash, not by name).

## Tests

`tests/s6b/s6b_unit.c` builds synthetic packages with the writer and checks the round trip (normal
names, a full 256-byte name, a zero-length entry, an empty package) and rejection of: short header, bad
magic, size mismatch, count overflow, truncated entry header, entry body past the end, trailing bytes.
`tests/s6b/run.py` packs the assembled test corpus into `shaderpackage001.sdp` / `002.sdp` and runs the
whole pipeline on them.

## Sources

* UESP, "SDP Files" (Oblivion Construction Set wiki): <https://cs.uesp.net/wiki/SDP_Files> - header of
  three DWORDs (0x64, number of shaders, data size = file size - 12), entries with a 0x100-byte name, a
  size and the compiled shader.
* lojack5, "oblivionsdp" notes (from reading OBMM's code): <https://github.com/lojack5-snippets/oblivionsdp/blob/master/README.md> -
  same layout; entries packed, no padding.
* Microsoft, "Shader Binary Format" / D3D9 token format (version token, comment token `0xFFFE` with
  length in bits 16-30, `CTAB` FOURCC, end token): Windows driver documentation,
  <https://learn.microsoft.com/en-us/windows-hardware/drivers/display/shader-code-format>.

Both community sources describe Oblivion's packages; that New Vegas uses the identical layout is an
assumption (same engine lineage, same file names and `BSShaderManager` entry point, RENDER.md), not yet
checked against a real file. The reader's strict checks will report any deviation on a real install (the tool
prints the error and the package name); confirm on a player's files at the first opportunity.
