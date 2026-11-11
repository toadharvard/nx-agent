#!/usr/bin/env python3
"""Tests for verify/nostub.py and verify/gate_nostub.py. Plain script: exit 0 on pass, one PASS/FAIL line per case."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)

def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(VERIFY, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, VERIFY)
    spec.loader.exec_module(mod)
    return mod

ns, verdict = load("nostub"), load("verdict")
FAILED = []

def M(s):
    """Policy words are written with a `¬` inside so that this test file passes its own lint."""
    return s.replace("¬", "")

def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(info)[:300]))
    if not ok:
        FAILED.append(name)

def case(name, path, src, want, terms=()):
    """want: list of (rule, token) in finding order; line numbers are checked separately."""
    fs, used = ns.scan_text(path, M(src), terms)
    got = [(r, t) for _, r, t in fs]
    want = [(r, M(t)) for r, t in want]
    check(name, sorted(got) == sorted(want), "got=%r want=%r" % (got, want))
    return fs, used

# ---- R1
case("R1 c: abort in code", "a.c", "void f(void) { abort(); }", [("R1", "abort")])
case("R1 c: abort in comment and string is not code", "a.c", 'void f(void) { /* abort() */ puts("abort("); }', [])
case("R1 c: raw string holding abort( is not R1", "a.cc", 'const char *s = R"x(abort(); " )x"; int g;', [])
case("R1 c: plain raw string with a quote inside", "a.cc", 'auto s = R"(a"b)"; void h() { abort(); }', [("R1", "abort")])
case("R1 c: __builtin_trap", "a.c", "void f(void) { __builtin_trap(); }", [("R1", "__builtin_trap")])
case("R1 c: raise(SIGABRT) yes, raise(SIGINT) no", "a.c", "void f(void) { raise(SIGABRT); raise(SIGINT); }", [("R1", "raise")])
case("R1 c: assert(0) and assert(string) yes, assert(x) no", "a.c", 'void f(int x) {\nassert(0);\nassert("no"); assert(x); }',
     [("R1", "assert"), ("R1", "assert")])
case("R1 c++: std::terminate and terminate(", "a.cc", "void f() { std::terminate();\nterminate(); o.terminate(); }",
     [("R1", "terminate"), ("R1", "terminate")])
case("R1 c: asm string with brk", "a.c", 'void f(void) { __asm__ volatile("nop; brk #0"); }', [("R1", "asm-trap")])
case("R1 c: asm string without trap", "a.c", 'void f(void) { __asm__ volatile("nop"); }', [])
case("R1 asm: udf and hlt after a label", "a.s", "f: udf #0\n\thlt\n\tmov x0, #1\n", [("R1", "udf"), ("R1", "hlt")])
case("R1 asm: hlt as an operand is not an instruction", ".s", "mov x0, hlt_val\n", [])
case("R1 c: _Exit outside a terminator", "a.c", "void g(int c) { _Exit(c); }", [("R1", "_Exit")])
fs, used = case("R1 c: _Exit inside the declared terminator body", "a.c", "namespace n { void Die(int c) { _Exit(c); } }\n"
                "void g(int c) { }\n", [("R6", "g")], terms=("Die",))
check("R1 c: terminator coverage counted", used == {"Die": 1}, used)
case("R1 py: os.abort()", "a.py", "import os\nos.abort()\n", [("R1", "abort")])
# ---- R2
case("R2 c: E_NOTIMPL", "a.c", "int f(int x) { return x ? E_NOTIMPL : 0; }", [("R2", "E_NOTIMPL")])
case("R2 c: D3DERR_NOTAVAILABLE is a contract answer", "a.c", "int f(int x) { return x ? D3DERR_NOTAVAILABLE : x; }", [])
case("R2 py: raise NotImplementedError", "a.py", "def f(x):\n    if x:\n        raise NotImplementedError('x')\n    return x\n",
     [("R2", "NotImplementedError"), ("R5i", "NO¬T IMPLEMENTED")])
case("R2 py: catching it is not R2", "a.py", "def f(x):\n    try:\n        return x()\n    except NotImplementedError:\n        raise\n", [("R5i", "NO¬T IMPLEMENTED")])
# ---- R3
case("R3 c: weak in attribute", "a.c", "__attribute__((weak)) void f(void);", [("R3", "weak")])
case("R3 c: weak inside a longer attribute run", "a.c", "__attribute__((noinline, __weak__)) void f(void);", [("R3", "__weak__")])
case("R3 c: alias attribute", "a.c", 'void g(void) __attribute__((alias("f")));', [("R3", "alias")])
case("R3 c: other attribute", "a.c", "__attribute__((noinline)) void f(void);", [])
case("R3 c: pragma weak", "a.c", "#pragma weak f\nvoid f(void);\n", [("R3", "#pragma weak")])
case("R3 asm: .weak directive", "a.S", ".weak foo\n.global bar\n", [("R3", ".weak")])
case("R3 ld: PROVIDE", "a.ld", "PROVIDE(__x = .);\nSECTIONS { .text : { *(.text) } }\n", [("R3", "PROVIDE")])
case("R3 sh: ld option in a command", "a.sh", "ld a.o b.o --defs¬ym x=1\n", [("R3", "--defs¬ym")])
case("R3 sh: ld option in a comment is not R3", "a.sh", "# --defs¬ym is forbidden\nld a.o\n", [])
case("R3 mk: z undefs", "Makefile", "LDFLAGS += -z undef¬s\n", [("R3", "-z undef¬s")])
case("R3 c: option in string", "a.c", 'const char *o = "-Wl,--unresolved¬-symbols=ignore-all";', [("R3", "--unresolved¬-symbols")])
case("R3 mk: -z relro is fine", "Makefile", "LDFLAGS += -z relro\n", [])
# ---- R4
case("R4 c++: catch(...) with throw", "a.cc", "void f() { try { g(); } catch (...) { cleanup(); throw; } }", [])
case("R4 c++: catch(...) without throw", "a.cc", "void f() { try { g(); } catch (...) { log(); } }", [("R4", "catch(...)")])
case("R4 c++: catch of a type with an empty body is not R4/R6", "a.cc", "void f() { try { g(); } catch (const E&) {} }", [])
case("R4 c: fault signal with longjmp", "a.c", "void f(void) { signal(SIGSEGV, h); } void g(void) { longjmp(b, 1); }",
     [("R4", "signal+longjmp")])
case("R4 c: fault signal without longjmp", "a.c", "void f(void) { signal(SIGSEGV, h); }", [])
case("R4 py: except without raise", "a.py", "def f():\n    try:\n        g()\n    except Exception:\n        print(1)\n", [("R4", "except Exception")])
case("R4 py: bare except without raise", "a.py", "try:\n    g()\nexcept:\n    h()\n", [("R4", "except ")])
case("R4 py: except with raise", "a.py", "try:\n    g()\nexcept Exception:\n    raise\n", [])
case("R4 py: narrow except with pass", "a.py", "try:\n    g()\nexcept ValueError:\n    pass\n", [])
case("R4 py: pass-only function", "a.py", "def f(x):\n    pass\n", [("R4", "def f")])
case("R4 py: ellipsis-only function with docstring", "a.py", 'def f(x):\n    """doc"""\n    ...\n', [("R4", "def f")])
case("R4 py: function with a body", "a.py", "def f(x):\n    '''doc'''\n    return x + 1\n", [])
case("R4 sh: || true and set +e", "a.sh", "make || true\nset +e\nrm x || :\n", [("R4", "|| true"), ("R4", "set +e"), ("R4", "|| :")])
case("R4 sh: || exit 1, set -e and quoted text", "a.sh", "make || exit 1\nset -e\necho 'x || true'\n", [])
# ---- R5
case("R5 c: marker in a comment", "a.c", "/* TO¬DO later */ int x;", [("R5", "TO¬DO")])
case("R5 c: marker in a string", "a.c", 'const char *s = "not impl¬emented yet";', [("R5", "NO¬T IMPLEMENTED")])
case("R5 c: marker after an escape in a string", "a.c", 'const char *s = "\\nST¬UB";', [("R5", "ST¬UB")])
case("R5i c: marker inside an identifier", "a.c", "int fnvnx_st¬ub_nulldev;\nint Stu¬bDevice;\nint fall¬backPath;",
     [("R5i", "ST¬UB"), ("R5i", "ST¬UB"), ("R5i", "FALL¬BACK")])
case("R5 c: marker in code is not a comment finding but an identifier one", "a.c", "int TO¬DO;", [("R5i", "TO¬DO")])
case("R5i c: nostub is one word, a marker in a string is not R5i", "a.c", 'int nostub = 0; /* ok */ const char *s = "x";', [])
case("R5 c: #if 0 yes, #if 1 and #if X no", "a.c", "#if 0\nint a;\n#endif\n#if 1\n#endif\n#if X\n#endif\n", [("R5", "#if 0")])
case("R5 c: #if false", "a.c", "#if false\n#endif\n", [("R5", "#if false")])
case("R5 py: marker in a comment", "a.py", "x = 1  # FIX¬ME\n", [("R5", "FIX¬ME")])
case("R5 sh: marker in a comment", "a.sh", "echo a # HA¬CK\n", [("R5", "HA¬CK")])
case("R5 sh: marker in a quoted string", "a.sh", 'echo "for no¬w"\n', [("R5", "FO¬R NOW")])
case("R5 mk: marker in a comment", "Makefile", "all:\n\techo a # WORK¬AROUND\n", [("R5", "WORK¬AROUND")])
case("R5 cmake: marker in a comment", "CMakeLists.txt", "# TEMPO¬RARY\nadd_library(x a.c)\n", [("R5", "TEMPO¬RARY")])
case("R5 c: #error text with an apostrophe lexes", "a.c", "#error don't\nint x;\n", [])
case("R5 c: #include name is not code", "a.c", "#include <sys/abort.h>\n", [])
# ---- R6
case("R6 c: empty function", "a.c", "void f(void) {}", [("R6", "f")])
case("R6 c++: empty method with const noexcept", "a.cc", "struct S { int g() const noexcept {} };", [("R6", "g")])
case("R6 c++: = default and = delete are not bodies", "a.cc", "struct S { S() = default; S(const S&) = delete; };", [])
case("R6c c++: empty constructor with member init", "a.cc", "S::S(int a) : m(a), n(2) {}", [("R6c", "n")])
case("R6 c++: access label before a constructor is not R6c", "a.cc", "class A { public: A() {} };", [("R6", "A")])
case("R6 c++: empty lambda", "a.cc", "void f() { auto l = []() {}; }", [("R6", "lambda")])
case("R6k c: return 0 / S_OK / nullptr", "a.cc", "int f() { return 0; } long g() { return S_OK; } void *h() { return nullptr; }",
     [("R6k", "return 0"), ("R6k", "return S_OK"), ("R6k", "return nullptr")])
case("R6k c: return of a variable, of an expression", "a.c", "int f(int x) { return x; } int g(void) { return 1 + 2; }", [])
case("R6 c: control statements are not functions", "a.c", "void f(int x) { if (x) {} while (x) { return 0; } for (;;) {} }", [])
case("R6 c: namespace and struct braces are not functions", "a.cc", "namespace n {} struct S {}; extern \"C\" {}", [])
# ---- lexing, unlexable, lines
fs, _ = ns.scan_text("a.c", "int a; // x \\\n TO" + "DO\nint b = 1'000; char c = '\"'; const char *s = \"/* \"; /* y */ int d;\n")
check("lex c: comment continuation, digit separator, quote char, comment marker in string", fs == [(2, "R5", M("TO¬DO"))], fs)
fs, _ = ns.scan_text("a.c", "int a;\n/* a\nb */\nvoid f(void) { abort(); }\n")
check("lex c: lines after a block comment", fs == [(4, "R1", "abort")], fs)
for nm, p, s in (("unterminated string", "a.c", 'char *s = "abc;\n'), ("unterminated comment", "a.h", "int a; /* x\n"),
                 ("unterminated char", "a.c", "char c = 'x;\n"), ("unterminated raw string", "a.cc", 'auto s = R"x(abc)y\n;\n'),
                 ("python syntax", "a.py", "def f(:\n"), ("shell quote", "a.sh", "echo 'abc\n")):
    fs, _ = ns.scan_text(p, s)
    check("unlexable: " + nm, fs == [(1, "unlexable", "-")], fs)
td = tempfile.mkdtemp()
os.makedirs(os.path.join(td, "t", "sub"))
open(os.path.join(td, "t", "bad.c"), "wb").write(b"int a; /* \xff\xfe */\n")
open(os.path.join(td, "t", "sub", "ok.c"), "w").write("int add(int a, int b) { return a + b; }\n")
open(os.path.join(td, "t", "sub", "z.c"), "w").write("void f(void) { abort(); }\n")
open(os.path.join(td, "t", "note.txt"), "w").write("TO" + "DO\n")
res = ns.check([os.path.join(td, "t")])
check("universe: names filtered, sorted, undecodable file unlexable", [os.path.basename(f) for f in res["files"]] == ["bad.c", "ok.c", "z.c"]
      and [(os.path.basename(x["file"]), x["rule"]) for x in res["findings"]] == [("bad.c", "unlexable"), ("z.c", "R1")], res)
# ---- CLI and determinism
def cli(*a):
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, "nostub.py")] + list(a), capture_output=True, text=True)
    return p.returncode, p.stdout
j1, j2 = os.path.join(td, "1.json"), os.path.join(td, "2.json")
r1, o1 = cli("check", "--json", j1, os.path.join(td, "t"))
r2, o2 = cli("check", "--json", j2, os.path.join(td, "t"))
check("cli: findings exit 2, output sorted lines and summary", r1 == 2 and "z.c:1: R1 abort" in o1 and o1.splitlines()[-1].endswith("2 findings"), o1)
check("cli: two runs give equal stdout and JSON bytes", o1 == o2 and open(j1, "rb").read() == open(j2, "rb").read())
r3, o3 = cli("check", os.path.join(td, "t", "sub", "nonexistent"))
check("cli: missing directory is usage error", r3 == 1)
os.remove(os.path.join(td, "t", "bad.c"))
os.remove(os.path.join(td, "t", "sub", "z.c"))
r4, o4 = cli("check", os.path.join(td, "t"))
check("cli: no findings exit 0", r4 == 0 and o4.splitlines()[-1].endswith("1 files, 0 findings"), o4)
r5, _ = cli("check", "--terminator", "Die", "--json", j1, os.path.join(td, "t"))
check("cli: json reports terminators", r5 == 0 and json.load(open(j1))["terminators"] == {"Die": 0})

# ---- gate (absolute rule)
GATE = os.path.join(VERIFY, "gate_nostub.py")
def gate(files, *args):
    root, out = tempfile.mkdtemp(), tempfile.mkdtemp()
    for rel, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        open(os.path.join(root, rel), "w").write(M(text))
    env = {"PATH": os.environ.get("PATH", ""), "GATE_OUT": out}
    p = subprocess.run([sys.executable, "-I", GATE] + list(args), cwd=root, env=env, capture_output=True, text=True)
    r = json.load(open(os.path.join(out, "nostub.json")))
    return p.returncode, r, verdict._gate_result({"name": "nostub", "result": "nostub.json"}, out, p.returncode)
CLEAN = "int ok(int x) { return x + 1; }\n"
rc, r, st = gate({"d/a.c": CLEAN, "d/e/b.py": "def f(x):\n    return x\n"}, "d", "missing")
check("gate: clean files -> green, verdict checker accepts, missing dir ignored", rc == 0 and r["universe"] == 2 and r["certified"] == 2
      and r["uncertified"] == [] and st[0] == "green", (rc, r, st))
rc, r, st = gate({"d/a.c": CLEAN, "d/z.c": "void z(void) {}\n", "d/k.c": "/* TO¬DO */\n", "d/u.c": "char *s = \"x;\n"}, "d")
check("gate: dirty and unlexable files uncertified, sorted, per_rule/per_dir counts", rc == 2 and r["status"] == "red" and r["universe"] == 4
      and r["certified"] == 1 and r["uncertified"] == ["d/k.c", "d/u.c", "d/z.c"] and r["per_rule"] == {"R5": 1, "R6": 1, "unlexable": 1}
      and r["per_dir"] == {"d": 3} and st[0] == "red", (rc, r))
rc, r, st = gate({}, "d")
check("gate: nothing to check -> green, universe 0", rc == 0 and r["universe"] == 0 and r["status"] == "green", r)
rc, r, st = gate({"d/a.c": "void a(void) { _Exit(1); }\n"}, "d", "--terminator", "a")
check("gate: --terminator covers the exit body and is reported", rc == 0 and r["terminators"] == {"a": 1}, r)
rc, r, st = gate({"d/a.c": "void a(void) { _Exit(1); }\n"}, "d")
check("gate: without the terminator the exit is an R1 finding", rc == 2 and r["per_rule"] == {"R1": 1}, r)
rc, r, st = gate({"d/x.c": "void a(void) {}\n", "e/y.c": CLEAN}, "d", "e")
check("gate: per_dir per directory", r["per_dir"] == {"d": 1, "e": 0}, r)

print("%d failed" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
