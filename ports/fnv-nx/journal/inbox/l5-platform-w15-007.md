# w15 007: dll roles from contract/dllkind.py (closure.tsv)

platform/manifest/pemanifest.py now takes `<dir> <root.exe> <outdir> <bridge.tsv> <dllkind.py> <sha256>`. The two name lists
(contract-only, dynamic) and their callers are gone. The role of every dll is contract/dllkind.py's `classify` (file from
claude/fnv-l1-contract-w18 37b24de3, pinned by the sha256 argument, read from its path, not copied). BRIDGE = dlls with rows
in bridge.tsv. Dynamic reach: a module of the directory that a reached module loads by a constant LoadLibrary name (its
dynsites literal) joins as a delay-load import of the root, to a fixed point. closure.tsv gains the column `reach`
(static|dynamic). An imported dll with no role is an error.

Global numbers (exe + gamedata/redist c53729e, fnv-nx-build l5-platform/manifest e3984cb): 25 rows = root 1, image 5
(atimgpud [dynamic], binkw32, d3dx9_38, libvorbis, libvorbisfile), bridge 15, declared 0, platform 0,
not-in-static-closure 4 (d3dx9_31, galaxy, gdffalloutnv, ssce5432).
d3dx9_31.dll is not reached by any constant name (no string of it in the exe, atimgpud or binkw32): the old hand list put it
in the closure. Its rows (imports 177, dynsites 40, regsites 6, sites_resolved 40) are dropped from the four tables
pemanifest/resolve_dynsites own; filemap_sites, fileset, filesites still carry d3dx9_31 rows (their owner regenerates).
galaxywrp.dll is `bridge` (it has bridge rows), galaxy.dll is outside the static closure.
