/* a weakref to a symbol this unit does not define: an unanalyzed alias node (libgcc emutls.c / unwind-dw2.c use __gthrw_* of this kind). The pi plugin read its target with symtab_node::get_alias_target (), a null dereference. */
static int w1(int) __attribute__((weakref("not_defined_anywhere")));
static void w2(void) __attribute__((weakref("also_undefined")));
int f(int x) { if (w1) return w1(x); if (w2) w2(); return x; }
