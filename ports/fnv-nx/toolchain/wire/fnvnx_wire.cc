// fnvnx_wire.cc - GCC plugin: the WIRE facts of a unit (docs: libnx data-model rule W2).
//
//   cc1 ... -fplugin=<dir>/fnvnx_wire.so -fplugin-arg-fnvnx_wire-out=<dir> [-fplugin-arg-fnvnx_wire-sink=<header suffix>]...
//           [-fplugin-arg-fnvnx_wire-tls=<header suffix>]... unit.c
//
// A type is a WIRE type iff an object of it, or its address converted to a buffer pointer, reaches the kernel or the IPC wire:
//   svc     an argument of a call to a function declared in a sink header (sink=kernel/svc.h): every pointer / 64-bit argument;
//   ipc     an argument of a call to a function declared in a sinkbuf header (sinkbuf=sf/service.h, sf/hipc.h, ...) whose parameter type erased
//           the object (void*, char*, u64): serviceDispatchImpl's in_data/out_data, hipcMakeBuffer's buffer;
//   sfmember a value stored into a member of a struct declared in a sink header (SfBuffer.ptr, the hipc descriptors, ...);
//   tls     a store through, or a memcpy/memmove/memset into, a pointer that derives from a call to a function declared in a tls header
//           (arm/tls.h: armGetTls, the TLS IPC area).
// The type is read from the expression's static type BEFORE its conversion to void*/an integer: the pass follows every sink value
// backwards through conversions, pointer arithmetic, PHIs and ADDR_EXPR to its origin set
//   t = pointee types seen on the way (the object's own type at an ADDR_EXPR, the declared pointee at a load, a parameter, a call result),
//   p = parameters of this function it derives from, r = functions whose result it derives from, unk = origins that carry no type
//       (a void*/integer loaded from memory, inline asm): a count and the places, never a guess.
// One JSON line per fact goes to <out>/<unit>.wire; tools merge them over the call graph (parameter summaries and a fixpoint):
//   {"k":"def","f":F}                                     F is defined in this unit
//   {"k":"sink","f":F,"at":L,"why":W,"t":[..],"p":[..],"r":[..],"u":N}
//   {"k":"edge","f":F,"at":L,"to":[G,j],"t":[..],"p":[..],"r":[..],"u":N}   argument j of a call to G has this origin
//   {"k":"ret","f":F,"t":[..],"p":[..],"r":[..],"u":N}    F returns a value with this origin
//   {"k":"ind","f":F,"at":L}                              an indirect call with a pointer argument: its callee is unknown
// A type is named "<name>@<file>:<line>" (the line of its definition), so the ILP32 and LP64 compiles of the same source agree on it.
#include "gcc-plugin.h"
#include "plugin-version.h"
#include "context.h"
#include "tree.h"
#include "tree-pass.h"
#include "function.h"
#include "basic-block.h"
#include "tree-ssa-alias.h"
#include "internal-fn.h"
#include "gimple-expr.h"
#include "gimple.h"
#include "gimple-iterator.h"
#include "stringpool.h"
#include "diagnostic.h"
#include "builtins.h"
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>

int plugin_is_GPL_compatible;
// a constant that fits: types of variably modified objects (VLAs) have non-constant sizes
static bool fits(const_tree x) { return x && TREE_CODE(x) == INTEGER_CST && tree_fits_uhwi_p(x); }

static std::vector<std::string> sink_files, sinkbuf_files, tls_files;
static std::string out_dir;
static FILE *out_fp;

struct Org {
  std::set<std::string> t, r;
  std::set<int> p;
  int unk = 0;
  bool tls = false;
  bool empty() const { return t.empty() && r.empty() && p.empty() && !unk; }
  void add(const Org &o) { t.insert(o.t.begin(), o.t.end()); r.insert(o.r.begin(), o.r.end()); p.insert(o.p.begin(), o.p.end()); unk += o.unk; tls |= o.tls; }
};

