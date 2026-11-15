"""gen_cxx.py: the `record` kind of the COM bridge generator (gen_combridge.py imports it): the C++ objects of the exe's CRT that the platform builds,
generated from platform/crt/cxx_types.tsv (the format is in that file's header; every row cites Wine 9.0).

For each `fn` row: one bridge-table row (dll `!cxx`, name = the MSVC decorated name, ordinal = the row's index, convention and stack words DECODED from
the name by contract/proto.py, the one decoder; a convention outside the bridge grammar is an error) and the thunk nxb_cxx_<index>, which takes `this`
(thiscall: first, from ecx) and the x86 stack words, converts them by the C++ types of the row's signature and calls the body. Row `args` hold the stack
words WITHOUT `this` (the bridge grammar: x86 stack slots); the native thunk takes `this` first (AAPCS64 x0).

For each `class` row: the type_info record, the RTTI chain (base descriptor, base array, hierarchy, locator), the vtable (the locator at [-1], one slot per
fn), and by `cxx` the CatchableType and, for `throw`, the CatchableTypeArray and the ThrowInfo, in the i386 layout of Wine 9.0 cppexcept.h / cxx.h
(32-bit guest addresses, ILP32). The slots that name a native function hold the function's import key, the value the guest dispatcher maps to the thunk:
the key is a function of the whole bridge table (contract/iat.py), so it is resolved by nxcom::cxx_init in the init that resolves every other key of this
bridge, before the first guest use. Every slot is generated from a row, so every slot has a bridge row by construction."""
import os
import re
import sys

LAYOUT = {  # i386 sizes of Wine 9.0's records (cppexcept.h, cxx.h)
    "type_info": 72, "base_desc": 24, "hier": 16, "locator": 20, "cxxti": 28, "table": 16, "throw": 16,
}
NBASE_ARRAY = 10   # rtti_base_array.bases[10]
NTABLE = 3         # cxx_type_info_table.info[3]
RECORD_TYPES = []   # filled by build: the record types of the generated unit


def die(msg):
    sys.stderr.write("gen_cxx: " + msg + "\n")
    sys.exit(2)


def parse(path):
    fns, classes = [], []
    for ln, line in enumerate(open(path).read().split("\n"), 1):
        if not line or line.startswith("#"):
            continue
        c = line.split("\t")
        if c[0] == "fn" and len(c) == 5:
            fns.append(dict(sym=c[1], mangled=c[2], sig=c[3], cite=c[4], line=ln))
        elif c[0] == "class" and len(c) == 10:
            classes.append(dict(name=c[1], mangled=c[2], size=int(c[3]), bases=[] if c[4] == "-" else c[4].split(";"),
                                vtable=[] if c[5] == "-" else c[5].split(";"), cxx=c[6], copy=c[7], dtor=c[8], cite=c[9], line=ln))
        else:
            die("%s:%d: not a fn (5 columns) or class (10 columns) row" % (path, ln))
    for r in fns + classes:
        if not re.fullmatch(r"wine-9\.0:[\w./-]+:[\w:]+", r["cite"]):
            die("%s:%d: the citation %r is not wine-9.0:<path>:<function>" % (path, r["line"], r["cite"]))
    return fns, classes


def split_sig(sig, line):
    m = re.fullmatch(r"(.+?)\((.*)\)", sig)
    if not m:
        die("row %d: signature %r" % (line, sig))
    args = [a.strip() for a in m.group(2).split(",") if a.strip()]
    return m.group(1).strip(), args


