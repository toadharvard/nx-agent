# render/shaders/patches: tracked patches that make "all files of the patched external tree" the unit set

Applied in this order on the pinned trees of fnv-nx-build (git archive of the pin, no .git):

| external | pin | tree_sha256 (verify/cas.py ingest) | patches, in order |
|---|---|---|---|
| mojoshader | `tools/mojoshader` ad5dff84830c2863c841f4b1f4e3df78c705b383 | e49a1b65e09c86647fd8c0b26c7a8e16e4a16e6ad1290a1e7ada85047306f361 | `../translator/mojoshader.patch`, `translator/mojoshader_units.patch` |

Patched trees (cas.py ingest, measured at the commit of this note): mojoshader after both patches 7af1fe43..., uam after the three patches 4e16a499...

## mojoshader_units.patch
Deletes the files the CMake profile set of build.sh does not build: `mojoshader_effects.c` (EFFECT_SUPPORT=OFF) and the six `utils/*.c`
programs. Adds `mojoshader_version.h`, which CMake wrote with `MOJOSHADER_VERSION -1` and `MOJOSHADER_CHANGESET "???"` (build.sh's shallow
clone has no HEAD~, so CMake's git step always gave "???"). Result: 13 units, all built today by `ninja mojoshader`.
`fnvnx_sm_rows.h` is not in the patch: it is a recipe `generated` python step (`total/gen_sm_rows.py`, input `total/sm_table.json`,
output byte-equal to the committed `total/fnvnx_sm_rows.h`, which `gen_sm_rows.py --check` compares).

uam patches (`uam_gen.patch`, `uam_nomain.patch`) were removed: generated content is not tracked. See journal/inbox/l6-render-w1b.md, "after the first frame".