static bool in_files(const char *f, const std::vector<std::string> &v) {
  if (!f) return false;
  size_t n = strlen(f);
  for (auto &s : v) if (n >= s.size() && strcmp(f + n - s.size(), s.c_str()) == 0) return true;
  return false;
}
static std::string jesc(const std::string &s) {
  std::string o;
  for (char c : s) { if (c == '"' || c == '\\') o += '\\'; o += c; }
  return o;
}
static std::string loc_str(location_t l) {
  expanded_location x = expand_location(l);
  char b[64]; snprintf(b, sizeof b, ":%d", x.line);
  return std::string(x.file ? x.file : "?") + b;
}
static std::string func_id(tree d) {
  std::string s = DECL_ASSEMBLER_NAME_SET_P(d) ? IDENTIFIER_POINTER(DECL_ASSEMBLER_NAME(d)) : (DECL_NAME(d) ? IDENTIFIER_POINTER(DECL_NAME(d)) : "?");
  if (!TREE_PUBLIC(d)) s += "@" + std::string(DECL_SOURCE_FILE(d));
  return s;
}
// the object type an expression points at, peeled to the element of arrays; void and function types name nothing
static tree object_type(tree t) {
  while (t && (TREE_CODE(t) == ARRAY_TYPE)) t = TREE_TYPE(t);
  if (!t || TREE_CODE(t) == VOID_TYPE || TREE_CODE(t) == FUNCTION_TYPE) return NULL_TREE;
  return TYPE_MAIN_VARIANT(t);
}
static std::string type_id(tree t) {
  const char *name = "";
  location_t loc = UNKNOWN_LOCATION;
  tree n = TYPE_NAME(t);
  tree stub = TYPE_STUB_DECL(t);
  if (stub && DECL_SOURCE_LOCATION(stub) != UNKNOWN_LOCATION) loc = DECL_SOURCE_LOCATION(stub);
  if (n) {
    if (TREE_CODE(n) == IDENTIFIER_NODE) name = IDENTIFIER_POINTER(n);
    else if (DECL_NAME(n)) name = IDENTIFIER_POINTER(DECL_NAME(n));
    if (loc == UNKNOWN_LOCATION && TREE_CODE(n) == TYPE_DECL) loc = DECL_SOURCE_LOCATION(n);
  }
  const char *kind = TREE_CODE(t) == RECORD_TYPE ? "struct" : TREE_CODE(t) == UNION_TYPE ? "union" : TREE_CODE(t) == POINTER_TYPE ? "ptr" : "scalar";
  char sz[32]; snprintf(sz, sizeof sz, "%d", (int)(TYPE_SIZE(t) && fits(TYPE_SIZE(t)) ? tree_to_uhwi(TYPE_SIZE(t)) : 0));
  return std::string(kind) + " " + name + "@" + (loc == UNKNOWN_LOCATION ? std::string("builtin:") + sz : loc_str(loc));
}
// Only the types whose layout can differ between the data models are facts: struct/union (their members), a pointer object, a `long`.
// int, char, u64, float, enums have one layout in both models.
static std::map<std::string, std::string> layout_lines;
static void record_layout(tree t);
static bool model_dependent_scalar(tree t) {
  return POINTER_TYPE_P(t) || t == long_integer_type_node || t == long_unsigned_type_node;
}
static void add_type(Org &o, tree t) {
  t = object_type(t);
  if (!t) return;
  if (TREE_CODE(t) == RECORD_TYPE || TREE_CODE(t) == UNION_TYPE || model_dependent_scalar(t)) { std::string id = type_id(t); o.t.insert(id); record_layout(t); }
}
static void add_pointee(Org &o, tree ptr_type) { if (ptr_type && POINTER_TYPE_P(ptr_type)) add_type(o, TREE_TYPE(ptr_type)); }

