# tools/equiv - MSVC 2008 x86 builds of the decomp (stream S11)

`equiv.py` builds decomp units with the compilers the decomp matches (Visual C++ 2008 SP1 / RTM, under Wine) and
compares pristine vs prepared trees per function (docs/EQUIVALENCE.md). Other streams can use the same compilers as
the x86 oracle. Nothing here contains Microsoft or decomp files; objects and results stay in scratch directories.

## The compilers on the dev container

Unpacked from Microsoft's own ISOs exactly as the decomp README ("Compilers without Windows") describes; Authenticode
signatures of the installers and of `cl.exe`, `c1.dll`, `c1xx.dll`, `c2.dll` verify (osslsigncode, Microsoft Code
Signing PCA).

| | |
|---|---|
| SP1, cl 15.00.30729.01 | `FNV_MSVC9_ROOT=$S11/msvc/sp1` (VC/ + SDK/ v6.0A) |
| RTM, cl 15.00.21022.08 | `FNV_MSVC9_RTM_ROOT=$S11/msvc/rtm` (VC/ + the same SDK/) |
| Wine 9.0 (Ubuntu 24.04 `wine`, `wine32:i386`) prefix | `FNV_MSVC9_PREFIX=$S11/wineprefix` (win32) |
| VC 2005 SP1 (`vc8`, 1 unit) | not installed |

`$S11` = `/tmp/claude-0/-home-user-nx-agent/9349aed9-849c-5755-9ca4-b338a8b8ed63/scratchpad/s11` (stable; read-only
for other streams; a stream that wants its own Wine state points `FNV_MSVC9_PREFIX` at an empty directory of its
own, it is created on first use).

| Installer (download.microsoft.com) | SHA-256 |
|---|---|
| `VS2008ExpressWithSP1ENUX1504728.iso` (784,902,144 bytes) | `6d3b5eedfa461969c5d645871b53002d162a2e238486900d2fdf71774d53fb91` |
| `VS2008ExpressENUX1397868.iso` (938,108,928 bytes) | `632318ef0df5bad58fcb99852bd251243610e7a4d84213c45b4f693605a13ead` |

On another machine `tools/equiv/setup_msvc.sh <dest>` does all of this (apt, download, checksum, unpack, signature
check, Wine prefix) and prints the three variables; `tools/farm/equiv_job.sh` runs the whole gate for one shard of
units and publishes the pristine objects to the private artifacts repository (`msvc/pristine/<decomp12>`: `obj/`
and `meta.json`; the job fingerprints the objects itself, `rows.jsonl` there is optional).

## One unit to a COFF object (the decomp's own wrapper and flags)

    export FNV_MSVC9_ROOT=$S11/msvc/sp1 FNV_MSVC9_RTM_ROOT=$S11/msvc/rtm FNV_MSVC9_PREFIX=$S11/wineprefix
    cd <fnv_decomp>
    python3 tools/msvc.py --compiler <unit.compiler> /c /nologo <unit.flags...> /I<fnv_decomp>/include \
        <fnv_decomp>/<unit.source> /Fo<out>.obj

`unit` is the entry of `build/units.json` (`compiler` is `sp1` or `rtm`; `flags` e.g. `/MT /O2 /GS /EHsc /Gy`).
Absolute paths are converted to `Z:\...` by `msvc.py`. Add `/d1reportSingleClassLayoutNAME` or
`/d1reportAllClassLayout` for MSVC's class layouts on stdout. Disassembly: `objdump -d -r -M intel <out>.obj`
(binutils reads i386 COFF).

The same through this tool, with per-function fingerprints (compare_builds logic) and the objects kept:

    python3 tools/equiv/equiv.py build <fnv_decomp> <fnv_decomp> --out fp.jsonl --units 0,3 --objdir objs/

Sanity at decomp `87eb9373`: 2,987 of 2,988 units compile (the `vc8` unit needs VC 2005); every one of the 68,456
recipe functions in them is present in its object with exactly the recipe size (one more with trailing padding).

## Commands

    equiv.py rules                                  every prepare rule with its class (neutral / layout / platform)
    equiv.py prepare DECOMP OUT --rules SPEC        prepare.py with an allow-list ('neutral', 'neutral,-EXC1', 'TAIL1')
    equiv.py build DECOMP TREE --out FP.jsonl [--units all|i,j|FILE|shard:K/N] [--objdir D] [--layout] [--eh-off]
    equiv.py refp DECOMP FP.jsonl OBJDIR --out NEW.jsonl [--tree T] [--units U]   fingerprint kept objects again
                                                    (FP.jsonl '-': rows from the objects alone, no object = failed)
    equiv.py compare A.jsonl B.jsonl [--out R.json]          per-unit / per-function differences, retargeted calls
    equiv.py attribute DECOMP TREE A.jsonl B.jsonl --rules SPEC --out X.jsonl [--hier --default-groups] [--failed]
    equiv.py show DECOMP TREE_A TREE_B --unit I [--fn ADDR]  disassembly diff of one unit (output is decomp-derived)
    equiv.py errors FP.jsonl                        units that did not compile, by first error
    equiv.py bindings DECOMP                        static: platform function names the recipe binds elsewhere
    equiv.py summarize BASE.jsonl HEAD.jsonl --without NAME=FP.jsonl... [--eh A B]   per-rule table from leave-out runs
    equiv.py layouts DECOMP OBJDIR_A OBJDIR_B [--show CLASS,..]    MSVC class layouts of two --layout builds
    equiv.py report BASE.jsonl HEAD.jsonl --out R.md [--attr A.jsonl] [--eh A B]   markdown report of a gate
    merge_reports.py R1.md R2.md ... | --git CLONE [--farms eq1,..] [--commit C12]   one summary of shard reports

Prepared trees get `msvc/fnvnx_*.h` (MSVC meanings of the fnv-nx shim headers: the real SDK / CRT; `--lean` builds
the stand-in `<windows.h>` with WIN32_LEAN_AND_MEAN) and `/FI msvc/fnvnx_equiv_compat.h`; both sides get
`/FI msvc/fnvnx_equiv_fi.h` (macros only). A prepared tree's own build/units.json (S1b renames) is used where the
pristine name of a function is not in its object. `fnvnx_equiv_unaw.h` is generated from the SDK in use into
`~/.cache/fnvnx-equiv/` (never committed).
