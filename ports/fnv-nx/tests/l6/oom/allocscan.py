"""allocscan.py: the structural allocation-result check of tests/l6/oom, derived from the COMPILER's AST (clang -ast-dump=json), no text search.

Allocation functions = every function the compiler marks warn_unused_result and that returns a pointer (obj.h: nxd3d_alloc, nxd3d_gpu_alloc, nx_alloc_face, nx_make, nx_make_plain, NxSparse::add, ...).
Rule (A): the result of every call of an allocation function is (1) returned to the caller, or (2) the operand of a null test, or (3) stored in a variable / member that is null-tested before its next use
(`!p`, `p == null`, `p != null`, a conversion to bool), or (4) passed to a deallocation function (freeing null is defined). Any other use (dereference, member call, argument, discard) is a finding.
A `new` expression of a layer class is a compiler diagnostic (operator new is deprecated: obj.h NX_USE_MAKE), checked by the gcc build of the same gate.
Complements the gcc -fanalyzer run: the analyzer follows data flow but, measured in this tree (tests/l6/oom seeded defects), does not see a null result dereferenced through a base-class member of an
nx_make'd object; this rule is local and exact for that shape.
"""
import json, os, subprocess, sys

DEALLOC = {"nxd3d_free", "nxd3d_gpu_free", "nx_free_face"}   # the layer's deallocation API (obj.h, glue_stream.h)
TRANSPARENT = {"ImplicitCastExpr", "CStyleCastExpr", "ParenExpr", "ExprWithCleanups", "CXXStaticCastExpr", "MaterializeTemporaryExpr", "CXXBindTemporaryExpr", "CXXFunctionalCastExpr", "CXXReinterpretCastExpr", "ConstantExpr"}
CALLS = {"CallExpr", "CXXMemberCallExpr", "CXXOperatorCallExpr"}
NULLISH = {"CXXNullPtrLiteralExpr", "GNUNullExpr"}


