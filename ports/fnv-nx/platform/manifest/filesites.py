#!/usr/bin/env python3
"""File-name sites of a PE module set (L5 declared machine, file-set census).

Rule (owner): the platform file system serves exactly the files the installed game has plus the files the exe creates. This pass finds,
for every call of an imported file API in every module of the closure, the file-name operand by the same constant-argument data flow
as regsites.py (one engine: regsites.Analyzer, the same wrapper rule through the callers, with a deeper wrapper depth because the exe's
statically linked CRT puts fopen/_open/_access several wrappers above CreateFileA).

Output rows: module, site, api, kind, name, status, reason.
  kind   open | create | write | delete | move | stat | enum | profile | load | dir | module | query
  name   the constant file name (or the directory API name for a buffer filled by a directory call, e.g. `<GetModuleFileNameA>`),
         `dynamic` when the operand is not a constant.
  status static | dynamic. A dynamic row states why (object field, parameter without static callers, computed buffer ...).
A name operand that is the address of an ebp buffer is resolved only as far as the buffer is filled by a recognised API of the table in the
same function: the row then reads `<API>` + `+?` (a suffix appended by an intra-module call is NOT resolved by this pass and stays
dynamic with that reason). Nothing is guessed from names or text.
Domain gate: every import of kernel32/shell32/shlwapi/msvcrt/user32 whose name is a file-API family member (FILE_FAMILY) must have a
ROLES row; an import added to a module without one stops the pass.
Assumptions: those of regsites.py (ASM-001, ASM-003, ASM-006, ASM-012, ASM-013; registry verify/ASSUMPTIONS.json).
Usage: filesites.py <dir-with-modules> <root.exe> <out.tsv> --bridge bridge.tsv --dllkind dllkind.py --dllkind-sha SHA256 [--facts DIR]
Image set: the root and the dlls of dllkind role `image` (pemanifest.closure); no name list.
"""
import collections, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import funcfacts, pemanifest, regsites
from regsites import Analyzer, NOPUSH

# (nargs, None (no key argument), name arg index, extra name arg index, out buffer arg index, kind)
_B = {
    "CreateFile": (7, None, 0, None, None, "open"),
    "CopyFile": (3, None, 0, 1, None, "create"),
    "DeleteFile": (1, None, 0, None, None, "delete"),
    "MoveFile": (2, None, 0, 1, None, "move"),
    "CreateDirectory": (2, None, 0, None, None, "dir"),
    "RemoveDirectory": (1, None, 0, None, None, "delete"),
    "FindFirstFile": (2, None, 0, None, None, "enum"),
    "GetFileAttributes": (1, None, 0, None, None, "stat"),
    "SetFileAttributes": (2, None, 0, None, None, "stat"),
    "GetFullPathName": (4, None, 0, None, 2, "query"),
    "GetPrivateProfileInt": (4, None, 3, None, None, "profile"),
    "GetPrivateProfileString": (6, None, 5, None, None, "profile"),
    "GetPrivateProfileSection": (4, None, 3, None, None, "profile"),
    "WritePrivateProfileString": (4, None, 3, None, None, "profile-write"),
    "WritePrivateProfileSection": (3, None, 2, None, None, "profile-write"),
    "LoadLibrary": (1, None, 0, None, None, "load"),
    "GetDriveType": (1, None, 0, None, None, "query"),
    "GetModuleFileName": (3, None, None, None, 1, "module"),
    "GetCurrentDirectory": (2, None, None, None, 1, "module"),
    "GetWindowsDirectory": (2, None, None, None, 0, "module"),
    "GetSystemDirectory": (2, None, None, None, 0, "module"),
    "GetTempPath": (2, None, None, None, 1, "module"),
    "SetCurrentDirectory": (1, None, 0, None, None, "dir"),
    "SearchPath": (6, None, 1, None, None, "query"),
    "OpenFile": (3, None, 0, None, None, "open"),
}
ROLES = {}
for _n, _r in _B.items():
    ROLES[_n + "A"] = ROLES[_n + "W"] = _r
