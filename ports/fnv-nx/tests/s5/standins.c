/* standins.c - boot stand-ins for functions whose native body is wrong (the decomp's body does not do what the
 * exe's does). Each one is switched on by a line '<symbol> standin' in tests/s5/known_faults.txt: emu_guest.py
 * compiles this file (aarch64-linux-gnu-gcc -mabi=ilp32 -O2 -c), maps it after the guest and points the guest's
 * function at the definition here. Only the listed symbols are redirected. The comment of each says what the exe
 * does, from the exe's own code, and who fixes it; remove a stand-in and its table line when the fix lands.
 * Our own code; it names exe addresses only. */
#include <stdint.h>

extern void *fnv_mm_allocate(void *mm, uint32_t size) __asm__("__fnv_00aa3e40");   /* MemoryManager::Allocate */
extern void *fnv_messagehandler_init(void *self, _Bool is_static)
    __asm__("_ZN14MessageHandler12FUN_00c3bcf0Eb");                                  /* 0x00c3bcf0 */

#define G_MEMORY_MANAGER ((void *)0x011f6238u)
#define MESSAGEHANDLER_PHANDLER (*(void *volatile *)0x01202d74u)

/* 0x00c3c460 (WinMain's first call, via 0x00c3bd30): the exe allocates 8 bytes from the memory manager, runs
 * MessageHandler's constructor 0x00c3bcf0(this, false) on it (vptr 0x010c14a8, bStatic, pHandler = this) and stores
 * the result in MessageHandler::pHandler (0x01202d74). The decomp's body is `new MH(false)` with an empty MH::MH, so
 * the handler has no vptr; 0x00c3bd30 copies it to the heap logger 0x011f6388, and the first AbstractHeap commit
 * calls slot 5 of the zeroed object (S1: the body should call 0x00c3bcf0). */
void fnv_standin_00c3c460(void) __asm__("_Z12FUN_00c3c460v");
void fnv_standin_00c3c460(void) {
    void *p = fnv_mm_allocate(G_MEMORY_MANAGER, 8);
    MESSAGEHANDLER_PHANDLER = p ? fnv_messagehandler_init(p, 0) : 0;
}

extern void fnv_mm_deallocate(void *mm, void *p) __asm__("__fnv_00aa4060");          /* MemoryManager::Deallocate */
extern void fnv_memorypool_init(void *self, uint32_t block, uint32_t pool, const char *desc)
    __asm__("__fnv_00aa66a0");                                                           /* MemoryPool constructor */
extern void fnv_memorypool_fini(void *self) __asm__("__fnv_00aa6800");               /* MemoryPool destructor */

/* 0x00aa3cc0 MemoryManager::CreateMemoryPool (block size, pool size, description), from MemoryManager's
 * constructor 0x00aa3880 (boot_banner): the exe allocates the 0x80-byte pool from the memory manager, constructs it
 * (0x00aa66a0) and, when it got no memory ([pool+4] == 0: every VirtualAlloc at the fixed hints k << 24 failed, as
 * they do here: os_vm_reserve takes no hint), destroys it (0x00aa6800) and gives the block back with
 * MemoryManager::Deallocate (0x00aa4060); m_bPoolAllocationEnabled (+0x129) is 0 meanwhile. The decomp's
 * `delete pnewPool` frees through NiFree (0x00aa1460), which needs Gamebryo's allocator (0x011f6080): it is made
 * later in the same constructor (InitializeGlobal -> InitHolder), so the first static initializer faulted and was
 * skipped (S1: the pool's delete is the memory manager's). The exe's memory context 3 (TLS +0x2b4) around it is
 * not set here; it only labels the allocation for the heap statistics. */
void fnv_standin_00aa3cc0(void *self, uint32_t block, uint32_t pool, const char *desc)
    __asm__("_ZN18FUN_00aa3cc0_shell16CreateMemoryPoolEjjPc");
