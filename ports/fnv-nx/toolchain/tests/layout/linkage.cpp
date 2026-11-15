// linkage.cpp - the `linkage` column of .fnvnx.layout (C++ [basic.link]): each record type below has the value its comment states.
struct Ext { int a; };                                    // external
namespace pub { struct P { int a; }; namespace deep { struct D { int a; }; } }   // external, also nested namespaces
namespace {
struct AnonNs { int a; };                                 // internal: a member of an unnamed namespace
struct AnonOuter { struct Nested { int b; }; Nested n; int c; };   // internal, and Nested through its enclosing class
namespace inner { struct AnonDeep { int a; }; }           // internal: a namespace nested in the unnamed one
template <class T> struct InAnonT { T v; };               // a template of the unnamed namespace: internal
typedef struct { int a; } AnonTypedef;                    // internal: the typedef name for linkage is a member of the unnamed namespace
}
typedef struct { int a; } ExtTypedef;                     // external: an unnamed class with a typedef name for linkage takes that name's linkage
template <class T> struct Tmpl { T v; };
Tmpl<Ext> t_ext;                                          // external: a specialization with an external argument
Tmpl<AnonNs> t_int_arg;                                   // internal: a specialization with an argument of internal linkage (determine_visibility)
Tmpl<pub::P> t_pub;                                       // external
InAnonT<int> t_anon_tmpl;                                 // internal (a member of the unnamed namespace)
AnonTypedef t_anon_typedef;
ExtTypedef t_ext_typedef;
struct { int u; } unnamed_var;                            // none: an unnamed class without a typedef name for linkage, at namespace scope
struct Holder { struct { int z; } member; int k; };       // Holder external; the unnamed member class is nested in an external class
union { int u1; float u2; } unnamed_union_var;            // none: an unnamed union at namespace scope
typedef union { int w1; float w2; } ExtUnion;             // external: a union named for linkage by a typedef
ExtUnion ext_union_var;
int f()
{
  struct Local { int a; };                                // none: a local class
  typedef struct { int b; } LocalTypedef;                 // none: a local class, named for linkage by a typedef in the function
  struct LocalHolder { struct LocalIn { int c; }; LocalIn in; };   // none, and LocalIn nested in a local class: none
  Local l = {1}; LocalTypedef lt = {2}; LocalHolder lh = {{3}};
  Tmpl<Local> tl = {l};                                   // the specialization has external linkage in C++11; its argument does not (the front end makes it internal)
  return l.a + lt.b + lh.in.c + tl.v.a;
}
auto lam = [](int x) { return x + 1; };                    // a lambda at namespace scope: mangling scope exists, external
int g() { auto lm = [](int x) { return x + 2; }; return lm(1); }   // a lambda in a function: no linkage