ROLES["FindFirstFileExA"] = ROLES["FindFirstFileExW"] = (6, None, 0, None, None, "enum")
ROLES["SHGetFolderPathA"] = ROLES["SHGetFolderPathW"] = (5, None, None, None, 4, "module")
ROLES["SHGetSpecialFolderPathA"] = ROLES["SHGetSpecialFolderPathW"] = (4, None, None, None, 1, "module")
ROLES["ShellExecuteA"] = ROLES["ShellExecuteW"] = (6, None, 2, None, None, "load")
ROLES["CreateDirectoryExA"] = ROLES["CreateDirectoryExW"] = (3, None, 1, None, None, "dir")
ROLES["MoveFileExA"] = ROLES["MoveFileExW"] = (3, None, 0, 1, None, "move")
ROLES["GetFileSize"] = (2, None, None, None, None, "handle")
ROLES["GetFileSizeEx"] = (2, None, None, None, None, "handle")
ROLES["CreateFileMappingA"] = ROLES["CreateFileMappingW"] = (6, None, None, None, None, "handle")
ROLES["GetTempFileNameA"] = ROLES["GetTempFileNameW"] = (4, None, 0, None, 3, "create")
ROLES["PathFileExistsA"] = ROLES["PathFileExistsW"] = (1, None, 0, None, None, "stat")

# every import of these families must have a row (the gate only widens)
FILE_FAMILY = re.compile(r"^(CreateFile|CopyFile|DeleteFile|MoveFile|CreateDirectory|RemoveDirectory|FindFirstFile|GetFileAttributes|"
                         r"SetFileAttributes|GetFullPathName|GetPrivateProfile|WritePrivateProfile|LoadLibrary|GetDriveType|"
                         r"GetModuleFileName|GetCurrentDirectory|GetWindowsDirectory|GetSystemDirectory|GetTempPath|GetTempFileName|"
                         r"SetCurrentDirectory|SearchPath|OpenFile|SHGetFolderPath|SHGetSpecialFolderPath|ShellExecute|PathFileExists|"
                         r"GetFileSize|CreateFileMapping|_lopen|_lcreat|SHFileOperation|SHCreateDirectory)")
# imports that are family members by prefix but are not file-name APIs and are covered by an explicit reason
NOT_FILE_NAME = {"LoadLibraryExA", "LoadLibraryExW", "GetPrivateProfileStructA", "GetPrivateProfileStructW",
                 "GetPrivateProfileSectionNamesA", "GetPrivateProfileSectionNamesW", "GetPrivateProfileSectionW",
                 "WritePrivateProfileStructA", "WritePrivateProfileStructW", "GetPrivateProfileStringW", "GetPrivateProfileIntW",
                 "WritePrivateProfileStringW", "GetFileAttributesExA", "GetFileAttributesExW", "GetFileAttributesW",
                 "LoadLibraryW", "SHFileOperationA", "SHFileOperationW", "SHCreateDirectoryExA", "SHCreateDirectoryExW",
                 "SetFileAttributesW"}

FILE_DLLS = ("kernel32.dll", "shell32.dll", "shlwapi.dll", "msvcrt.dll", "user32.dll", "advapi32.dll")
regsites.DEPTH = 6


def gate_domain(mods):
    return [(n, dll, nm) for n, m in mods.items() for dll, nm, _ in m.imports
            if dll in FILE_DLLS and FILE_FAMILY.match(nm) and nm not in ROLES and nm not in NOT_FILE_NAME]


def _open_result(self, f, args):
    return (None, None)  # an out buffer slot is not a handle; its producer is found by provenance()


def provenance(an, site, v):
    """For a buffer operand ("a", slot): the directory API of the same function that filled it before `site`, or None."""
    if v[0] != "a":
        return None
    found = set()
    for st in an.mod.entries_of(site):   # the site is analysed in every entry that contains it; the entries must agree
        best = None
        for s, f, args in an.run(st)[0]:
            o = ROLES[f][4]
            if s < site and o is not None and o < len(args) and args[o] == v:
                best = f
        found.add(best)
    return found.pop() if len(found) == 1 else None


