# w13 handoff: FreeType configuration (ftcfg), stopped by L5

State at root head 84f12313f: no code change needed. platform/freetype/gen_units.py derives ftoption.h from the pinned upstream header plus debian/patches/series only; no configure probing, no tracked hand header. SYSTEM_ZLIB, USE_BZIP2, USE_PNG, USE_BROTLI, HARFBUZZ stay at upstream defaults.

Whole-body constant-error bodies in the unit set: 2 (static count, matches w10 f9f448169).
- FT_Library_SetLcdGeometry (ftlcdfil.c:358-365). Doc row: include/freetype/ftlcdfil.h:303-304 of the pinned FreeType.
- FT_Stream_OpenBzip2 (ftbzip2.c:522-529). Doc row: include/freetype/ftbzip2.h:87-88 of the pinned FreeType.

Open: L1's row form (table next to wine_fixme.tsv); L1 w25 kept-section list decides whether each body is judged. If OpenBzip2 is kept, report to L5.
