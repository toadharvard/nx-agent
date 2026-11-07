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
void *fnv_standin_00aa78a0(void *self, uint32_t reserve, uint32_t commit, const char *name, _Bool flag) {
    unsigned char *p = self;
    fnv_abstractheap_base(self, reserve, commit, name, 0x10000);
    *(volatile uint32_t *)p = 0x010a28fcu;
    p[0x20f0] = flag;
    p[0x20f1] = 0;
    fnv_heap_initialize_slots(self);
    return self;
}

/* 0x00b57ea0 HashTableBase::InsertOrUpdate(key, value) (the display-mode list, ModeItemInit 0x00e7b260): the
 * decomp declares HashTableBase in the exe's slot order with a placeholder at slot 0 (Reserved0; BucketIndex 1,
 * EqualKey 2, Initialize 3, AllocateNode 5), a call-only declaration (tests/s5/callonly_vt.py). Its receivers in
 * the boot are native maps with GCC's D1/D0 pair (_ZTV10BE7B040MapIjP12BE7B040ValueE, bound to 0x010eec7c), laid
 * out by D22's minus-one rule, so BucketIndex ran D0 (ResizeHeapBlock fault). The same body, dispatching exe slot
 * k at native slot k+1 (S1-refs: `virtual ~HashTableBase()` for Reserved0, D22). */
typedef struct HashNode { struct HashNode *next; uint32_t key, value; } HashNode;
typedef struct { void **vptr; uint32_t size; HashNode **buckets; uint32_t count; } HashTableBase;
#define VSLOT_M1(obj, k) (((void **)((HashTableBase *)(obj))->vptr)[(k) + 1])
void fnv_standin_00b57ea0(HashTableBase *t, uint32_t key, uint32_t value)
    __asm__("_ZN13HashTableBase14InsertOrUpdateEjj");
void fnv_standin_00b57ea0(HashTableBase *t, uint32_t key, uint32_t value) {
    uint32_t i = ((uint32_t (*)(void *, uint32_t))VSLOT_M1(t, 1))(t, key);
    for (HashNode *n = t->buckets[i]; n; n = n->next)
        if (((_Bool (*)(void *, uint32_t, uint32_t))VSLOT_M1(t, 2))(t, key, n->key)) {
            n->value = value;
            return;
        }
    HashNode *n = ((HashNode * (*)(void *)) VSLOT_M1(t, 5))(t);
    ((void (*)(void *, HashNode *, uint32_t, uint32_t))VSLOT_M1(t, 3))(t, n, key, value);
    n->next = t->buckets[i];
    t->buckets[i] = n;
    t->count++;
}
