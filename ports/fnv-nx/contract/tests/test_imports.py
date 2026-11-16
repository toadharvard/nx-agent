#!/usr/bin/env python3
"""Tests for contract/imports.py and contract/bridgetable.py (gate `imports`): synthetic symbol tables, specs and tables, every uncertified
class, the green case and bad input. Plain script: PASS/FAIL lines, exit 0 on pass."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): contract.X by its qualified name
from fnvnx.contract import bridgetable as B  # noqa: E402
from fnvnx.contract import imports as I  # noqa: E402

FAILED = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)[:400]))
    if not ok:
        FAILED.append(name)


SPECS = {"kernel32": "1 stdcall Foo(long ptr)\n2 stdcall Bar()\n3 stdcall Baz()\n4 stdcall Qux()\n5 stdcall Fwd(long) other.Real\n",
         "other": "1 stdcall Real(long)\n",
         "msvcrt": "1 cdecl memcpy(ptr ptr long)\n2 cdecl strlen(str)\n3 cdecl notserved(long)\n4 cdecl memset(ptr long long)\n"
                   "5 extern _iob\n6 varargs sprintf(str str)\n",
         "d3d9": ""}
NAMES = ("kernel32.dll\tFoo\nkernel32.dll\tBar\nkernel32.dll\tBaz\nkernel32.dll\tQux\nkernel32.dll\tFwd\nmsvcrt.dll\tmemcpy\n"
         "msvcrt.dll\tstrlen\nmsvcrt.dll\tnotserved\nmsvcrt.dll\tmemset\nmsvcrt.dll\t_iob\nmsvcrt.dll\tsprintf\n")
# the records (exports.tsv): the definer of every function row, with the return letter of its declared type; notserved has none
EXPORTS = {("kernel32.dll", "Foo"): ("Foo", "func", "i", "-"), ("kernel32.dll", "Bar"): ("Bar", "func", "v", "-"), ("kernel32.dll", "Baz"): ("Baz", "func", "i", "-"),
           ("kernel32.dll", "Qux"): ("Qux", "func", "i", "-"), ("other.dll", "Real"): ("Fwd", "func", "q", "-"),
           ("msvcrt.dll", "memcpy"): ("fnvnx_memcpy", "func", "i", "-"), ("msvcrt.dll", "strlen"): ("fnvnx_strlen", "func", "i", "-"),
           ("msvcrt.dll", "memset"): ("fnvnx_memset", "func", "i", "-"), ("msvcrt.dll", "_iob"): ("fnvnx_iob", "data", "-", "-"),
           ("msvcrt.dll", "sprintf"): ("fnvnx_msvcrt_va_sprintf", "func", "i", "-")}
# who defines what over the link (the members of the link the root enters): judge checks a link row against these
LINK = {"Foo": {"platform": [("GLOBAL", "FUNC")]},            # certified
        "Bar": {"platform": [("UND", "NOTYPE")]},             # unresolved-symbol is not a definer: external
        "Baz": {"platform": [("WEAK", "FUNC")]},              # weak: external
        "Qux": {"platform": [("GLOBAL", "OBJECT")]},          # defined, the wrong type: external
        "Fwd": {"platform": [("GLOBAL", "FUNC")], "lift/rt": [("GLOBAL", "FUNC")]},   # two members: multiple-definers
        "fnvnx_memcpy": {"platform": [("GLOBAL", "FUNC")]},
        "fnvnx_strlen": {"platform": [("GLOBAL", "OBJECT")]},
        "fnvnx_iob": {"platform": [("GLOBAL", "OBJECT")]},
        "fnvnx_msvcrt_va_sprintf": {"lift/rt": [("GLOBAL", "FUNC")]}}   # defined by another member: green
VA = B.read_va("sprintf\tfnvnx_msvcrt_va_sprintf\n")


def spec_of(specs):
    def f(d):
        if d not in specs:
            raise FileNotFoundError(d)
        return specs[d]
    return f


def derive(names=NAMES, specs=SPECS, syms=(), extra=(), va=VA, exports=EXPORTS):
    return B.derive(names, spec_of(specs), set(syms), list(extra), va, exports)


rows, errs, _ = derive()
check("derive: the one row without a record is the only error", errs == ["msvcrt.dll!notserved: no export record: the definer is not declared (FNVNX_EXPORT), so the native symbol and the return type have no source"], errs)
rows, errs, _ = derive(names=NAMES.replace("msvcrt.dll\tnotserved\n", ""))
check("derive: no errors", errs == [], errs)
bind = {(r[0], r[1]): r[7] for r in rows}
ret = {(r[0], r[1]): r[4] for r in rows}
check("derive: a row with a record binds link, symbol and ret from the record", bind[("kernel32.dll", "Foo")] == "link" and ret[("kernel32.dll", "Bar")] == "v" and ret[("msvcrt.dll", "memcpy")] == "i")
check("derive: a spec forward is followed (signature of the target, record of the forward chain)", [(r[5], r[4], r[6]) for r in rows if r[1] == "Fwd"] == [("i", "q", "Fwd")])
check("derive: a data export binds link with ret -", bind[("msvcrt.dll", "_iob")] == "link" and ret[("msvcrt.dll", "_iob")] == "-")
check("derive: the va record agrees with the record (the forwarder is the exported symbol)", bind[("msvcrt.dll", "sprintf")] == "link")
check("derive: sorted", rows == sorted(rows, key=lambda r: (r[0].lower(), r[1])))

res = dict(I.judge(rows, {}, LINK, {}))
want = {"kernel32.dll!Foo": None, "kernel32.dll!Bar": "external", "kernel32.dll!Baz": "external", "kernel32.dll!Qux": "external",
        "kernel32.dll!Fwd": "multiple-definers", "msvcrt.dll!memcpy": None, "msvcrt.dll!strlen": "external", "msvcrt.dll!memset": "external",
        "msvcrt.dll!_iob": None, "msvcrt.dll!sprintf": None}
check("judge: every class over the members of the link", res == want, {k: (res.get(k), v) for k, v in want.items() if res.get(k) != v})
check("judge: a link row nobody defines is external", I.judge(rows[:1], {}, {}, {}) == [("kernel32.dll!Bar", "external")])
g = [("a.dll", "F", "", "stdcall", "i", "", "F", "sym")]
check("judge: a sym row (another owner's file) is proven by the root's own symbols", I.judge(g, {"F": [("GLOBAL", "FUNC")]}, {}, {}) == [("a.dll!F", None)])
check("judge: UNIQUE counts as global", I.judge(g, {"F": [("UNIQUE", "FUNC")]}, {}, {}) == [("a.dll!F", None)])
check("judge: a defined WEAK symbol is not a body", I.judge(g, {"F": [("WEAK", "FUNC")]}, {}, {}) == [("a.dll!F", "weak")])
check("judge: only referenced is unresolved-symbol", I.judge(g, {"F": [("UND", "NOTYPE")]}, {}, {}) == [("a.dll!F", "unresolved-symbol")])
check("judge: the wrong type is not-a-function", I.judge(g, {"F": [("GLOBAL", "OBJECT")]}, {}, {}) == [("a.dll!F", "not-a-function")])
check("judge: no symbol of that name is none", I.judge(g, {}, {}, {}) == [("a.dll!F", "none")])

# extra rows: replace, own and other-owner, duplicates and bad columns are errors
ex = "d3d9.dll\tDirect3DCreate9\t\tstdcall\ti\ti\tD3D\tsym\n"
NN = NAMES.replace("msvcrt.dll\tnotserved\n", "")
r3, e3, of3 = derive(names=NN, extra=[ex])
check("derive: extra row added", ("d3d9.dll", "Direct3DCreate9") in {(r[0], r[1]) for r in r3} and e3 == [] and of3 == {("d3d9.dll", "Direct3DCreate9"): 0})
check("judge: row of another owner needs the other root", I.judge([r for r in r3 if r[0] == "d3d9.dll"], {}, {}, of3) == [("d3d9.dll!Direct3DCreate9", "external")])
check("judge: row of another owner certified by the other root",
      I.judge([r for r in r3 if r[0] == "d3d9.dll"], {}, {"D3D": {"a": [("GLOBAL", "FUNC")]}}, of3) == [("d3d9.dll!Direct3DCreate9", None)])
check("derive: duplicate extra row is an error", derive(names=NN, extra=[ex, ex])[1] != [])
check("derive: extra row with a bad column count is an error", derive(names=NN, extra=["a\tb\n"])[1] != [])
check("derive: stub in the spec is an error", derive(names="kernel32.dll\tFoo\n", specs={"kernel32": "1 stub Foo\n"})[1] != [])
check("derive: name missing from the spec is an error", derive(names="kernel32.dll\tMissing\n")[1] != [])

# the data row symbol comes from the record, else its own name when the product defines it (bind data), else none; a varargs export needs its va row
NOREC = {k: v for k, v in EXPORTS.items() if k[1] != "_iob"}
check("derive: a data export without a record and without a symbol of its own name is bind none", [(r[6], r[7]) for r in derive(exports=NOREC)[0] if r[1] == "_iob"] == [("-", "none")])
check("derive: a data export without a record binds the symbol of its own name when the product defines it", [(r[6], r[7]) for r in derive(exports=NOREC, syms=["_iob"])[0] if r[1] == "_iob"] == [("_iob", "data")])
check("derive: a varargs export without a va row is an error", any("no forwarder in the va record" in e for e in derive(names="msvcrt.dll\tsprintf\n", va={})[1]))
check("derive: a va row naming another symbol than the record is an error", any("the va record names" in e for e in derive(names="msvcrt.dll\tsprintf\n", va={"sprintf": "other"})[1]))
for bad in ("a\n", "a\tb\tc\n", "a\tb\na\tc\n", "a\t1b\n"):
    try:
        B.read_va(bad)
        check("va record bad input %r is an error" % bad, False)
    except ValueError:
        check("va record bad input %r is an error" % bad, True)

# the rows table (stage A) and its keys: a function of dll, name, conv only
from fnvnx.contract import iat  # noqa: E402
ra, ea, _ = derive(names=NAMES.replace("msvcrt.dll\tnotserved\n", ""))
rb, _, _ = derive(names=NAMES.replace("msvcrt.dll\tnotserved\n", ""), exports={k: (v[0] if k[1] == "sprintf" else v[0] + "_other", v[1], "v" if v[2] == "i" else v[2], v[3]) for k, v in EXPORTS.items()})
check("rows table: the import keys do not depend on the symbols or the return letters",
      iat.system_keys([(r[0], r[1], r[3], r[2]) for r in ra]) == iat.system_keys([(r[0], r[1], r[3], r[2]) for r in rb]))
import subprocess  # noqa: E402
import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    for dll, text in SPECS.items():
        open(os.path.join(td, dll + ".spec"), "w").write(text)
    open(os.path.join(td, "names"), "w").write(NAMES.replace("msvcrt.dll\tnotserved\n", ""))
    open(os.path.join(td, "va"), "w").write("sprintf\tfnvnx_msvcrt_va_sprintf\n")
    open(os.path.join(td, "exports"), "w").write("".join("\t".join((m, n) + v) + "\n" for (m, n), v in sorted(EXPORTS.items())))
    r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(HERE), "bridgegen.py"), "--names", td + "/names", "--specs", td, "--va", td + "/va", "--exports", td + "/exports", "--out", td + "/bridge.tsv"],
                       capture_output=True, text=True)
    got = open(td + "/bridge.tsv").read() if r.returncode == 0 else ""
    check("bridgegen: the stage writes the header and the rows of derive", got == "dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n" + "".join("\t".join(x) + "\n" for x in ra), (r.stderr[-200:], got[:200]))

# the entry kind: the record says the body works on the guest state; the row binds `entry`, ret `-`, conv and args stay the spec's; judged like a link row
ENT = {**EXPORTS, ("kernel32.dll", "Bar"): ("fnvl_Bar", "entry", "-", "-")}
re_, ee, _ = derive(names=NN, exports=ENT)
brow = [r for r in re_ if r[1] == "Bar"][0]
check("derive: an entry record gives bind entry, ret -, the spec's conv", ee == [] and brow[7] == "entry" and brow[4] == "-" and brow[3] == "stdcall" and brow[6] == "fnvl_Bar", (ee, brow))
check("derive: an entry record for a data import is an error", derive(names="msvcrt.dll\t_iob\n", exports={("msvcrt.dll", "_iob"): ("x", "entry", "-", "-")})[1] != [])
check("judge: an entry row is certified by exactly one FUNC definer over the link", I.judge([brow], {}, {"fnvl_Bar": {"lift/rt": [("GLOBAL", "FUNC")]}}, {}) == [("kernel32.dll!Bar", None)]
      and I.judge([brow], {}, {}, {}) == [("kernel32.dll!Bar", "external")]
      and I.judge([brow], {}, {"fnvl_Bar": {"a": [("GLOBAL", "FUNC")], "b": [("GLOBAL", "FUNC")]}}, {}) == [("kernel32.dll!Bar", "multiple-definers")])
# a module without a reference spec (a vendor SDK's): the signature is the definer's declared type (ret and args of the record), never a constant of derive
GAL = {("galaxywrp.dll", "SteamAPI_RegisterCallback"): ("SteamAPI_RegisterCallback", "func", "v", "ii"), ("galaxywrp.dll", "SteamAPI_Shutdown"): ("SteamAPI_Shutdown", "func", "v", "-"),
       ("galaxywrp.dll", "SteamAPI_Init"): ("SteamAPI_Init", "func", "i", "-")}
gn = "".join("galaxywrp.dll\t%s\n" % k[1] for k in GAL)
rg, eg, _ = derive(names=gn, exports=GAL)
check("derive: a module with no spec takes ret and args from its records", eg == [] and {r[1]: (r[3], r[4], r[5], r[7]) for r in rg} == {
    "SteamAPI_RegisterCallback": ("cdecl", "v", "ii", "link"), "SteamAPI_Shutdown": ("cdecl", "v", "", "link"), "SteamAPI_Init": ("cdecl", "i", "", "link")}, (eg, rg))
check("derive: an import of a module with no spec and no record is an error", derive(names=gn, exports={})[1] != [])
print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
