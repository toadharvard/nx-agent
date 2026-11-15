// crossing.cpp - `.fnvnx.crossing`: the terms that hold for the parameter and return types of each external function
#include <stdarg.h>
struct VB { int v; };
struct DV : virtual VB { int d; };            // a class with a virtual base
struct DD : DV { int e; };                    // ... inherited through a base
struct HoldsDV { DV in; };                    // ... held by member
struct HoldsArr { DV in[2]; };                // ... in an array member
struct PM { int (PM::*f)(); int PM::*d; };    // pointers to member inside a class
struct Plain { int a; };
struct PlainDer : Plain { int b; };
struct VaH { va_list ap; };
int pm_data(int Plain::*p);
int pm_func(int (Plain::*p)(int));
int pm_in_class(PM m);
int pm_ptr(PM *m);                            // a pointer contains nothing: no term
int vb_ref(const DV &x);                      // a reference: no term
int vb_val(DV x);
int vb_derived(DD x);
int vb_member(HoldsDV x);
int vb_array(HoldsArr x);
DV vb_return();
int none_plain(Plain a, PlainDer b, int c, void *p);
int va(int n, va_list ap);
int va_held(VaH h);
int both(DV x, int Plain::*p);
int uses() {
  Plain pl = {1};
  PM pmv = {};
  VaH vh;
  DV dv;
  return pm_data(&Plain::a) + pm_func(nullptr) + pm_in_class(pmv) + pm_ptr(&pmv) + vb_ref(dv) + vb_val(dv) + vb_derived(DD()) + vb_member(HoldsDV())
       + vb_array(HoldsArr()) + vb_return().d + none_plain(pl, PlainDer(), 1, nullptr) + va(0, vh.ap) + va_held(vh) + both(dv, &Plain::a);
}
