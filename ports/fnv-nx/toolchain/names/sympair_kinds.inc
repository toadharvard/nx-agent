/* sympair_kinds.inc - the closed set of `kind` values of a `.fnvnx.sympair` row (`<symbol>\t<MSVC name>\t<kind>\t<def|ref>`), THE one definition.
 * The names plugin includes it and refuses to write a row whose kind is outside it (a kind is one of these names, or `nodecor:<reason>`, the typed fact
 * "the decoration source has no name for the symbol"). contract/l2sections.py reads the names from the FNVNX_SYMPAIR_KINDS line below.
 * One entry per kind: X(name, "meaning"). */
#define FNVNX_SYMPAIR_KINDS(X) \
  X(func, "the MSVC name is the name cl gives the same function declaration") \
  X(data, "the MSVC name is the name cl gives the same variable declaration") \
  X(ctor.c1, "GCC's complete constructor: cl's one ??0 (most-derived flag 1)") \
  X(ctor.c2, "GCC's base constructor: cl's one ??0") \
  X(dtor.d2, "the base destructor: ??1") \
  X(dtor.d1, "the complete destructor: ??1, or ??_D when the class has virtual bases") \
  X(dtor.d0, "the deleting destructor: ??_G") \
  X(dtor.sdd, "the SDD2 slot function <entry>.fnvnx_sdd: ??_E") \
  X(dtor.sdd.thunk, "the same for a secondary table: cl's adjustor thunk to ??_E") \
  X(internal, "cl gives it internal linkage: not in the contract, the MSVC name is empty") \
  X(thunk, "a GCC this/result adjusting thunk: the name is its target's") \
  X(vtable, "a GCC table group: its address points are named in .fnvnx.vtpair") \
  X(gcc, "an Itanium ABI object with no counterpart in the exe (VTT, construction table, typeinfo, guard variable): the MSVC name is empty") \
  X(noemit, "a function this unit DEFINES whose body GCC wrote nowhere in the object (inlined at every use, or never used out of line): it has a cl name and NO symbol in the object, def=ref; every cl name without a symbol is accounted for by this kind or a nodecor row")