static Org origin(tree t, std::set<tree> &seen);
static Org origin_of_object_address(tree obj, std::set<tree> &seen) {
  Org o;
  tree base = obj;
  while (handled_component_p(base)) base = TREE_OPERAND(base, 0);
  add_type(o, TREE_TYPE(obj));                       // the object whose address it is
  if (TREE_CODE(base) == MEM_REF || TREE_CODE(base) == TARGET_MEM_REF) o.add(origin(TREE_OPERAND(base, 0), seen));   // &p->f: where p comes from
  return o;
}
static Org origin(tree t, std::set<tree> &seen) {
  Org o;
  if (!t) return o;
  if (TREE_CODE(t) == ADDR_EXPR) return origin_of_object_address(TREE_OPERAND(t, 0), seen);
  if (TREE_CODE(t) == INTEGER_CST || TREE_CODE(t) == REAL_CST || TREE_CODE(t) == STRING_CST) return o;
  if (TREE_CODE(t) != SSA_NAME) { o.unk++; return o; }
  if (!seen.insert(t).second) return o;
  gimple *d = SSA_NAME_DEF_STMT(t);
  if (SSA_NAME_IS_DEFAULT_DEF(t)) {
    tree v = SSA_NAME_VAR(t);
    if (v && TREE_CODE(v) == PARM_DECL) {
      int i = 0;
      for (tree a = DECL_ARGUMENTS(current_function_decl); a; a = DECL_CHAIN(a), i++) if (a == v) { o.p.insert(i); break; }
      add_pointee(o, TREE_TYPE(v));
    }
    return o;
  }
  switch (gimple_code(d)) {
  case GIMPLE_PHI:
    for (unsigned i = 0; i < gimple_phi_num_args(d); i++) o.add(origin(gimple_phi_arg_def(d, i), seen));
    return o;
  case GIMPLE_ASM: o.unk++; return o;
  case GIMPLE_CALL: {
    tree fd = gimple_call_fndecl(d);
    if (fd) {
      o.r.insert(func_id(fd));
      if (in_files(DECL_SOURCE_FILE(fd), tls_files)) o.tls = true;
    } else o.unk++;
    add_pointee(o, TREE_TYPE(t));
    for (unsigned i = 0; i < gimple_call_num_args(d); i++) {       // a result derived from an argument (hipcGetBufferAddress(desc)): over-approximated
      tree a = gimple_call_arg(d, i);
      if (POINTER_TYPE_P(TREE_TYPE(a)) || INTEGRAL_TYPE_P(TREE_TYPE(a))) o.add(origin(a, seen));
    }
    return o;
  }
  case GIMPLE_ASSIGN: {
    enum tree_code c = gimple_assign_rhs_code(d);
    tree r1 = gimple_assign_rhs1(d);
    if (gimple_assign_single_p(d) && (c == SSA_NAME || is_gimple_min_invariant(r1))) {
      if (TREE_CODE(r1) == ADDR_EXPR) return origin(r1, seen);
      return c == SSA_NAME ? origin(r1, seen) : o;
    }
    if (gimple_assign_single_p(d)) {                  // a load: the declared type is all there is
      if (POINTER_TYPE_P(TREE_TYPE(t)) && object_type(TREE_TYPE(TREE_TYPE(t)))) add_pointee(o, TREE_TYPE(t));
      else if (POINTER_TYPE_P(TREE_TYPE(t)) || (INTEGRAL_TYPE_P(TREE_TYPE(t)) && TYPE_PRECISION(TREE_TYPE(t)) == 64)) o.unk++;   // an address of no known type
      return o;
    }
    if (CONVERT_EXPR_CODE_P(c) || c == VIEW_CONVERT_EXPR) {
      if (POINTER_TYPE_P(TREE_TYPE(r1))) add_pointee(o, TREE_TYPE(r1));   // the type before the conversion to void*/an integer
      o.add(origin(r1, seen));
      return o;
    }
    for (unsigned i = 1; i < gimple_num_ops(d); i++) {   // pointer arithmetic and integer arithmetic on an address: every operand
      tree x = gimple_op(d, i);
      if (x && (TREE_CODE(x) == SSA_NAME || TREE_CODE(x) == ADDR_EXPR)) o.add(origin(x, seen));
    }
    return o;
  }
  default: o.unk++; return o;
  }
}
static Org origin_of(tree t) { std::set<tree> seen; return origin(t, seen); }