class Scan:
    def __init__(self, ast, main_files):
        self.main = main_files   # a predicate on a file path: is this file product code?
        self.fn = {}; self.sites = []; self.findings = []
        self.file = None; self.line = 0
        self._index(ast)

    # ---- locations: clang repeats file / line only when they change, so they are tracked in document order
    def _loc(self, loc):
        if not isinstance(loc, dict): return
        if "spellingLoc" in loc: self._loc(loc["spellingLoc"]); self._loc(loc.get("expansionLoc")); return
        if "file" in loc: self.file = loc["file"]
        if "line" in loc: self.line = loc["line"]

    def _index(self, ast):
        def rec(n):
            if isinstance(n, dict):
                if n.get("kind") in ("FunctionDecl", "CXXMethodDecl", "CXXConstructorDecl", "FunctionTemplateDecl") and "id" in n and n.get("kind") != "FunctionTemplateDecl":
                    attrs = [i.get("kind") for i in n.get("inner", []) if isinstance(i, dict)]
                    q = (n.get("type") or {}).get("qualType", ""); ret = q.split("(")[0]
                    self.fn[n["id"]] = {"name": n.get("name", ""), "alloc": ("WarnUnusedResultAttr" in attrs or "MallocAttr" in attrs) and "*" in ret}
                for v in n.get("inner", []) if isinstance(n.get("inner"), list) else []: rec(v)
        rec(ast)

    def _callee(self, call):
        c = call.get("inner", [None])[0]
        while isinstance(c, dict) and c.get("kind") in TRANSPARENT and c.get("inner"): c = c["inner"][0]
        if not isinstance(c, dict): return None
        d = c.get("referencedDecl") or {}
        did = d.get("id") if d else c.get("referencedMemberDecl")
        return self.fn.get(did), (d.get("name") if d else None)

    # ---- walk
    def run(self, ast):
        def top(n):
            self._loc(n.get("loc")); r = n.get("range") or {}; self._loc(r.get("begin")); self._loc(r.get("end"))
            if n.get("kind") in ("FunctionDecl", "CXXMethodDecl", "CXXConstructorDecl", "CXXDestructorDecl") and any(i.get("kind") == "CompoundStmt" for i in n.get("inner", []) if isinstance(i, dict)):
                self._func(n)
            else:
                for v in n.get("inner", []) if isinstance(n.get("inner"), list) else []:
                    if isinstance(v, dict): top(v)
        top(ast)
        return self

    def _func(self, f):
        pending = {}
        def key(n):   # variable / member designated by n (through casts), else None
            while isinstance(n, dict) and n.get("kind") in TRANSPARENT and n.get("inner"): n = n["inner"][0]
            if not isinstance(n, dict): return None
            if n.get("kind") == "DeclRefExpr": return ("d", (n.get("referencedDecl") or {}).get("id"))
            if n.get("kind") == "MemberExpr": return ("m", n.get("referencedMemberDecl"))
            return None
        def own_file(): return self.main(self.file)
        def up(parents, i):   # index of the first non-transparent ancestor
            while i >= 0 and parents[i].get("kind") in TRANSPARENT: i -= 1
            return i
        def is_test(parents, i, child):   # is `child` (at depth i+1 of parents) the operand of a null test?
            p = parents[i] if i >= 0 else None
            if not p: return False
            k = p.get("kind")
            if k == "UnaryOperator" and p.get("opcode") == "!": return True
            if k == "BinaryOperator" and p.get("opcode") in ("==", "!=", "&&", "||"): return True
            if k in ("IfStmt", "WhileStmt", "ForStmt", "ConditionalOperator"):
                inn = p.get("inner", []); return bool(inn) and (inn[0] is child or (k == "IfStmt" and len([x for x in inn if x is child]) and inn.index(child) <= 1))
            return False
        def visit(n, parents):
            if not isinstance(n, dict): return
            self._loc(n.get("loc")); r = n.get("range") or {}; self._loc(r.get("begin")); self._loc(r.get("end")); here = (self.file, self.line)
            k = n.get("kind"); inn = n.get("inner", []) if isinstance(n.get("inner"), list) else []
            # --- an allocation call
            if k in CALLS:
                cal = self._callee(n)
                if cal and cal[0] and cal[0]["alloc"]:
                    if self.main(here[0]): self.sites.append((here[0], here[1], cal[0]["name"] or cal[1]))
                    i = up(parents, len(parents) - 1); p = parents[i] if i >= 0 else None; pk = p.get("kind") if p else None
                    ok = False; track = None
                    if pk == "ReturnStmt": ok = True
                    elif pk == "VarDecl": track = ("d", p.get("id"))
                    elif pk == "BinaryOperator" and p.get("opcode") == "=" and p["inner"][1] is not None and key(p["inner"][0]) is not None:
                        track = key(p["inner"][0]); j = up(parents, i - 1)   # assignment inside a null test: `if (!(p = alloc()))`
                        if j >= 0 and is_test(parents, j, p): track = None; ok = True
                    elif pk == "CallExpr" and (self._callee(p) or (None, ""))[1] in DEALLOC: ok = True
                    elif is_test(parents, i, n): ok = True
                    if track is not None: pending[track] = here
                    elif not ok and self.main(here[0]): self.findings.append((here[0], here[1], "result of %s is used without a null test" % (cal[0]["name"] or cal[1])))
            # --- a use of a tracked variable / member
            if k in ("DeclRefExpr", "MemberExpr") and pending:
                kk = key(n)
                if kk in pending and not (n.get("isRead") is False):
                    i = up(parents, len(parents) - 1); p = parents[i] if i >= 0 else None; pk = p.get("kind") if p else None
                    tested = False
                    # a conversion to bool is a test; so is == / != / !
                    j = len(parents) - 1
                    while j >= 0 and parents[j].get("kind") in TRANSPARENT: j -= 1
                    if any(c.get("kind") == "ImplicitCastExpr" and c.get("castKind") == "PointerToBoolean" for c in parents[j + 1:]): tested = True
                    elif pk == "UnaryOperator" and p.get("opcode") == "!": tested = True
                    elif pk == "BinaryOperator" and p.get("opcode") in ("==", "!="): tested = True
                    elif pk == "BinaryOperator" and p.get("opcode") == "=" and p["inner"][0] is not None and key(p["inner"][0]) == kk and (p["inner"][0] is n or n in [p["inner"][0]] or any(c is n for c in p["inner"][0].get("inner", []))):
                        tested = True   # reassigned: the new value is tracked by its own allocation
                    elif pk == "ReturnStmt": tested = True
                    elif pk == "CallExpr" and (self._callee(p) or (None, ""))[1] in DEALLOC: tested = True
                    if tested: pending.pop(kk, None)
                    else:
                        if self.main(here[0]): self.findings.append((here[0], here[1], "allocation result stored at line %d is used before a null test" % pending[kk][1]))
                        pending.pop(kk, None)
            for v in inn: visit(v, parents + [n])
        body = [i for i in f["inner"] if isinstance(i, dict) and i.get("kind") == "CompoundStmt"][0]
        visit(body, [])


def ast_of(clang, args, src):
    r = subprocess.run([clang] + args + ["-fsyntax-only", "-Xclang", "-ast-dump=json", src], capture_output=True, text=True)
    try: return json.loads(r.stdout)
    except Exception: return None


def scan(clang, args, src, main_files):
    a = ast_of(clang, args, src)
    if a is None: return None
    s = Scan(a, main_files).run(a)
    return s.sites, s.findings


if __name__ == "__main__":
    print("usage: imported by test_oom.py")
