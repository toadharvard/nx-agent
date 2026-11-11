#!/usr/bin/env python3
"""Tests for contract/hive.py (gate `registry`): synthetic call sites and declared rows; every uncertified class, the generator's refusals and the
green case. Plain script: PASS/FAIL lines, exit 0 on pass."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import hive as H  # noqa: E402

FAILED = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)[:400]))
    if not ok:
        FAILED.append(name)


def raises(fn, *a):
    try:
        fn(*a)
    except ValueError as e:
        return str(e)
    return None


SH = "\t".join(H.SITES_HEAD) + "\n"
DH = "\t".join(H.DECL_HEAD) + "\n"


def site(mod, s, api, root, key, val, status="static"):
    return "\t".join([mod, s, api, root, key, val, status, "r"]) + "\n"


LM = "HKEY_LOCAL_MACHINE"
SITES = (SH + site("a.exe", "0x401000", "RegQueryValueExA", LM, "SOFTWARE\\Toy\\Game", "Installed Path")
         + site("a.exe", "0x401100", "RegQueryValueExA", LM, "software\\toy\\game", "Count")
         + site("a.exe", "0x401200", "RegOpenKeyExA", LM, "SOFTWARE\\Toy\\Game", "")
         + site("a.exe", "0x401300", "RegSetValueExA", LM, "SOFTWARE\\Toy\\Game", "Written"))
DECL = (DH + "%s\tSOFTWARE\\Toy\\Game\tInstalled Path\tREG_SZ\t{game_dir}\tdeclared machine\n" % LM
        + "%s\tSOFTWARE\\Toy\\Game\tCount\tREG_DWORD\t3\tdeclared machine\n" % LM)
SEED = H.generate(SITES, DECL)
check("generate: one entry per declared row, terminator", SEED.count("\n    { 0x80000002u") == 2 and SEED.endswith("{ 0, 0, 0, 0, 0, 0 }\n"), SEED)
check("generate: dword is 4 little-endian bytes, size 4", '\\003\\000\\000\\000", 4 }' in SEED, SEED)
check("generate: text keeps the game directory mark, size 0", '"\\001", 0 }' in SEED, SEED)
check("generate: deterministic", H.generate(SITES, DECL) == SEED)

REFS = {"seed.c": {"RegCreateKeyExA", "RegSetValueExA"}, "a.c": {"RegQueryValueExA", "memcpy"}}
r = dict(H.judge(SITES, DECL, SEED, REFS, "seed.c"))
check("judge: green case (case-insensitive key match, seed equal, only the seed unit writes)", all(v is None for v in r.values()) and len(r) == 4, r)
check("judge: universe is declared rows, seed, writers", sorted(r) == sorted(["declared:%s\\SOFTWARE\\Toy\\Game:Installed Path" % LM,
      "declared:%s\\SOFTWARE\\Toy\\Game:Count" % LM, "seed", "writer:seed.c"]), sorted(r))

# every uncertified class
unread = DECL + "%s\tSOFTWARE\\Toy\\Other\tX\tREG_SZ\tv\tdeclared machine\n" % LM
r = dict(H.judge(SITES, unread, SEED, REFS, "seed.c"))
check("judge: declared row with no read site", r["declared:%s\\SOFTWARE\\Toy\\Other:X" % LM] == "no-read-site", r)
check("judge: the include cannot be generated while a row is unread (seed class names the generator)", r["seed"].startswith("generator: "), r)
check("judge: a key that is only written or opened is not read", dict(H.judge(SITES, DH + "%s\tSOFTWARE\\Toy\\Game\tWritten\tREG_SZ\tv\ts\n" % LM, SEED, {}, "s"))
      ["declared:%s\\SOFTWARE\\Toy\\Game:Written" % LM] == "no-read-site")
r = dict(H.judge(SITES, DECL, SEED + " ", REFS, "seed.c"))
check("judge: include drifted by one byte", r["seed"] == "seed-differs", r)
r = dict(H.judge(SITES, DECL, SEED, dict(REFS, **{"evil.c": {"RegSetValueW"}}), "seed.c"))
check("judge: foreign writer unit", r["writer:evil.c"] == "foreign-writer", r)
r = dict(H.judge(SITES, DECL, SEED, {"x.c": {"RegCreateKeyA"}}, "seed.c"))
check("judge: any hive-writing API makes a writer (RegCreateKeyA)", r["writer:x.c"] == "foreign-writer", r)
dyn = SITES + site("a.exe", "0x402000", "RegQueryValueExA", LM, "?", "?", "dynamic")
r = H.judge(dyn, DECL, SEED, REFS, "seed.c")
check("judge: a dynamic read site is class dynamic", ("site:a.exe:0x402000:RegQueryValueExA", "dynamic") in r, r)
dynw = SITES + site("a.exe", "0x402000", "RegSetValueExA", LM, "?", "?", "dynamic")
check("judge: a dynamic write site does not open a measurement", not any(c == "dynamic" for _, c in H.judge(dynw, DECL, SEED, REFS, "seed.c")))

# generator refusals and bad input
for name, d in (("unknown root", DH + "HKEY_X\tk\tn\tREG_SZ\tv\ts\n"), ("unknown type", DH + "%s\tSOFTWARE\\Toy\\Game\tCount\tREG_FOO\t1\ts\n" % LM),
                ("missing source", DH + "%s\tSOFTWARE\\Toy\\Game\tCount\tREG_DWORD\t1\t\n" % LM),
                ("duplicate row (case-insensitive)", DECL + "%s\tsoftware\\toy\\game\tCOUNT\tREG_DWORD\t4\ts\n" % LM),
                ("dword out of range", DH + "%s\tSOFTWARE\\Toy\\Game\tCount\tREG_DWORD\t4294967296\ts\n" % LM),
                ("stray brace", DH + "%s\tSOFTWARE\\Toy\\Game\tCount\tREG_SZ\t{x}\ts\n" % LM),
                ("non-ascii text", DH + "%s\tSOFTWARE\\Toy\\Game\tCount\tREG_SZ\té\ts\n" % LM),
                ("odd hex", DH + "%s\tSOFTWARE\\Toy\\Game\tCount\tREG_BINARY\tabc\ts\n" % LM),
                ("multi_sz without terminator", DH + "%s\tSOFTWARE\\Toy\\Game\tCount\tREG_MULTI_SZ\t41\ts\n" % LM),
                ("bad key path", DH + "%s\t\\SOFTWARE\tCount\tREG_DWORD\t1\ts\n" % LM),
                ("bad header", "root\tkey\n"), ("short row", DH + "a\tb\n"), ("empty text", "")):
    check("generate refuses: " + name, raises(H.generate, SITES, d) is not None)
check("sites: api without a registry role is an error", raises(H.read_sites, SH + site("a", "1", "CreateFileA", LM, "k", "v")) is not None)
check("sites: bad status is an error", raises(H.read_sites, SH + site("a", "1", "RegQueryValueExA", LM, "k", "v", "maybe")) is not None)
check("generate: REG_BINARY and REG_QWORD encode", H.generate(SITES, DH + "%s\tSOFTWARE\\Toy\\Game\tCount\tREG_BINARY\t0aff\ts\n" % LM).count('"\\012\\377", 2') == 1
      and '"\\377\\000\\000\\000\\000\\000\\000\\000", 8' in H.generate(SITES, DH + "%s\tSOFTWARE\\Toy\\Game\tCount\tREG_QWORD\t255\ts\n" % LM))

print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
