// dec_cull.cpp - V1 (D14): stand-ins for the decomp's culling worker WorkerS694a0::FUN_00a694e0 (0x00a694e0,
// nicullingprocess.cpp); cull.inc is generated at run time from the decomp (never in git). The getter 0x0043d450
// and the classification 0x004b6100 are in dec_plane.cpp (their own unit's shells), bound by mangled name / glue.
#include <stdint.h>
struct NiPlane_004b6100 { int a, b, c, d; };
struct NiBound {};
struct B_004b6100 { int ClassifySphereAgainstPlane(NiPlane_004b6100 *); };
struct S694a0;
#define V(n) virtual void vslot##n();
struct NiAVObject {
    int unk04, unk08, unk0c, unk10, unk14, unk18, unk1c;
    B_004b6100 *bound;
    NiBound const &FUN_0043d450() const;
    V(0) V(1) V(2) V(3) V(4) V(5) V(6) V(7) V(8) V(9) V(10) V(11) V(12) V(13) V(14) V(15) V(16) V(17) V(18) V(19)
    V(20) V(21) V(22) V(23) V(24) V(25) V(26) V(27) V(28) V(29) V(30) V(31) V(32) V(33) V(34) V(35) V(36) V(37)
    V(38) V(39) V(40) V(41) V(42) V(43) V(44) V(45) V(46) V(47) V(48) V(49) V(50) V(51) V(52)
    virtual void callback(S694a0 *);
};
#undef V
struct WorkerS694a0 {
    int unk00, unk04, unk08, unk0c, unk10, unk14, unk18, unk1c, unk20, unk24, unk28;
    NiPlane_004b6100 planes[6];
    unsigned int flags;
    void FUN_00a694e0(NiAVObject *object);
};

#include "cull.inc"

#define V(n) void NiAVObject::vslot##n() {}
V(0) V(1) V(2) V(3) V(4) V(5) V(6) V(7) V(8) V(9) V(10) V(11) V(12) V(13) V(14) V(15) V(16) V(17) V(18) V(19)
V(20) V(21) V(22) V(23) V(24) V(25) V(26) V(27) V(28) V(29) V(30) V(31) V(32) V(33) V(34) V(35) V(36) V(37)
V(38) V(39) V(40) V(41) V(42) V(43) V(44) V(45) V(46) V(47) V(48) V(49) V(50) V(51) V(52)
#undef V
void NiAVObject::callback(S694a0 *) {}

extern "C" void *fn_0043d450(const void *self);
NiBound const &NiAVObject::FUN_0043d450() const { return *(NiBound *)fn_0043d450(this); }

extern "C" uint32_t g_cb_called, g_cb_mask, g_cb_worker_ok;
static WorkerS694a0 g_worker;
struct TestObj : NiAVObject {
    void callback(S694a0 *w) override;
};
// the callback (excluded from the dynamic counts by test_s15simd_decomp.py)
void TestObj::callback(S694a0 *w)
{
    g_cb_called++;
    g_cb_mask = ((WorkerS694a0 *)w)->flags;
    g_cb_worker_ok = (void *)w == (void *)&g_worker;
}
extern "C" void *memcpy(void *, const void *, unsigned long);
static uint32_t g_sphere[4];
extern "C" __attribute__((noinline)) void dec_cull_call(WorkerS694a0 *w, NiAVObject *o) { w->FUN_00a694e0(o); }
// in: [0] mask, [1] has bound, [2..5] sphere, [6..29] planes; out: called, mask in callback, mask after, worker ok
extern "C" void dec_cull(const uint32_t *in, uint32_t *out)
{
    TestObj obj;
    memcpy(g_sphere, in + 2, 16);
    obj.bound = in[1] ? (B_004b6100 *)g_sphere : 0;
    memcpy(g_worker.planes, in + 6, 96);
    g_worker.flags = in[0];
    g_cb_called = g_cb_mask = g_cb_worker_ok = 0;
    dec_cull_call(&g_worker, &obj);
    out[0] = g_cb_called;
    out[1] = g_cb_mask;
    out[2] = g_worker.flags;
    out[3] = g_cb_worker_ok;
}
