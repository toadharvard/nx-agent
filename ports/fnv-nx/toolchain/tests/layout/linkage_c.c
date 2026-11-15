/* the layout rows of a C unit: every record is linkage `c` (C11 6.2.2p6: a tag has no linkage; 6.2.7: units agree by type compatibility) */
struct S { int a; char b; };
union U { int i; float f; };
static struct Hidden { int h; } hidden;
struct S gs;
int f(void) { struct InFn { int x; } v; v.x = 1; return v.x + gs.a + hidden.h; }