static tree base_ptr(tree ref) {
  while (handled_component_p(ref)) ref = TREE_OPERAND(ref, 0);
  return (TREE_CODE(ref) == MEM_REF || TREE_CODE(ref) == TARGET_MEM_REF) ? TREE_OPERAND(ref, 0) : NULL_TREE;
}
static void emit_set(const Org &o) {
  fprintf(out_fp, ",\"t\":[");
  bool f = true; for (auto &s : o.t) { fprintf(out_fp, "%s\"%s\"", f ? "" : ",", jesc(s).c_str()); f = false; }
  fprintf(out_fp, "],\"p\":["); f = true; for (int i : o.p) { fprintf(out_fp, "%s%d", f ? "" : ",", i); f = false; }
  fprintf(out_fp, "],\"r\":["); f = true; for (auto &s : o.r) { fprintf(out_fp, "%s\"%s\"", f ? "" : ",", jesc(s).c_str()); f = false; }
  fprintf(out_fp, "],\"u\":%d}\n", o.unk);
}
static void fact(const char *k, const std::string &fn, const std::string &at, const char *why, const Org &o, tree to = NULL_TREE, int j = -1) {
  if (o.empty() && strcmp(k, "ret") != 0) return;
  fprintf(out_fp, "{\"k\":\"%s\",\"f\":\"%s\"", k, jesc(fn).c_str());
  if (!at.empty()) fprintf(out_fp, ",\"at\":\"%s\"", jesc(at).c_str());
  if (why) fprintf(out_fp, ",\"why\":\"%s\"", why);
  if (to) fprintf(out_fp, ",\"to\":[\"%s\",%d]", jesc(func_id(to)).c_str(), j);
  emit_set(o);
}
static bool addr_like(tree a) { return POINTER_TYPE_P(TREE_TYPE(a)) || INTEGRAL_TYPE_P(TREE_TYPE(a)); }
// a parameter that can carry an address to the kernel: a pointer or a 64-bit integer (u64 addr); `erased` = its type says nothing about the object
static bool carries_address(tree pt) { return pt && (POINTER_TYPE_P(pt) || (INTEGRAL_TYPE_P(pt) && TYPE_PRECISION(pt) == 64)); }
static bool erased(tree pt) {
  if (!pt) return false;
  if (INTEGRAL_TYPE_P(pt)) return TYPE_PRECISION(pt) == 64;
  if (!POINTER_TYPE_P(pt)) return false;
  tree t = TYPE_MAIN_VARIANT(TREE_TYPE(pt));
  return TREE_CODE(t) == VOID_TYPE || (INTEGRAL_TYPE_P(t) && TYPE_PRECISION(t) == 8);
}
static tree param_type(tree fd, unsigned i) {
  tree a = TYPE_ARG_TYPES(TREE_TYPE(fd));
  for (; a && i; a = TREE_CHAIN(a), i--) ;
  return a ? TREE_VALUE(a) : NULL_TREE;
}

