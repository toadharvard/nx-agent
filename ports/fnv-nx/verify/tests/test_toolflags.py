#!/usr/bin/env python3
"""Tests for verify/toolflags.py (R12-T class 2). Plain script: exit 0 on pass, one PASS/FAIL line per case."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import toolflags  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

FAILED = testcase.FAILED
TMP = tempfile.mkdtemp(prefix="toolflags-test-")




ENV = {"TZ": "UTC", "LC_ALL": "C", "PYTHONHASHSEED": "0", "SOURCE_DATE_EPOCH": "1700000000", "COLUMNS": "80",
       "PATH": "/tc/bin"}
INPUTS = [{"at": "/tc", "tree": "t"}, {"at": "/src", "tree": "s"}]
GCC = ["/tc/bin/gcc", "-c", "/src/a.c", "-o", "/out/a.o", "-O2", "-frandom-seed=a.o", "-ffile-prefix-map=/src=.",
       "-Werror=date-time", "-pipe"]
LD = ["/tc/bin/ld", "-o", "/out/a.elf", "/src/a.o", "--build-id=sha1"]
PY = ["/tc/bin/python3", "-B", "-s", "/src/tool.py", "-j", "4"]
CLDECL = {"coff_normalize": True, "no_date_time_macros": {"checked_by": "srcscan"}}
CL = ["/tc/bin/cl.exe", "/c", "/Z7", "/Fo/out/a.obj", "/src/a.c"]
LINK = ["/tc/bin/link.exe", "/INCREMENTAL:NO", "/OUT:/out/a.exe", "/src/a.obj"]


def doc(tool, argv, env=None, extra=None):
    d = {"format": "fnvnx-stage/1", "stage": "S", "class": "build", "tool": tool, "argv": list(argv), "cwd": "/src",
         "env": dict(ENV if env is None else env), "inputs": copy.deepcopy(INPUTS), "outputs": ["/out"]}
    d.update(extra or {})
    return d


def rules(d, root=None):
    return sorted({v[0] for v in toolflags.check_doc(d, root)})


def expect(name, d, want, root=None):
    got = rules(d, root)
    case('expect', got == sorted(want), "want %s got %s" % (sorted(want), got), key=name)


def env_with(**kw):
    e = dict(ENV)
    for k, v in kw.items():
        if v is None:
            e.pop(k)
        else:
            e[k] = v
    return e


def argv_with(base, add=(), drop=()):
    return [a for a in base if not any(a.startswith(p) for p in drop)] + list(add)


# --- realistic passing stages
expect("gcc full compile passes", doc("gcc", GCC), [])
expect("ar rcD passes", doc("ar", ["/tc/bin/ar", "rcD", "/out/liba.a", "/src/a.o", "/src/b.o"]), [])
expect("ld passes", doc("ld", LD), [])
expect("python passes (script args may hold -j)", doc("python", PY), [])
expect("msvc-cl coff_normalize passes", doc("msvc-cl", CL, extra=CLDECL), [])
expect("msvc-link passes", doc("msvc-link", LINK, extra={"coff_normalize": True}), [])

# --- environment rules
for k, v in (("TZ", "Europe/Berlin"), ("LC_ALL", "en_US.UTF-8"), ("PYTHONHASHSEED", "1"), ("SOURCE_DATE_EPOCH", "12x"),
             ("SOURCE_DATE_EPOCH", "-5"), ("COLUMNS", "wide")):
    expect("env value %s=%s" % (k, v), doc("gcc", GCC, env_with(**{k: v})), ["env-value"])
for k in ("TZ", "LC_ALL", "PYTHONHASHSEED", "SOURCE_DATE_EPOCH", "COLUMNS", "PATH"):
    expect("env missing %s" % k, doc("gcc", GCC, env_with(**{k: None})), ["env-missing"])
for k in ("LANG", "LANGUAGE", "LC_TIME", "PYTHONPATH", "HOME", "USER", "LOGNAME", "HOSTNAME", "PWD", "DISPLAY",
          "CCACHE_DIR", "DISTCC_HOSTS", "GCC_EXEC_PREFIX", "COMPILER_PATH", "LIBRARY_PATH", "CPATH", "C_INCLUDE_PATH",
          "CPLUS_INCLUDE_PATH", "DEPENDENCIES_OUTPUT", "SUNPRO_DEPENDENCIES", "MAKEFLAGS", "MFLAGS", "MAKELEVEL",
          "PYTHONSTARTUP"):
    expect("env forbidden %s" % k, doc("gcc", GCC, env_with(**{k: "x"})), ["env-forbidden"])
expect("env PATH outside inputs", doc("gcc", GCC, env_with(PATH="/tc/bin:/usr/bin")), ["env-path"])
expect("env PATH prefix lookalike", doc("gcc", GCC, env_with(PATH="/tcx/bin")), ["env-path"])
expect("env TMPDIR under output ok", doc("gcc", GCC, env_with(TMPDIR="/out/tmp")), [])
expect("env TMPDIR outside outputs", doc("gcc", GCC, env_with(TMPDIR="/var/tmp")), ["env-tmpdir"])

# --- tool declaration
d = doc("gcc", GCC)
del d["tool"]
expect("tool undeclared", d, ["tool-undeclared"])
expect("tool sh", doc("sh", ["/tc/bin/sh", "-c", "true"]), ["tool-unanalysable"])
expect("tool make", doc("make", ["/tc/bin/make"]), ["tool-unanalysable"])
expect("tool other", doc("other", ["/tc/bin/foo"]), ["tool-unlisted"])
expect("tool unknown kind", doc("rustc", ["/tc/bin/rustc"]), ["tool-unlisted"])

# --- gcc rows
expect("gcc no random-seed", doc("gcc", argv_with(GCC, drop=["-frandom-seed"])), ["flag-missing"])
expect("gcc empty random-seed", doc("gcc", argv_with(GCC, ["-frandom-seed="], ["-frandom-seed"])), ["flag-missing"])
expect("gcc no file-prefix-map", doc("gcc", argv_with(GCC, drop=["-ffile-prefix-map"])), ["flag-missing"])
expect("gcc no Werror=date-time", doc("gcc", argv_with(GCC, drop=["-Werror=date-time"])), ["flag-missing"])
for f in ("-march=native", "-mtune=native", "-fprofile-use", "-fprofile-generate", "-fbranch-probabilities",
          "-fauto-profile", "-save-temps", "-Wno-date-time", "-Wno-error=date-time", "-fprofile-use=rel/x.gcda"):
    expect("gcc forbidden %s" % f, doc("gcc", argv_with(GCC, [f])),
           ["path-outside-inputs"] if "=rel" in f else ["flag-forbidden"])
expect("gcc profile-use under input", doc("gcc", argv_with(GCC, ["-fprofile-use=/src/p.gcda"])), [])
expect("gcc profile-use outside input", doc("gcc", argv_with(GCC, ["-fprofile-use=/home/p"])), ["path-outside-inputs"])
expect("gcc plugin under input", doc("gcc", argv_with(GCC, ["-fplugin=/tc/p.so"])), [])
expect("gcc plugin outside input", doc("gcc", argv_with(GCC, ["-fplugin=/opt/p.so"])), ["path-outside-inputs"])
expect("gcc specs under input", doc("gcc", argv_with(GCC, ["-specs=/tc/s.specs"])), [])
expect("gcc specs outside input", doc("gcc", argv_with(GCC, ["-specs=/etc/s"])), ["path-outside-inputs"])
expect("gcc -j in argv", doc("gcc", argv_with(GCC, ["-j4"])), ["jobs-in-argv"])
expect("gcc --jobs in argv", doc("gcc", argv_with(GCC, ["--jobs=4"])), ["jobs-in-argv"])
expect("jobs field non-integer", doc("gcc", GCC, extra={"jobs": "4"}), ["jobs-invalid"])
expect("jobs field integer ok", doc("gcc", GCC, extra={"jobs": 4}), [])

# --- as
AS = ["/tc/bin/as", "-o", "/out/a.o", "/src/a.s"]
expect("as plain passes", doc("as", AS), [])
expect("as -g without debug-prefix-map", doc("as", AS + ["-g"]), ["flag-missing"])
expect("as --gdwarf-5 without map", doc("as", AS + ["--gdwarf-5"]), ["flag-missing"])
expect("as -g with map", doc("as", AS + ["-g", "--debug-prefix-map", "/src=."]), [])

# --- ld
expect("ld no build-id", doc("ld", argv_with(LD, drop=["--build-id"])), ["flag-missing"])
expect("ld bare build-id ok", doc("ld", argv_with(LD, ["--build-id"], ["--build-id"])), [])
expect("ld build-id none ok", doc("ld", argv_with(LD, ["--build-id=none"], ["--build-id"])), [])
expect("ld build-id uuid", doc("ld", argv_with(LD, ["--build-id=uuid"], ["--build-id"])), ["flag-forbidden"])
expect("ld -T under input", doc("ld", LD + ["-T", "/src/l.ld"]), [])
expect("ld -T outside", doc("ld", LD + ["-T", "/etc/l.ld"]), ["path-outside-inputs"])
expect("ld --script= outside", doc("ld", LD + ["--script=/etc/l.ld"]), ["path-outside-inputs"])
expect("ld -Ttext address ok", doc("ld", LD + ["-Ttext", "0x400000"]), [])
expect("ld -Map output ok", doc("ld", LD + ["-Map", "/out/a.map"]), [])

# --- ar / objcopy
AR = ["/tc/bin/ar", "rcD", "/out/liba.a", "/src/a.o"]
expect("ar without D", doc("ar", ["/tc/bin/ar", "rc", "/out/liba.a", "/src/a.o"]), ["flag-missing"])
expect("ar with U", doc("ar", ["/tc/bin/ar", "rcDU", "/out/liba.a", "/src/a.o"]), ["flag-forbidden"])
expect("ar dash form ok", doc("ar", ["/tc/bin/ar", "-rcD", "/out/liba.a", "/src/a.o"]), [])
OC = ["/tc/bin/objcopy", "-D", "-O", "binary", "-j", ".text", "/src/a.o", "/out/a.bin"]
expect("objcopy passes (-j is --only-section)", doc("objcopy", OC), [])
expect("objcopy without -D", doc("objcopy", ["/tc/bin/objcopy", "/src/a.o", "/out/b.o"]), ["flag-missing"])
expect("objcopy long -D ok", doc("objcopy", ["/tc/bin/objcopy", "--enable-deterministic-archives", "/src/a.o"]), [])
expect("objcopy -p", doc("objcopy", OC + ["-p"]), ["flag-forbidden"])
expect("objcopy --preserve-dates", doc("objcopy", OC + ["--preserve-dates"]), ["flag-forbidden"])
expect("objcopy --disable-deterministic-archives", doc("objcopy", OC + ["-U"]), ["flag-forbidden"])
expect("objcopy --jobs", doc("objcopy", OC + ["--jobs=2"]), ["jobs-in-argv"])

# --- python
expect("python without -B", doc("python", ["/tc/bin/python3", "-s", "/src/tool.py"]), ["flag-missing"])
expect("python PYTHONDONTWRITEBYTECODE=1 env stands in for -B",
       doc("python", ["/tc/bin/python3", "-s", "/src/tool.py"], env_with(PYTHONDONTWRITEBYTECODE="1")), [])
expect("python without -s", doc("python", ["/tc/bin/python3", "-B", "/src/tool.py"]), ["flag-missing"])
expect("python -I drops PYTHONHASHSEED", doc("python", ["/tc/bin/python3", "-I", "-B", "-s", "/src/tool.py"]),
       ["flag-forbidden"])
expect("python -E drops PYTHONHASHSEED", doc("python", ["/tc/bin/python3", "-E", "-B", "-s", "/src/tool.py"]),
       ["flag-forbidden"])
expect("python -c", doc("python", ["/tc/bin/python3", "-B", "-s", "-c", "pass"]), ["flag-forbidden"])
expect("python -m", doc("python", ["/tc/bin/python3", "-B", "-s", "-m", "x"]), ["flag-forbidden"])
expect("python no script", doc("python", ["/tc/bin/python3", "-B", "-s"]), ["flag-missing"])
expect("python script outside inputs", doc("python", ["/tc/bin/python3", "-B", "-s", "/etc/t.py"]),
       ["path-outside-inputs"])
expect("python clustered -Bs ok", doc("python", ["/tc/bin/python3", "-Bs", "/src/tool.py"]), [])
expect("python -j before script", doc("python", ["/tc/bin/python3", "-B", "-s", "-j", "/src/tool.py"]),
       ["jobs-in-argv"])
expect("python other PYTHON* env", doc("python", PY, env_with(PYTHONOPTIMIZE="1")), ["env-forbidden"])

# --- msvc
expect("msvc-cl no decl", doc("msvc-cl", CL, extra={"no_date_time_macros": {"checked_by": "x"}}), ["decl-missing"])
expect("msvc-cl both decl", doc("msvc-cl", CL + ["/Brepro"], extra=dict(CLDECL, msvc_brepro=True)), ["decl-conflict"])
expect("msvc-cl brepro declared and present",
       doc("msvc-cl", CL + ["/Brepro"], extra={"msvc_brepro": True, "no_date_time_macros": {"checked_by": "x"}}), [])
expect("msvc-cl brepro declared absent",
       doc("msvc-cl", CL, extra={"msvc_brepro": True, "no_date_time_macros": {"checked_by": "x"}}), ["flag-missing"])
expect("msvc-cl no date/time declaration", doc("msvc-cl", CL, extra={"coff_normalize": True}), ["decl-missing"])
expect("msvc-cl empty checked_by",
       doc("msvc-cl", CL, extra={"coff_normalize": True, "no_date_time_macros": {"checked_by": ""}}), ["decl-missing"])
for f in ("/Zi", "/ZI", "/MP", "/MP4", "/Fdfoo.pdb"):
    expect("msvc-cl forbidden %s" % f, doc("msvc-cl", CL + [f], extra=CLDECL), ["flag-forbidden"])
expect("msvc-cl /showIncludes /FC ok", doc("msvc-cl", CL + ["/showIncludes", "/FC"], extra=CLDECL), [])
expect("msvc-link no INCREMENTAL:NO", doc("msvc-link", argv_with(LINK, drop=["/INCREMENTAL"]),
                                          extra={"pe_normalize": True}), ["flag-missing"])
expect("msvc-link /INCREMENTAL", doc("msvc-link", argv_with(LINK, ["/INCREMENTAL"], ["/INCREMENTAL"]),
                                     extra={"pe_normalize": True}), ["flag-forbidden", "flag-missing"])
for f in ("/LTCG:PGINSTRUMENT", "/LTCG:PGOPTIMIZE", "/ltcg:pgoptimize"):
    expect("msvc-link forbidden %s" % f, doc("msvc-link", LINK + [f], extra={"pe_normalize": True}), ["flag-forbidden"])
expect("msvc-link no decl", doc("msvc-link", LINK), ["decl-missing"])
expect("msvc-lib passes", doc("msvc-lib", ["/tc/bin/lib.exe", "/OUT:/out/a.lib", "/src/a.obj"],
                              extra={"coff_normalize": True}), [])
expect("msvc-lib brepro missing", doc("msvc-lib", ["/tc/bin/lib.exe", "/OUT:/out/a.lib"],
                                      extra={"msvc_brepro": True}), ["flag-missing"])

# --- wine
WENV = env_with(WINEDEBUG="-all", WINEPREFIX="/out/wp")
WARGV = ["/tc/bin/wine"] + CL
expect("wine passes", doc("wine", WARGV, WENV, dict(CLDECL, wine_tool="msvc-cl")), [])
expect("wine without wine_tool", doc("wine", WARGV, WENV, CLDECL), ["tool-undeclared"])
expect("wine_tool not msvc", doc("wine", WARGV, WENV, dict(CLDECL, wine_tool="gcc")), ["tool-unlisted"])
expect("wine inner rules apply", doc("wine", WARGV + ["/Zi"], WENV, dict(CLDECL, wine_tool="msvc-cl")),
       ["flag-forbidden"])
expect("wine no WINEDEBUG", doc("wine", WARGV, env_with(WINEPREFIX="/out/wp"), dict(CLDECL, wine_tool="msvc-cl")),
       ["env-missing"])
expect("wine WINEPREFIX outside", doc("wine", WARGV, env_with(WINEDEBUG="-all", WINEPREFIX="/root/.wine"),
                                      dict(CLDECL, wine_tool="msvc-cl")), ["env-value"])
expect("wine WINEPREFIX under input ok", doc("wine", WARGV, env_with(WINEDEBUG="-all", WINEPREFIX="/tc/wp"),
                                             dict(CLDECL, wine_tool="msvc-cl")), [])

# --- response files
root = os.path.join(TMP, "root")
os.makedirs(os.path.join(root, "src"))


def rsp(text):
    with open(os.path.join(root, "src", "a.rsp"), "w") as f:
        f.write(text)


GBASE = ["/tc/bin/gcc", "-c", "/src/a.c", "-o", "/out/a.o"]
rsp("-frandom-seed=a '-ffile-prefix-map=/src=.' -Werror=date-time\n-O2\n")
expect("response file satisfies requirements", doc("gcc", GBASE + ["@/src/a.rsp"]), [], root)
rsp("-frandom-seed=a -ffile-prefix-map=/src=. -Werror=date-time -march=native\n")
expect("response file violation inside", doc("gcc", GBASE + ["@/src/a.rsp"]), ["flag-forbidden"], root)
rsp("-fplugin=/etc/p.so -frandom-seed=a -ffile-prefix-map=/src=. -Werror=date-time\n")
expect("response file path violation inside", doc("gcc", GBASE + ["@/src/a.rsp"]), ["path-outside-inputs"], root)
expect("response file without --root", doc("gcc", GBASE + ["@/src/a.rsp"]), ["response-file-unreadable", "flag-missing"])
expect("response file missing on disk", doc("gcc", GBASE + ["@/src/none.rsp"]),
       ["response-file-unreadable", "flag-missing"], root)
expect("response file outside inputs", doc("gcc", GBASE + ["@/etc/a.rsp"]),
       ["path-outside-inputs", "flag-missing"], root)
rsp("@/src/a.rsp\n")
expect("response file self-include", doc("gcc", GBASE + ["@/src/a.rsp"]), ["response-file-unreadable", "flag-missing"],
       root)
rsp("--build-id=sha1\n")
expect("ld response file", doc("ld", ["/tc/bin/ld", "@/src/a.rsp"]), [], root)

# --- CLI: output, JSON, ordering, exit codes
good = os.path.join(TMP, "good.json")
bad = os.path.join(TMP, "bad.json")
junk = os.path.join(TMP, "junk.json")
json.dump(doc("gcc", GCC), open(good, "w"))
json.dump(doc("gcc", argv_with(GCC, ["-march=native"], ["-frandom-seed"]), env_with(HOME="/h"),
              {"stage": "BADSTAGE"}), open(bad, "w"))
open(junk, "w").write("{not json")


def run(*args):
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "toolflags.py")] + list(args),
                       capture_output=True, text=True)
    return p.returncode, p.stdout


rc, out = run("check", good)
case("cli clean exit 0", rc == 0 and "toolflags: 1 stages, 0 violations" in out, out)
outj = os.path.join(TMP, "o.json")
rc, out = run("check", good, bad, "--json", outj)
lines = out.splitlines()
case("cli violations exit 2", rc == 2, out)
case("cli line format", any(l.startswith("BADSTAGE: flag-forbidden ") and l.endswith("]") for l in lines), out)
case("cli summary", lines[-1].startswith("toolflags: 2 stages, 3 violations (") and "env-forbidden: 1" in lines[-1],
     lines[-1])
rc2, out2 = run("check", good, bad)
case("cli output deterministic", out == out2)
j = json.load(open(outj))
case("json schema", j["format"] == "fnvnx-toolflags/1" and j["summary"]["violations"] == 3 and
     [s["stage"] for s in j["stages"]] == ["S", "BADSTAGE"] and
     set(j["stages"][1]["violations"][0]) == {"rule", "detail", "citation"}, str(j)[:200])
case("cli unreadable document exit 1", run("check", junk)[0] == 1)
case("cli missing file exit 1", run("check", os.path.join(TMP, "none.json"))[0] == 1)
case("cli usage exit 1", run()[0] == 1 and run("bogus")[0] == 1 and run("check")[0] == 1)
rc, out = run("policy")
case("cli policy table", rc == 0 and "gcc" in out and "UNVERIFIED" in out and "reproducible-builds.org" in out, out[:200])
pg = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "puregate.py"), "check",
                     os.path.join(VERIFY, "toolflags.py"), os.path.join(VERIFY, "toolflags_policy.py")],
                    capture_output=True, text=True)
case("toolflags passes puregate", pg.returncode == 0, pg.stdout[-300:])

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILED else 0)