void fnv_standin_00aa3cc0(void *self, uint32_t block, uint32_t pool, const char *desc) {
    unsigned char *mm = self;
    if (block < 8)
        return;
    mm[0x129] = 0;
    void *p = fnv_mm_allocate(G_MEMORY_MANAGER, 0x80);
    if (p) {
        fnv_memorypool_init(p, block, pool, desc);
        if (!((uint32_t *)p)[1]) {
            fnv_memorypool_fini(p);
            fnv_mm_deallocate(G_MEMORY_MANAGER, p);
        }
    }
    mm[0x129] = 1;
}

/* 0x00406d50 (dst, size, src): the exe's body is strcat_s (0x00ec6bd2) of src onto dst[size]; 0x00406d30 before it is
 * strcpy_s (0x00ec65a6). WinMain builds each INI path as strcpy_s(buf, 0x104, <My Games dir>) then
 * strcat_s(buf, 0x104, "FalloutPrefs.ini"). The decomp declares this one `FUN_00406d50(...)` and defines it nowhere,
 * so every call was a trap returning 0: the FalloutPrefs.ini load got only the directory, sD3DDevice stayed "" and
 * the device check sent the game to the launcher (ShellExecute, WinMain returns 0). Variadic integer arguments come
 * in x0..x2 as fixed ones do (AAPCS64), so a three-argument definition serves every caller. S1-refs: define it. */
void fnv_standin_00406d50(char *dst, uint32_t size, const char *src) __asm__("_Z12FUN_00406d50z");
void fnv_standin_00406d50(char *dst, uint32_t size, const char *src) {
    uint32_t n = 0, i = 0;
    if (!dst || !size)
        return;
    while (n < size && dst[n])
        n++;
    if (n == size || !src) {   /* not terminated, or no source: strcat_s fails and empties dst */
        dst[0] = 0;
        return;
    }
    for (; n + i + 1 < size && src[i]; i++)
        dst[n + i] = src[i];
    if (src[i]) {              /* does not fit: strcat_s empties dst */
        dst[0] = 0;
        return;
    }
    dst[n + i] = 0;
}

/* 0x00aa78a0 MemoryHeap::MemoryHeap(reserve, commit, name, flag), now the D20 reconstruction (fnv-nx-build
 * recon/bscore_memoryheap, all bytes equal on x86). Natively its vptr is _ZTVN12fnv_00aa78a010MemoryHeapE, which
 * the link builds by D22's minus-one rule: GCC's D1/D0 at slots 0/1, exe slot k at k+1 (GetName at 2, Allocate at 3).
 * The memory manager dispatches on its heaps through call-only placeholder declarations in the exe's slot order
 * (HeapStandIn::Allocate slot 2, FNV_Heap release slot 3, FUN_00aa45a0_vt::v4 ContainsBlock slot 4), so every
 * MemoryManager::Allocate returned the heap's name (0x010824fc) and every free ran on a wrong block (S10 fault,
 * J-0155). The exe's body, with the exe's vptr (its vftable 0x010a28fc, whose slots S3's code-pointer pass made
 * natives in the exe's order): AbstractHeap base 0x00aa8ca0(this, reserve, commit, name, 0x10000), vptr, flag at
 * +0x20f0, initialized (+0x20f1) = 0, InitializeSlots 0x00aa8dd0. Fix: the D22 counterpart for call-only
 * declarations (S1-refs; tests/s5/callonly_vt.py lists them). */
extern void *fnv_abstractheap_base(void *self, uint32_t reserve, uint32_t commit, const char *name, uint32_t page)
    __asm__("__fnv_00aa8ca0");
extern void fnv_heap_initialize_slots(void *self) __asm__("__fnv_00aa8dd0");
void *fnv_standin_00aa78a0(void *self, uint32_t reserve, uint32_t commit, const char *name, _Bool flag)
    __asm__("_ZN12fnv_00aa78a010MemoryHeapC1EjjPKcb");
/* D26 (b, c): the heap callers are declared with a virtual destructor now (InitializeSlots' allocator call moved from
 * slot 12 to 13), so the object carries the link's minus-one table, not the exe's vftable: with 0x010a28fc slot 13 was
 * exe slot 13, the allocation returned 0 and InitializeSlots exited the guest before WinMain (link 3f7a6c35e923). */
