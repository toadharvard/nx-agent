// locals and temporaries: order orders only memory another thread can reach (R9).  The ds3d `norm` pattern: an HFA aggregate
// returned by value, built in a result temporary, copied from a parameter on the other path.
struct V { float x, y, z; };
static V v3(float x, float y, float z) { V v = {x, y, z}; return v; }
static V scale(V a, float s) { return v3(a.x * s, a.y * s, a.z * s); }
static float sq(V a) { return a.x * a.x + a.y * a.y + a.z * a.z; }
V norm(V a, V d) { float l = __builtin_sqrtf(sq(a)); return l > 1e-12f ? scale(a, 1.0f / l) : d; }
extern void sink(V*);
V escaped(float x) { V v = {x, x, x}; sink(&v); return v; }
float plain_local(float x) { V v = {x, x, x}; v.y = x + 1; return v.x + v.y; }
