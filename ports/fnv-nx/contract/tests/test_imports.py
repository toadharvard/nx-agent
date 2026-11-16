#!/usr/bin/env python3
"""Tests for contract/imports.py and contract/bridgetable.py (gate `imports`): synthetic symbol tables, specs and tables, every uncertified
class, the green case and bad input. Plain script: PASS/FAIL lines, exit 0 on pass."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import bridgetable as B  # noqa: E402
import imports as I  # noqa: E402

FAILED = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)[:400]))
    if not ok:
        FAILED.append(name)


SPECS = {"kernel32": "1 stdcall Foo(long ptr)\n2 stdcall Bar()\n3 stdcall Baz()\n4 stdcall Qux()\n5 stdcall Fwd(long) other.Real\n",
         "other": "1 stdcall Real(long)\n",
         "msvcrt": "1 cdecl memcpy(ptr ptr long)\n2 cdecl strlen(str)\n3 cdecl notserved(long)\n4 cdecl memset(ptr long long)\n"
                   "5 extern _iob\n6 cdecl sprintf(str str)\n",
         "d3d9": ""}
NAMES = ("kernel32.dll\tFoo\nkernel32.dll\tBar\nkernel32.dll\tBaz\nkernel32.dll\tQux\nkernel32.dll\tFwd\nmsvcrt.dll\tmemcpy\n"
         "msvcrt.dll\tstrlen\nmsvcrt.dll\tnotserved\nmsvcrt.dll\tmemset\nmsvcrt.dll\t_iob\nmsvcrt.dll\tsprintf\n")
TABLE = B.read_name_table("memcpy\tfnvnx_memcpy\tfunc\nstrlen\tfnvnx_strlen\tfunc\nmemset\tfnvnx_memset\tfunc\n")
SYMS = {"Foo": [("GLOBAL", "FUNC")],                 # certified
        "Bar": [("UND", "NOTYPE")],                  # unresolved-symbol
        "Baz": [("WEAK", "FUNC")],                   # weak
        "Qux": [("GLOBAL", "OBJECT")],               # not-a-function
        "Fwd": [("GLOBAL", "FUNC")],
        "fnvnx_memcpy": [("GLOBAL", "FUNC")],        # lookup, certified
        "fnvnx_strlen": [("GLOBAL", "OBJECT")],      # lookup-miss (wrong type)
        "fnvnx_iob": [("GLOBAL", "OBJECT")],
        "fnvnx_msvcrt_va_sprintf": [("GLOBAL", "FUNC")]}   # memset: table entry names no symbol at all -> lookup-miss


VA = B.read_va("sprintf\tfnvnx_msvcrt_va_sprintf\n")
TABLE = B.read_name_table("memcpy\tfnvnx_memcpy\tfunc\nstrlen\tfnvnx_strlen\tfunc\nmemset\tfnvnx_memset\tfunc\n_iob\tfnvnx_iob\tdata\n")


def derive(names=NAMES, specs=SPECS, syms=SYMS, table=TABLE, extra=(), va=VA):
    return B.derive(names, lambda d: specs[d], set(syms), table, list(extra), va)


rows, errs, _ = derive()
check("derive: no errors", errs == [], errs)
bind = {(r[0], r[1]): r[7] for r in rows}
check("derive: a name of the symbol table binds sym", bind[("kernel32.dll", "Foo")] == "sym")
check("derive: a spec forward is followed (signature of the target)", [r[5] for r in rows if r[1] == "Fwd"] == ["i"])
check("derive: name in the table is lookup", bind[("msvcrt.dll", "memcpy")] == "lookup")
check("derive: msvcrt name outside the table is none", bind[("msvcrt.dll", "notserved")] == "none")
check("derive: data export binds data", bind[("msvcrt.dll", "_iob")] == "data")
check("derive: va table", bind[("msvcrt.dll", "sprintf")] == "sym")
check("derive: sorted", rows == sorted(rows, key=lambda r: (r[0].lower(), r[1])))

res = dict(I.judge(rows, SYMS, TABLE, {}, {}))
want = {"kernel32.dll!Foo": None, "kernel32.dll!Bar": "unresolved-symbol", "kernel32.dll!Baz": "weak", "kernel32.dll!Qux": "not-a-function",
        "kernel32.dll!Fwd": None, "msvcrt.dll!memcpy": None, "msvcrt.dll!strlen": "lookup-miss", "msvcrt.dll!memset": "lookup-miss",
        "msvcrt.dll!notserved": "lookup-miss", "msvcrt.dll!_iob": None, "msvcrt.dll!sprintf": None}
check("judge: every class of the synthetic table", res == want, {k: (res.get(k), v) for k, v in want.items() if res.get(k) != v})

# none: a name no symbol table knows
rows2, _, _ = derive(names="kernel32.dll\tFoo\n", syms={}, table={})
check("judge: none (no symbol of that name anywhere)", I.judge(rows2, {}, {}, {}, {}) == [("kernel32.dll!Foo", "none")])

# l3 and external: only a root that defines the symbol certifies
l3rows, _, _ = derive(names="msvcrt.dll\t_CIpow\n", specs={"msvcrt": "1 cdecl _CIpow()\n"}, syms={}, table={})
check("derive: l3 symbol", l3rows[0][6] == "fnvl_msvcrt_CIpow" and l3rows[0][7] == "l3", l3rows)
check("judge: l3 without a providing root is external", I.judge(l3rows, {}, {}, {}, {}) == [("msvcrt.dll!_CIpow", "external")])
check("judge: l3 provided by another root", I.judge(l3rows, {}, {}, {"fnvl_msvcrt_CIpow": [("GLOBAL", "FUNC")]}, {}) == [("msvcrt.dll!_CIpow", None)])
check("judge: l3 defined weak elsewhere is external", I.judge(l3rows, {}, {}, {"fnvl_msvcrt_CIpow": [("WEAK", "FUNC")]}, {}) == [("msvcrt.dll!_CIpow", "external")])

# extra rows: replace, own and other-owner, duplicates and bad columns are errors
ex = "d3d9.dll\tDirect3DCreate9\t\tstdcall\ti\ti\tD3D\tsym\n"
r3, e3, of3 = derive(extra=[ex])
check("derive: extra row added", ("d3d9.dll", "Direct3DCreate9") in {(r[0], r[1]) for r in r3} and e3 == [] and of3 == {("d3d9.dll", "Direct3DCreate9"): 0})
check("judge: row of another owner needs the other root", I.judge([r for r in r3 if r[0] == "d3d9.dll"], SYMS, TABLE, {}, of3) == [("d3d9.dll!Direct3DCreate9", "external")])
check("judge: row of another owner certified by the other root",
      I.judge([r for r in r3 if r[0] == "d3d9.dll"], SYMS, TABLE, {"D3D": [("GLOBAL", "FUNC")]}, of3) == [("d3d9.dll!Direct3DCreate9", None)])
check("derive: duplicate extra row is an error", derive(extra=[ex, ex])[1] != [])
check("derive: extra row with a bad column count is an error", derive(extra=["a\tb\n"])[1] != [])
check("derive: stub in the spec is an error", derive(names="kernel32.dll\tFoo\n", specs={"kernel32": "1 stub Foo\n"})[1] != [])
check("derive: name missing from the spec is an error", derive(names="kernel32.dll\tMissing\n")[1] != [])

# the data row symbol comes from the name table, the va forwarder from the va record; either missing is an error, never a default
check("derive: a data export without a data row in the name table is an error", derive(table={k: v for k, v in TABLE.items() if k != "_iob"})[1] != [])
check("derive: a varargs export without a va row is an error", derive(names="msvcrt.dll\tprintf\n", specs=dict(SPECS, msvcrt="1 varargs printf(str)\n"), va={})[1] != [])
for bad in ("a\n", "a\tb\tc\n", "a\tb\na\tc\n", "a\t1b\n"):
    try:
        B.read_va(bad)
        check("va record bad input %r is an error" % bad, False)
    except ValueError:
        check("va record bad input %r is an error" % bad, True)

# bad name tables are errors, never silently empty
for bad in ("a\tb\n", "a\tb\tproc\n", "a\tb\tfunc\na\tc\tfunc\n", "a\t\tfunc\n"):
    try:
        B.read_name_table(bad)
        check("name table bad input %r is an error" % bad, False)
    except ValueError:
        check("name table bad input %r is an error" % bad, True)
check("name table: empty text is the empty table", B.read_name_table("") == {})

# green case
g = [("a.dll", "F", "", "stdcall", "i", "", "F", "sym")]
check("judge: green case", I.judge(g, {"F": [("GLOBAL", "FUNC")]}, {}, {}, {}) == [("a.dll!F", None)])
check("judge: UNIQUE counts as global", I.judge(g, {"F": [("UNIQUE", "FUNC")]}, {}, {}, {}) == [("a.dll!F", None)])

# the rows table (stage A): syms=None binds by rule, never reads a symbol table, and its keys equal the keys of every judged derivation
import iat  # noqa: E402
ra, ea, _ = B.derive(NAMES, lambda d: SPECS[d], None, TABLE, [], VA)
check("rows table: no errors", ea == [], ea)
check("rows table: every symbol row binds sym, data binds data (iob), msvcrt binds lookup by the name table, a name outside it is none",
      {(r[1]): r[7] for r in ra if r[0] == "kernel32.dll"} == {"Foo": "sym", "Bar": "sym", "Baz": "sym", "Qux": "sym", "Fwd": "sym"}
      and {r[1]: r[7] for r in ra if r[0] == "msvcrt.dll"}["_iob"] == "data" and {r[1]: r[7] for r in ra if r[0] == "msvcrt.dll"}["notserved"] == "none"
      and {r[1]: r[7] for r in ra if r[0] == "msvcrt.dll"}["memcpy"] == "lookup", ra)
rb, _, _ = derive()
check("rows table: columns dll, name, ordinal, conv, ret, args equal those of the judged derivation (the symbols judge, they do not shape the rows)",
      [r[:6] for r in ra] == [r[:6] for r in rb], (ra, rb))
check("rows table: the import keys of the rows table equal the keys of the judged one (a function of dll, name, conv only)",
      iat.system_keys([(r[0], r[1], r[3], r[2]) for r in ra]) == iat.system_keys([(r[0], r[1], r[3], r[2]) for r in rb]))
import subprocess  # noqa: E402
import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    for dll, text in SPECS.items():
        open(os.path.join(td, dll + ".spec"), "w").write(text)
    open(os.path.join(td, "names"), "w").write(NAMES)
    open(os.path.join(td, "table"), "w").write("memcpy\tfnvnx_memcpy\tfunc\nstrlen\tfnvnx_strlen\tfunc\nmemset\tfnvnx_memset\tfunc\n_iob\tfnvnx_iob\tdata\n")
    open(os.path.join(td, "va"), "w").write("sprintf\tfnvnx_msvcrt_va_sprintf\n")
    r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(HERE), "bridgegen.py"), "--names", td + "/names", "--specs", td, "--name-table", td + "/table", "--va", td + "/va", "--out", td + "/bridge.tsv"],
                       capture_output=True, text=True)
    got = open(td + "/bridge.tsv").read() if r.returncode == 0 else ""
    check("bridgegen: the stage writes the header and the rows of derive(syms=None)", got == "dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n" + "".join("\t".join(x) + "\n" for x in ra), (r.stderr, got[:200]))
print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
