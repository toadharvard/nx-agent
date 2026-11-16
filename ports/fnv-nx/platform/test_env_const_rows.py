"""Rule: every row of manifest/env_const.tsv names a file that is a tracked file of the root whose build.json has a gen_env_const step that selects it (--only PREFIX), so a
row nobody includes is an error (a dead row). The universe is every build.json under ports/fnv-nx by the glob, every gen_env_const step in it, every row of its table.
Run: python3 -I ports/fnv-nx/platform/test_env_const_rows.py"""
import glob, json, os, subprocess, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.dirname(HERE)   # ports/fnv-nx


def steps():
    """[(root dir, table path, only prefix or None)] of every gen_env_const step of every build.json"""
    out = []
    for b in sorted(glob.glob(os.path.join(TREE, "**", "build.json"), recursive=True)):
        root = os.path.dirname(b)
        for g in json.load(open(b)).get("generated", []):
            if g.get("tool", "").endswith("gen_env_const.py"):
                a = g["args"]
                tsv = next(os.path.join(TREE, x.split("/in/ports/fnv-nx/", 1)[1]) for x in a if x.endswith(".tsv") and "/in/ports/fnv-nx/" in x and "declared" not in x)
                out.append((root, tsv, a[a.index("--only") + 1] if "--only" in a else None))
    return out


def findings(root_files, table_rows, only_by_root):
    """[error] of ROWS (symbol, file): a row selected by no step of the root (its file must start with some step's prefix) or naming no file of the root.
    ROOT_FILES: the set of root-relative tracked files; ONLY_BY_ROOT: the prefixes of the root's steps (None selects every row)."""
    errs = []
    for sym, f in table_rows:
        if not any(p is None or f.startswith(p) for p in only_by_root):
            errs.append("%s: file %s is selected by no gen_env_const step" % (sym, f))
        elif os.path.normpath(f) != f or f not in root_files:
            errs.append("%s: file %s is not a tracked file of the including root" % (sym, f))
    return errs


def tracked_files(root):
    out = subprocess.check_output(["git", "-C", root, "ls-files"], text=True).split("\n")   # paths relative to ROOT
    return {x for x in out if x}


def table_rows(tsv):
    with open(tsv, encoding="utf-8") as fh:
        ls = fh.read().split("\n")
    return [(r[0], r[7]) for r in (l.split("\t") for l in ls[1:] if l)]


class Rule(unittest.TestCase):
    def test_every_row_is_included(self):
        st = steps()
        self.assertTrue(st, "no gen_env_const step found")
        by_tsv = {}
        for root, tsv, only in st:
            by_tsv.setdefault(tsv, []).append((root, only))
        for tsv, ss in by_tsv.items():
            self.assertEqual(len({r for r, _ in ss}), 1, "a table is included by one root")
            root = ss[0][0]
            self.assertEqual(findings(tracked_files(root), table_rows(tsv), [o for _, o in ss]), [], tsv)

    def test_red_on_a_dead_row(self):
        for f in ("../platform_host/os/os_horizon.c", "win32/no_such_file.cpp", "audio/dshow.cpp.orig"):
            e = findings({"audio/dshow.cpp", "win32/env_const.cpp"}, [("os_cpu_count", f)], ["audio/dshow.cpp", "win32/"])
            self.assertTrue(e, f)
        self.assertEqual(findings({"audio/dshow.cpp"}, [("x", "audio/dshow.cpp")], ["audio/dshow.cpp"]), [])


if __name__ == "__main__":
    unittest.main()