extern const void *fnv_memoryheap_vtable[] __asm__("_ZTVN12fnv_00aa78a010MemoryHeapE")
    __attribute__((visibility("hidden")));   /* a direct adrp/add: --inject has no GOT relocations */
void *fnv_standin_00aa78a0(void *self, uint32_t reserve, uint32_t commit, const char *name, _Bool flag) {
    unsigned char *p = self;
    fnv_abstractheap_base(self, reserve, commit, name, 0x10000);
    *(const void *volatile *)p = &fnv_memoryheap_vtable[2];   /* the address point: after offset-to-top and RTTI */
    p[0x20f0] = flag;
    p[0x20f1] = 0;
    fnv_heap_initialize_slots(self);
    return self;
}

/* 0x00aa7400 FUN_00aa7400::FUN_00aa7400(size, name): the memory manager's second heap (classes 0, 3 and 9). The class
 * is defined (its constructor is native) but declares only `virtual void v00()` for the exe's slot 0, so the link
 * gives _ZTV12FUN_00aa7400 the exe's order (slot k at k, filled from the exe vftable 0x010a2884: slot 2 is 0x00aa7530
 * Allocate, slot 3 the empty 0x00aa75e0), not D26's minus-one order. The memory manager's heap calls are declared
 * through HeapStandIn, which has a virtual destructor now (D26 (b)), so Allocate is native slot 3: it ran the empty
 * exe slot 3, returned `this` and handed the heap object itself out as the next allocation; BSNiAllocator was built on
 * it, FUN_00aa21d0 stored the address in word 0, and the next Allocate fetched [obj+12] (the buffer pointer
 * 0x4cb4e020) as code: the boot_banner fault and the 4,550 skips behind it (S1-rules: the class needs a
 * `virtual ~FUN_00aa7400()` for slot 0; S2-bodies: the link must lay out a defined class by D22). The same body as
 * the exe's, with a minus-one table (D1 stub, D0 = the exe's deleting destructor at 0, exe slot k at k+1). */
extern void *fnv_alloc_buffer(void *mm, uint32_t size, uint32_t pad)
    __asm__("_ZN18FUN_00aa4290_Class14AllocateBufferEjj");
static void *fnv_heap2_vtable[2 + 14];
/* D26 minus-one table from an exe vftable: D1 stub and D0 (the exe's slot 0) at 0/1, exe slot k at k+1. */
static void fnv_m1_d1(void *self) { (void)self; }
static void **fnv_m1_table(void **tab, uint32_t exe_vftable, int n) {
    void **exe = (void **)exe_vftable;
    tab[0] = (void *)fnv_m1_d1;
    tab[1] = exe[0];
    for (int k = 1; k < n; k++)
        tab[k + 1] = exe[k];
    return tab;
}
void *fnv_standin_00aa7400(void *self, uint32_t size, const char *name) __asm__("_ZN12FUN_00aa7400C1Ejj");
void *fnv_standin_00aa7400(void *self, uint32_t size, const char *name) {
    uint32_t *p = self;
    p[0] = (uint32_t)(uintptr_t)fnv_m1_table(fnv_heap2_vtable, 0x010a2884u, 14);
    p[1] = size;
    p[2] = (uint32_t)(uintptr_t)name;
    p[5] = 0;
    p[8] = 0;
    p[9] = 0;
    p[3] = p[4] = (uint32_t)(uintptr_t)fnv_alloc_buffer(G_MEMORY_MANAGER, size, 1);
    return self;
}

/* 0x00aa2230 BSNiAllocator's constructor (the exe stores only the vftable 0x010a2528): the decomp defines the class
 * with `virtual void m_virtual0()` for the destructor slot, so _ZTV26FUN_00aa2230_BSNiAllocator is in the exe's order
 * and Gamebryo's allocator calls (NiAllocator, declared with a virtual destructor, D26 (b)) land one slot early:
 * NiMalloc 0x00aa1070 (vfn_04, native slot 5) ran exe slot 5 and called MemoryManager::Deallocate on its own stack
 * block, _free_r wrote through a bad reent, 554 Setting initializers skipped. The same class as 0x00aa7400 above:
 * a defined class whose slot-0 placeholder is not a destructor (S1-rules). A minus-one table of the exe's 9 slots. */