static void scan_function(function *fn) {
  std::string F = func_id(fn->decl);
  fprintf(out_fp, "{\"k\":\"def\",\"f\":\"%s\"}\n", jesc(F).c_str());
  basic_block bb;
  FOR_EACH_BB_FN(bb, fn) {
    for (gimple_stmt_iterator gsi = gsi_start_bb(bb); !gsi_end_p(gsi); gsi_next(&gsi)) {
      gimple *s = gsi_stmt(gsi);
      std::string at = loc_str(gimple_location(s));
      if (gimple_code(s) == GIMPLE_RETURN) {
        tree v = gimple_return_retval(as_a<greturn *>(s));
        if (v && addr_like(v)) fact("ret", F, "", nullptr, origin_of(v));
      } else if (is_gimple_call(s)) {
        tree fd = gimple_call_fndecl(s);
        unsigned n = gimple_call_num_args(s);
        if (fd && gimple_call_builtin_p(s, BUILT_IN_NORMAL) && n >= 2 &&
            (DECL_FUNCTION_CODE(fd) == BUILT_IN_MEMCPY || DECL_FUNCTION_CODE(fd) == BUILT_IN_MEMMOVE)) {
          if (origin_of(gimple_call_arg(s, 0)).tls) fact("sink", F, at, "tls-memcpy", origin_of(gimple_call_arg(s, 1)));
          continue;
        }
        if (fd && gimple_call_builtin_p(s, BUILT_IN_NORMAL) && n >= 1 && DECL_FUNCTION_CODE(fd) == BUILT_IN_MEMSET) continue;   // writes bytes, no type
        bool sink = fd && in_files(DECL_SOURCE_FILE(fd), sink_files);       // every pointer / 64-bit argument reaches the kernel
        bool sinkbuf = fd && !sink && in_files(DECL_SOURCE_FILE(fd), sinkbuf_files);   // only the arguments whose parameter type erased the object
        bool ind = !fd;
        for (unsigned i = 0; i < n; i++) {
          tree a = gimple_call_arg(s, i);
          if (!addr_like(a)) continue;
          tree pt = fd ? param_type(fd, i) : NULL_TREE;
          if (fd && !carries_address(pt)) continue;
          Org o = origin_of(a);
          if (sink) fact("sink", F, at, "svc", o);
          else if (sinkbuf) { if (erased(pt)) fact("sink", F, at, "ipc", o); }
          else if (fd) fact("edge", F, at, nullptr, o, fd, (int)i);
          else if (!o.empty() && (!o.t.empty() || !o.p.empty())) ind = true;
        }
        if (ind && !fd) { fprintf(out_fp, "{\"k\":\"ind\",\"f\":\"%s\",\"at\":\"%s\"}\n", jesc(F).c_str(), jesc(at).c_str()); }
      } else if (is_gimple_assign(s) && gimple_assign_single_p(s)) {
        tree lhs = gimple_assign_lhs(s), rhs = gimple_assign_rhs1(s);
        if (TREE_CODE(lhs) == SSA_NAME || !handled_component_p(lhs) && TREE_CODE(lhs) != MEM_REF && TREE_CODE(lhs) != TARGET_MEM_REF) continue;
        tree bp = base_ptr(lhs);
        if (bp && origin_of(bp).tls) {                       // a store into the TLS IPC area: the stored object's type is a wire type
          Org o; add_type(o, TREE_TYPE(lhs));
          if (addr_like(rhs)) o.add(origin_of(rhs));
          fact("sink", F, at, "tls-store", o);
        }
        for (tree r = lhs; handled_component_p(r); r = TREE_OPERAND(r, 0))
          if (TREE_CODE(r) == COMPONENT_REF) {                // a member of a struct declared in a sink header
            tree rec = DECL_FIELD_CONTEXT(TREE_OPERAND(r, 1));
            tree td = TYPE_STUB_DECL(rec);
            if (td && in_files(DECL_SOURCE_FILE(td), sink_files)) {
              if (addr_like(rhs)) fact("sink", F, at, "sfmember", origin_of(rhs));
              break;
            }
          }
      }
    }
  }
}

namespace {
const pass_data wire_data = { GIMPLE_PASS, "fnvnx_wire", OPTGROUP_NONE, TV_NONE, PROP_ssa, 0, 0, 0, 0 };
struct wire_pass : gimple_opt_pass {
  wire_pass(gcc::context *ctx) : gimple_opt_pass(wire_data, ctx) {}
  unsigned execute(function *fn) override {
    if (!out_fp) {
      std::string u = main_input_filename ? main_input_filename : "unit";
      for (char &c : u) if (c == '/' || c == '.') c = '_';
      std::string p = out_dir + "/" + u + ".wire";
      out_fp = fopen(p.c_str(), "w");
      if (!out_fp) { error("%<fnvnx_wire%>: cannot write %s", p.c_str()); return 0; }
    }
    scan_function(fn);
    return 0;
  }
};
}

