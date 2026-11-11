/* host_ids.inc - the ids the host hands to the guest (thread ids, directory ids) are table handles, never addresses: an id
 * is (generation << 16) | (index + 1), so 0 is never an id, a freed id never names a later object (the generation grows on
 * every reuse of its slot) and a made-up number is refused instead of being dereferenced. Standalone (<switch.h> Mutex only)
 * so tests/s4/backends runs it under qemu.
 * ASSUMPTION (the guest's side of the contract): an id is not used by one thread while another thread frees it. */
typedef struct HostThread {
    Thread t;
    u64 entry, arg;
    u32 state;                       /* HT_* bits, atomic */
    struct HostThread *next_zombie;  /* g_zombies */
} HostThread;
#define HT_STARTED 1u
#define HT_FINISHED 2u               /* the guest entry returned: the thread is in libnx's threadExit */
#define HT_DETACHED 4u               /* thread_free came before the end: the thread hands itself to the reaper */

#define FNVNX_ID_MAX 1024
enum { HOST_ID_THREAD = 1, HOST_ID_DIR = 2 };
typedef struct { void *p; u32 gen; int kind; } HostId;
static HostId g_ids[FNVNX_ID_MAX];
static Mutex g_ids_lock;

/* the new id of p, 0 when the table is full */
static u64 host_id_new(int kind, void *p) {
    u64 id = 0;
    mutexLock(&g_ids_lock);
    for (int i = 0; i < FNVNX_ID_MAX && !id; i++) {
        if (g_ids[i].kind) continue;
        g_ids[i].p = p;
        g_ids[i].kind = kind;
        g_ids[i].gen = (g_ids[i].gen + 1) & 0xFFFF;
        if (!g_ids[i].gen) g_ids[i].gen = 1;
        id = ((u64)g_ids[i].gen << 16) | (u64)(i + 1);
    }
    mutexUnlock(&g_ids_lock);
    return id;
}
/* the object of a live id of that kind, NULL otherwise (never created, freed, other kind, any made-up number) */
static void *host_id_get(int kind, u64 id) {
    u64 i = (id & 0xFFFF);
    void *p = NULL;
    if (!i || i > FNVNX_ID_MAX || (id >> 16) > 0xFFFF) return NULL;
    mutexLock(&g_ids_lock);
    HostId *e = &g_ids[i - 1];
    if (e->kind == kind && e->gen == (id >> 16)) p = e->p;
    mutexUnlock(&g_ids_lock);
    return p;
}
/* like get, and the id dies */
static void *host_id_del(int kind, u64 id) {
    u64 i = (id & 0xFFFF);
    void *p = NULL;
    if (!i || i > FNVNX_ID_MAX || (id >> 16) > 0xFFFF) return NULL;
    mutexLock(&g_ids_lock);
    HostId *e = &g_ids[i - 1];
    if (e->kind == kind && e->gen == (id >> 16)) {
        p = e->p;
        e->kind = 0;
        e->p = NULL;
    }
    mutexUnlock(&g_ids_lock);
    return p;
}
