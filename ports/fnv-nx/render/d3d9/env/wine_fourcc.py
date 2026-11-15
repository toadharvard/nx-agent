"""wine_fourcc.py: the one reader of the FOURCC formats of the pinned Wine 9.0 mirror (tools/wine-9.0-d3d9-formats). The env probe (envq.py) and the enum generator (gen_enums.py) both
take their FOURCC sets from it; nothing in this file is a list written by hand.
    members(wine)  the FOURCC members of `enum wined3d_format_id` (include/wine/wined3d.h), in header order: every format id Wine 9.0 gives a FOURCC value, i.e. every
                   enumerator defined with WINEMAKEFOURCC (wined3d_private.h defines none: the enum is in the public header). The code of a member is its four characters.
    remap(wine)    the subset that wined3d/utils.c format_index_remap resolves (the FOURCC formats CheckDeviceFormat reaches by remap, in the table's order); each name of that table
                   is resolved to its WINEMAKEFOURCC member by name, and a name with no member stops the reader. remap(wine) is a subset of members(wine): the members that are not in
                   it (DF16, DF24 in 9.0) are format ids that wined3d reaches through its depth-format table, so the front still needs their enumerators.
    wine = the directory of the mirror (an argument of the caller: no path of this file)."""
import os
import re

fcc = lambda s: sum(ord(c) << (8 * i) for i, c in enumerate(s))
MEMBER = r"(WINED3DFMT_\w+)\s*=\s*WINEMAKEFOURCC\('(.)',\s*'(.)',\s*'(.)',\s*'(.)'\)"


def _header(wine):
    return open(os.path.join(wine, "include", "wine", "wined3d.h")).read()


def members(wine):
    """[(WINED3DFMT_name, 'ABCD')] of every WINEMAKEFOURCC member of the enum, in header order"""
    h = _header(wine); i = h.index("enum wined3d_format_id"); blk = h[i:h.index("};", i)]
    out = [(m[0], "".join(m[1:])) for m in re.findall(MEMBER, blk)]
    if not out:
        raise SystemExit("wine_fourcc: no WINEMAKEFOURCC member in enum wined3d_format_id")
    return out


def remap(wine):
    """['ABCD'] of the names of format_index_remap, each resolved to its member (table order)"""
    u = open(os.path.join(wine, "dlls", "wined3d", "utils.c")).read(); i = u.index("format_index_remap[] ="); blk = u[i:u.index("};", i)]
    by = dict(members(wine)); out = []
    for n in re.findall(r"\{(WINED3DFMT_\w+),\s*WINED3D_FORMAT_FOURCC_BASE", blk):
        if n not in by:
            raise SystemExit("wine_fourcc: %s of format_index_remap has no WINEMAKEFOURCC member in wined3d.h" % n)
        out.append(by[n])
    if not out:
        raise SystemExit("wine_fourcc: format_index_remap not found")
    return out