/* The exe's slots 1, 3, 4, 6, 7 and 8 hold free functions (stdcall/cdecl: AllocateZeroedMemory 0x00aa2240,
 * ResizeHeapBlock 0x00aa22f0, AllocateAlignedBuffer 0x00aa2370, 0x00aa2420, 0x00a29680), which natively take no
 * `this`: emu_guest.py's thisless pass thunks them in the tables it knows; a table built here drops `this` itself
 * (x1..x7 -> x0..x6, then a branch). */
#define FNV_DROP_THIS(name, target) \
    __asm__(".text\n.balign 16\n.type " #name ",%function\n" #name ":\n" \
            "mov x0, x1\nmov x1, x2\nmov x2, x3\nmov x3, x4\nmov x4, x5\nmov x5, x6\nmov x6, x7\n" \
            "b " #target "\n"); \
    extern void name(void) __attribute__((visibility("hidden")))
FNV_DROP_THIS(fnv_drop_00aa2240, __fnv_00aa2240);
FNV_DROP_THIS(fnv_drop_00aa22f0, __fnv_00aa22f0);
FNV_DROP_THIS(fnv_drop_00aa2370, __fnv_00aa2370);
FNV_DROP_THIS(fnv_drop_00aa2420, __fnv_00aa2420);
FNV_DROP_THIS(fnv_drop_00a29680, __fnv_00a29680);
static void *fnv_niallocator_vtable[2 + 9];
void *fnv_standin_00aa2230(void *self) __asm__("_ZN26FUN_00aa2230_BSNiAllocatorC1Ev");
void *fnv_standin_00aa2230(void *self) {
    void **t = fnv_m1_table(fnv_niallocator_vtable, 0x010a2528u, 9);
    t[2] = (void *)fnv_drop_00aa2240;   /* exe slot 1 */
    t[4] = (void *)fnv_drop_00aa22f0;   /* 3 */
    t[5] = (void *)fnv_drop_00aa2370;   /* 4 */
    t[7] = (void *)fnv_drop_00aa2420;   /* 6 */
    t[8] = (void *)fnv_drop_00a29680;   /* 7 */
    t[9] = (void *)fnv_drop_00a29680;   /* 8 */
    *(uint32_t *)self = (uint32_t)(uintptr_t)t;
    return self;
}

/* The Setting registries 0x00632ec0 (image vftable 0x0104e6ac) and 0x0045d220 (0x01018508), singletons that every
 * Setting initializer registers itself with: the callers (hundreds of constructors and inlined initializers) declare
 * the registry as a call-only class with a NAMED slot-0 virtual (ObjectRegistry { Reset(); Register(); }), which
 * D26 (b) does not rewrite (it takes placeholder names: v0, Unk_00, Reserved0, ...). `Register` stays native slot 1
 * there, while the registry carries the minus-one table of its image vftable (D26 (c)): slot 1 is D0, so every
 * registration deleted the registry (FUN_004de550 with flags 1), its block was reused and the next one hit _purecall
 * (fatal after initializer #4000). Until the callers are declared right (S1-rules: widen D26 (b) to every call-only
 * class whose slot 0 is not defined or called by name; S2c), the registries get a copy of their table with slot 1 =
 * slot 2 (Register), which serves both the declared callers (1) and the D26 ones (2); a singleton is never deleted. */
extern void fnv_collection_init(void *self) __asm__("_ZN18FUN_0044f600_class14InitializeVtblEv");
extern const void *fnv_vt_632ec0[] __asm__("__fnv_0104e6ac") __attribute__((visibility("hidden")));
extern const void *fnv_vt_45d220[] __asm__("__fnv_01018508") __attribute__((visibility("hidden")));
static void *fnv_registry_vt_a[16], *fnv_registry_vt_b[16];
static void *fnv_registry_vtable(void **dst, const void *const *src) {
    for (int k = 0; k < 14; k++)
        dst[k] = (void *)src[k];
    dst[1] = dst[2];
    return dst;
}
void *fnv_standin_00632ec0(void *self) __asm__("_ZN12fnv_00632ec012FUN_00632ec0C1Ev");
void *fnv_standin_00632ec0(void *self) {
    fnv_collection_init(self);
    *(void *volatile *)self = fnv_registry_vtable(fnv_registry_vt_a, fnv_vt_632ec0);
    return self;
}
void *fnv_standin_0045d220(void *self) __asm__("_ZN10S_0045d22012FUN_0045d220Ev");
void *fnv_standin_0045d220(void *self) {
    fnv_collection_init(self);
    *(void *volatile *)self = fnv_registry_vtable(fnv_registry_vt_b, fnv_vt_45d220);
    return self;
}

/* 0x00869f00 (this, a, b), the constructor of the static objects 0x011de924 and 0x011de898 (initializers 0x00fa1fc0 and
 * 0x00fa1fe0): the exe stores the image vftable 0x0106ba6c and calls 0x006b3eb0, which allocates the object's array
 * through `self->Allocate()`, declared in extradatalist.cpp as a call-only class with a NAMED slot 0 (v0, then
 * Allocate: still native slot 1 after D26 (b), D0 of the minus-one table). The call deleted a static object
 * (MemoryManager::Deallocate -> free of 0x011de924, _free_r read fault): 2 skipped initializers. The same body with
 * a table whose slot 1 = slot 2 (as the registries above; S1-rules). */
extern void fnv_array_init(void *self, uint32_t a, uint32_t b) __asm__("_Z12FUN_006b3eb0P2AX2UWS1_");
extern const void *fnv_vt_869f00[] __asm__("__fnv_0106ba6c") __attribute__((visibility("hidden")));
static void *fnv_array_vt[16];
void *fnv_standin_00869f00(void *self, uint32_t a, uint32_t b) __asm__("_ZN18FUN_00869f00_shell12FUN_00869f00Ejj");
void *fnv_standin_00869f00(void *self, uint32_t a, uint32_t b) {
    *(void *volatile *)self = fnv_registry_vtable(fnv_array_vt, fnv_vt_869f00);
    fnv_array_init(self, a, b);
    return self;
}


/* 0x00405430 NiTMap_S::RemoveKey(key) (the AllForms-style map RemoveKey that TESForm's 0x00483c70 reaches from the
 * form constructor of the static form 0x011c9750, initializer 0x00f4ad10): NiTMap_S is a call-only declaration with
 * a placeholder at slot 0 (actor.cpp), so its KeyToHashIndex call reads native slot 1. The receiver is the native
 * NiTPointerMap<unsigned, TESForm*> (exe vftable 0x0101ca78, native _ZTV13NiTPointerMapIjP7TESFormE) with GCC's
 * D1/D0 at 0/1, so the call ran the deleting destructor and its result indexed the hash table out of range. The
 * same body, exe slot k at native slot k+1 (S1-rules: `virtual ~NiTMap_S()` for `v0`, D26 (b)). */
typedef struct MapItemS { struct MapItemS *next; uint32_t key; void *val; } MapItemS;
typedef struct { void **vptr; uint32_t size; MapItemS **table; uint32_t count; } NiTMapS;
#define MSLOT_M1(obj, k) (((void **)((NiTMapS *)(obj))->vptr)[(k) + 1])
_Bool fnv_standin_00405430(NiTMapS *m, uint32_t key) __asm__("_ZN8NiTMap_S9RemoveKeyEj");
_Bool fnv_standin_00405430(NiTMapS *m, uint32_t key) {
    uint32_t i = ((uint32_t (*)(void *, uint32_t))MSLOT_M1(m, 1))(m, key);
    MapItemS *cur = m->table[i], *prev = 0;
    while (cur && !((_Bool (*)(void *, uint32_t, uint32_t))MSLOT_M1(m, 2))(m, key, cur->key)) {
        prev = cur;
        cur = cur->next;
    }
    if (!cur)
        return 0;
    if (prev)
        prev->next = cur->next;
    else
        m->table[i] = cur->next;
    ((void (*)(void *, MapItemS *))MSLOT_M1(m, 4))(m, cur);
    ((void (*)(void *, MapItemS *))MSLOT_M1(m, 6))(m, cur);
    m->count--;
    return 1;
}