def build(path, proto):
    """-> rows, thunks (text), header (text), code (text)"""
    fns, classes = parse(path)
    idx = {}
    rows, thunks = [], []
    for i, f in enumerate(fns):
        if f["sym"] in idx:
            die("fn %s twice" % f["sym"])
        idx[f["sym"]] = i
        d = proto.declared(f["mangled"])
        if d is None or d.get("words") is None:
            die("fn %s: %r is not a decodable function name" % (f["sym"], f["mangled"]))
        cc = d["cc"]
        if cc not in ("thiscall", "stdcall", "cdecl", "fastcall"):
            die("fn %s: calling convention %s is outside the bridge grammar" % (f["sym"], cc))
        if cc == "fastcall" or any(r for r in d["regs"]):
            die("fn %s: register arguments of %s have no thunk rule here" % (f["sym"], cc))
        ret, args = split_sig(f["sig"], f["line"])
        has_this = cc == "thiscall"
        if len(args) != len(d["args"]) + (1 if has_this else 0):
            die("fn %s: signature has %d parameters, the name has %d stack words%s" % (f["sym"], len(args), len(d["args"]), " plus this" if has_this else ""))
        for a, k in zip(args[1 if has_this else 0:], d["args"]):
            if k != "i":
                die("fn %s: argument class %s has no thunk rule" % (f["sym"], k))
            if "*" not in a and a not in ("unsigned", "int", "unsigned int"):
                die("fn %s: type %r is not a word" % (f["sym"], a))
        if has_this and "*" not in args[0]:
            die("fn %s: `this` must be a pointer" % f["sym"])
        rl = "v" if ret == "void" else "i"
        rows.append(("!cxx", f["mangled"], str(i), cc, rl, "".join(d["args"]), "nxb_cxx_%d" % i, "sym"))
        ps = ", ".join("uintptr_t a%d" % k for k in range(len(args)))
        call = "%s(%s)" % (f["sym"], ", ".join("nxcom::word<%s>(a%d)" % (a, k) for k, a in enumerate(args)))
        if rl == "v":
            body = "    %s;\n    return 0;" % call
        else:
            body = "    return (uintptr_t)(uint32_t)(uintptr_t)%s;" % call
        thunks.append('extern "C" uintptr_t nxb_cxx_%d(%s)   /* %s: %s */\n{\n%s\n}' % (i, ps, f["mangled"], f["cite"], body))
    by = {c["name"]: c for c in classes}

    def need_fn(c, s, what):
        if s not in idx:
            die("class %s (row %d): %s %r is not an fn row" % (c["name"], c["line"], what, s))
    hdr = ["struct nxcom_cxx_throw { uint32_t flags; uint32_t dtor; uint32_t custom_handler; const void *table; };   /* cxx_exception_type */",
           "static_assert(sizeof(void *) == 4 && sizeof(nxcom_cxx_throw) == %d, \"cxx_exception_type, i386\");" % LAYOUT["throw"]]
    code = []
    assertions = []
    init = []
    for c in classes:
        for s in c["vtable"]:
            need_fn(c, s, "vtable slot")
        if c["cxx"] not in ("-", "info", "throw"):
            die("class %s: cxx %r" % (c["name"], c["cxx"]))
        if c["cxx"] != "-":
            need_fn(c, c["copy"], "copy constructor")
        if c["cxx"] == "throw":
            need_fn(c, c["dtor"], "destructor")
        for b in c["bases"]:
            if b not in by:
                die("class %s: base %s has no row" % (c["name"], b))
            if c["cxx"] != "-" and by[b]["cxx"] == "-":
                die("class %s: base %s has no cxx info" % (c["name"], b))
        if len(c["bases"]) + 1 > NBASE_ARRAY:
            die("class %s: more bases than rtti_base_array" % c["name"])
        if c["cxx"] == "throw" and len(c["bases"]) + 1 > NTABLE:
            die("class %s: more bases than cxx_type_info_table" % c["name"])
    decl, code = [], []
    if "type_info" not in by or not by["type_info"]["vtable"]:
        die("the class type_info with its vtable is required: every type_info record points to it")

    def obj(ty, name, initv):
        decl.append("extern %s nxcom_cxx_%s;" % (ty, name))
        code.append("%s nxcom_cxx_%s = %s;" % (ty, name, initv))
    for c in classes:
        n = c["name"]
        code.append("/* class %s: %s */" % (n, c["cite"]))
        obj("nxcom_cxx_type_info", "ti_" + n, "{ (const void *)&nxcom_cxx_vt_type_info.slot[0], NULL, \"%s\" }" % c["mangled"])
        obj("nxcom_cxx_base_desc", "bd_" + n, "{ &nxcom_cxx_ti_%s, %d, 0, -1, 0, 64 }" % (n, len(c["bases"])))
        arr = ["nxcom_cxx_bd_" + n] + ["nxcom_cxx_bd_" + b for b in c["bases"]]
        obj("nxcom_cxx_base_array", "ba_" + n, "{ { %s } }" % ", ".join("&" + a for a in arr))
        obj("nxcom_cxx_hier", "hier_" + n, "{ 0, 0, %d, &nxcom_cxx_ba_%s }" % (len(c["bases"]) + 1, n))
        obj("nxcom_cxx_locator", "loc_" + n, "{ 0, 0, 0, &nxcom_cxx_ti_%s, &nxcom_cxx_hier_%s }" % (n, n))
        if c["vtable"]:
            obj("nxcom_cxx_vtable<%d>" % len(c["vtable"]), "vt_" + n, "{ &nxcom_cxx_loc_%s, { 0 } }" % n)
            code.append("const void *const *const nxcom_cxx_%s_vtable = (const void *const *)&nxcom_cxx_vt_%s.slot[0];" % (n, n))
            hdr.append("extern const void *const *const nxcom_cxx_%s_vtable;" % n)
            for j, s in enumerate(c["vtable"]):
                init.append("    nxcom_cxx_vt_%s.slot[%d] = k[%d];" % (n, j, idx[s]))
        if c["cxx"] != "-":
            obj("nxcom_cxx_cxxti", "cxxti_" + n, "{ 0, &nxcom_cxx_ti_%s, 0, -1, 0, %d, 0 }" % (n, c["size"]))
            init.append("    nxcom_cxx_cxxti_%s.copy_ctor = k[%d];" % (n, idx[c["copy"]]))
        if c["cxx"] == "throw":
            infos = ["&nxcom_cxx_cxxti_" + n] + ["&nxcom_cxx_cxxti_" + b for b in c["bases"]]
            infos += ["NULL"] * (NTABLE - len(infos))
            obj("nxcom_cxx_table", "tbl_" + n, "{ %d, { %s } }" % (len(c["bases"]) + 1, ", ".join(infos)))
            code.append("nxcom_cxx_throw nxcom_cxx_%s_throw = { 0, 0, 0, &nxcom_cxx_tbl_%s };" % (n, n))
            hdr.append("extern nxcom_cxx_throw nxcom_cxx_%s_throw;" % n)
            init.append("    nxcom_cxx_%s_throw.dtor = k[%d];" % (n, idx[c["dtor"]]))
    pre = ["struct nxcom_cxx_type_info { const void *vtable; char *name; char mangled[64]; };",
           "struct nxcom_cxx_base_desc { const void *type_info; int32_t num_bases; int32_t this_offset, vbase_descr, vbase_offset; uint32_t attributes; };",
           "struct nxcom_cxx_base_array { const void *bases[%d]; };" % NBASE_ARRAY,
           "struct nxcom_cxx_hier { uint32_t signature, attributes; int32_t array_len; const void *base_classes; };",
           "struct nxcom_cxx_locator { uint32_t signature; int32_t base_class_offset; uint32_t flags; const void *type_descriptor; const void *type_hierarchy; };",
           "struct nxcom_cxx_cxxti { uint32_t flags; const void *type_info; int32_t this_offset, vbase_descr, vbase_offset; uint32_t size; uint32_t copy_ctor; };",
           "struct nxcom_cxx_table { uint32_t count; const void *info[%d]; };" % NTABLE,
           "template <unsigned N> struct nxcom_cxx_vtable { const void *rtti; uint32_t slot[N]; };   /* the locator at [-1] */",
           "static_assert(sizeof(nxcom_cxx_type_info) == %d && sizeof(nxcom_cxx_base_desc) == %d && sizeof(nxcom_cxx_hier) == %d && sizeof(nxcom_cxx_locator) == %d && sizeof(nxcom_cxx_cxxti) == %d && sizeof(nxcom_cxx_table) == %d, \"Wine 9.0 cppexcept.h / cxx.h records, i386\");" %
           (LAYOUT["type_info"], LAYOUT["base_desc"], LAYOUT["hier"], LAYOUT["locator"], LAYOUT["cxxti"], LAYOUT["table"]), ""]
    initfn = ["namespace nxcom {", "unsigned cxx_init()", "{", "    uint32_t k[%d];" % max(len(fns), 1), "    unsigned missing = 0;"]
    for i, f in enumerate(fns):
        initfn.append("    k[%d] = (uint32_t)(uintptr_t)fnvnx_bnd_export_value(\"!cxx\", \"%s\", %d, (const void *)&nxb_cxx_%d);   /* %s */" % (i, f["mangled"], i, i, f["sym"]))
        initfn.append("    if (!k[%d]) missing++;" % i)
    initfn += init + ["    return missing;", "}", "}   // namespace nxcom"]
    RECORD_TYPES[:] = pre
    return rows, thunks, hdr, decl, code, initfn