static std::string kind_of(tree t) {
  while (TREE_CODE(t) == ARRAY_TYPE) {
    char b[48]; snprintf(b, sizeof b, "[%d]", (int)(TYPE_DOMAIN(t) && TYPE_MAX_VALUE(TYPE_DOMAIN(t)) && fits(TYPE_MAX_VALUE(TYPE_DOMAIN(t))) ? tree_to_uhwi(TYPE_MAX_VALUE(TYPE_DOMAIN(t))) + 1 : -1));
    return std::string(b) + kind_of(TREE_TYPE(t));
  }
  if (TREE_CODE(t) == RECORD_TYPE || TREE_CODE(t) == UNION_TYPE) return type_id(TYPE_MAIN_VARIANT(t));
  if (POINTER_TYPE_P(t)) return "ptr";
  if (INTEGRAL_TYPE_P(t) || TREE_CODE(t) == ENUMERAL_TYPE) return TYPE_UNSIGNED(t) ? "u" : "i";
  if (SCALAR_FLOAT_TYPE_P(t)) return "f";
  return "?";
}
static unsigned long long bits(tree x) { return x && TREE_CODE(x) == INTEGER_CST && fits(x) ? tree_to_uhwi(x) : 0; }
static unsigned long long field_bitpos(tree f) {   // bit_position() without its asserts: a field of an incomplete type has no offset
  tree o = DECL_FIELD_OFFSET(f), b = DECL_FIELD_BIT_OFFSET(f);
  return (TREE_CODE(o) == INTEGER_CST ? bits(o) * 8 : 0) + bits(b);
}
static void record_layout(tree t) {   // computed when the type is first named: no tree is kept past the pass (GC)
  t = TYPE_MAIN_VARIANT(t);
  std::string id = type_id(t);
  if (layout_lines.count(id)) return;
  layout_lines[id] = "";                // recursion guard
  std::string l = "{\"k\":\"layout\",\"t\":\"" + jesc(id) + "\",";
  char b[96]; snprintf(b, sizeof b, "\"size\":%llu,\"align\":%u,\"m\":[", bits(TYPE_SIZE(t)), (unsigned)TYPE_ALIGN(t));
  l += b;
  if (TREE_CODE(t) == RECORD_TYPE || TREE_CODE(t) == UNION_TYPE) {
    bool first = true;
    for (tree f = TYPE_FIELDS(t); f; f = DECL_CHAIN(f)) {
      if (TREE_CODE(f) != FIELD_DECL) continue;
      tree ft = TREE_TYPE(f);
      char m[96]; snprintf(m, sizeof m, ",%llu,%llu,\"", field_bitpos(f), bits(DECL_SIZE(f)));
      l += std::string(first ? "" : ",") + "[\"" + (DECL_NAME(f) ? IDENTIFIER_POINTER(DECL_NAME(f)) : "") + "\"" + m + jesc(kind_of(ft)) + "\"]";
      first = false;
      tree e = ft; while (TREE_CODE(e) == ARRAY_TYPE) e = TREE_TYPE(e);
      if (TREE_CODE(e) == RECORD_TYPE || TREE_CODE(e) == UNION_TYPE) record_layout(e);
    }
  }
  layout_lines[id] = l + "]}\n";
}
static void finish(void *, void *) {
  if (!out_fp) return;
  for (auto &kv : layout_lines) fputs(kv.second.c_str(), out_fp);
  layout_lines.clear();
  fclose(out_fp); out_fp = nullptr;
}

int plugin_init(struct plugin_name_args *info, struct plugin_gcc_version *version) {
  if (!plugin_default_version_check(version, &gcc_version)) {
    error("%<fnvnx_wire%>: built for GCC %s, loaded by %s", gcc_version.basever, version->basever);
    return 1;
  }
  for (int i = 0; i < info->argc; i++) {
    const char *k = info->argv[i].key, *v = info->argv[i].value ? info->argv[i].value : "";
    if (!strcmp(k, "out")) out_dir = v;
    else if (!strcmp(k, "sink")) sink_files.push_back(v);
    else if (!strcmp(k, "sinkbuf")) sinkbuf_files.push_back(v);
    else if (!strcmp(k, "tls")) tls_files.push_back(v);
    else { error("%<fnvnx_wire%>: unknown argument %s", k); return 1; }
  }
  if (out_dir.empty()) { error("%<fnvnx_wire%>: out=<dir> is required"); return 1; }
  struct register_pass_info pass;
  pass.pass = new wire_pass(g);
  pass.reference_pass_name = "ssa";
  pass.ref_pass_instance_number = 1;
  pass.pos_op = PASS_POS_INSERT_AFTER;
  register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pass);
  register_callback(info->base_name, PLUGIN_FINISH_UNIT, finish, NULL);
  return 0;
}