def trace(an, st, site, v):
    """Facts about a computed buffer ("a", slot): the direct intra-module calls before `site` in the function at `st` that received the
    buffer as an argument, each with the constant strings among their other arguments, in call order. No call is interpreted."""
    from capstone import x86_const as X
    targets = {i.operands[0].imm for i in an.region(st).ins if i.mnemonic == "call" and i.operands and i.operands[0].type == X.X86_OP_IMM}
    out = []
    for s2, t, pushes in an.run(st, None, targets)[1]:
        if s2 < site and v in pushes:
            out.append("%x(%s)" % (t, ",".join(repr(x[1]) for x in pushes if x[0] == "s")))
    return out


def file_rows_of(an, site, f, args, via=None):
    """Rows of one file call in the shape of the registry rows (site, api, kind, name, "-", status, reason, via): one static row per
    name of the full value set (regsites.name_alts: constants, parameters bound through the callers, object fields of typed classes),
    or one dynamic row."""
    role = ROLES[f]
    kind = role[5]
    if kind == "handle":
        return []
    idxs = [k for k in (role[2], role[3]) if k is not None]
    if not idxs:
        return [(site, f, kind, "-", "-", "static", "", via)]
    out = []
    for k in idxs:
        v = args[k] if k < len(args) else NOPUSH
        names, why = regsites.name_alts(an, v)
        if names is not None:
            out += [(site, f, kind, n, "-", "static", "", via) for n in names]
            continue
        p = provenance(an, site, v)
        if p:
            out.append((site, f, kind, "<%s>+?" % p, "-", "dynamic", "suffix not resolved: buffer from %s" % p, via))
            continue
        if v[0] == "a":
            t = list(dict.fromkeys(x for st in an.mod.entries_of(site) for x in trace(an, st, site, v)))
            if t:
                why += "; built by " + " ".join(t)
        out.append((site, f, kind, "dynamic", "-", "dynamic", why or "", via))
    return out


def rows_of(mod):
    saved = (regsites.ROLES, regsites.rows_of, regsites.Analyzer.open_result, regsites.DEPTH)
    regsites.ROLES, regsites.rows_of, regsites.Analyzer.open_result, regsites.DEPTH = ROLES, file_rows_of, _open_result, 6
    try:
        return regsites.regsites(mod, None, None)
    finally:
        regsites.ROLES, regsites.rows_of, regsites.Analyzer.open_result, regsites.DEPTH = saved


def main(d, root, out, argv=()):
    mods = pemanifest.closure_from_argv(list(argv), d, root)[0]
    bad = gate_domain(mods)
    if bad:
        raise SystemExit("file import without a ROLES row: %r" % bad)
    if True:
        with open(out, "w") as f:
            f.write("module\tsite\tapi\tkind\tname\tstatus\treason\n")
            tot = collections.Counter()
            for name in sorted(mods):
                for r in sorted(set(rows_of(mods[name])), key=lambda r: (r[0], r[3], r[5], str(r[7]))):
                    rs = r[6] or (r[7] if r[7] and not r[7].startswith("via ") else "")
                    if r[7] and r[7].startswith("via ") and r[6]:
                        rs = r[6] + "; " + r[7]
                    elif r[7] and r[7].startswith("via "):
                        rs = r[7]
                    f.write("%s\t0x%08x\t%s\t%s\t%s\t%s\t%s\n" % (name, r[0], r[1], r[2], r[3], r[5], rs))
                    tot[(name, r[5])] += 1
    for k, v in sorted(tot.items()):
        sys.stderr.write("%s %s %d\n" % (k[0], k[1], v))


if __name__ == "__main__":
    funcfacts.facts_dir_from_argv(sys.argv)
    if len(sys.argv) < 4:
        sys.stderr.write(__doc__)
        sys.exit(2)
    main(*sys.argv[1:4], argv=sys.argv[4:])
