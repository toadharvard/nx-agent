/* reserve.c - portable planner of the address window reservation (stream S10). See fnvnx_reserve.h. */
#include "fnvnx_reserve.h"

#include <stdio.h>
#include <string.h>

uint32_t fnvnx_resv_scan(const FnvnxResvOps *ops, uint64_t start, uint64_t end, FnvnxResvRegion *out,
                         uint32_t cap) {
    uint32_t n = 0;
    uint64_t a = start;
    while (a < end) {
        FnvnxResvRegion m;
        if (ops->query(ops->ctx, a, &m) != 0 || m.size == 0 || m.addr + m.size <= a) {
            /* the query failed or made no progress: count the rest as occupied */
            if (n < cap) out[n] = (FnvnxResvRegion){ a, end - a, 0xFFu, 0 };
            return n + 1;
        }
        if (m.type != FNVNX_MEMTYPE_UNMAPPED) {
            if (n < cap) out[n] = m;
            n++;
        }
        a = m.addr + m.size;
    }
    return n;
}

static void scan_into(const FnvnxResvOps *ops, const FnvnxResvRange *r, FnvnxResvResult *res) {
    FnvnxResvRegion tmp[FNVNX_RESV_MAX_CONFLICTS];
    uint32_t k = fnvnx_resv_scan(ops, r->start, r->end, tmp, FNVNX_RESV_MAX_CONFLICTS);
    res->n_conflicts = k;
    memcpy(res->conflict, tmp, sizeof(FnvnxResvRegion) * (k < FNVNX_RESV_MAX_CONFLICTS ? k : FNVNX_RESV_MAX_CONFLICTS));
    if (k) res->state = FNVNX_RESV_COLLISION;
}

static int valid_range(const FnvnxResvRange *r) {
    return r->start < r->end && !(r->start & (FNVNX_RESV_PAGE - 1)) && !(r->end & (FNVNX_RESV_PAGE - 1));
}

uint32_t fnvnx_resv_unusable(const FnvnxResvRange *r, uint32_t n, const FnvnxResvResult *res,
                             uint32_t *collisions) {
    uint32_t bad = 0, col = 0;
    for (uint32_t i = 0; i < n; i++) {
        if (!(r[i].flags & FNVNX_RESV_REQUIRED)) continue;
        if (res[i].state == FNVNX_RESV_COLLISION) col++;
        if (res[i].state == FNVNX_RESV_COLLISION || res[i].state == FNVNX_RESV_FAILED) bad++;
    }
    if (collisions) *collisions = col;
    return bad;
}

uint32_t fnvnx_resv_claim(const FnvnxResvOps *ops, const FnvnxResvRange *r, uint32_t n, uint64_t heap_lo,
                          uint64_t heap_hi, uint64_t *heap_used, FnvnxResvResult *res) {
    uint64_t used = 0;
    heap_lo = (heap_lo + FNVNX_RESV_PAGE - 1) & ~(FNVNX_RESV_PAGE - 1);
    heap_hi &= ~(FNVNX_RESV_PAGE - 1);
    for (uint32_t i = 0; i < n; i++) {
        memset(&res[i], 0, sizeof res[i]);
        if (!valid_range(&r[i])) {
            res[i].state = FNVNX_RESV_FAILED;
            res[i].rc = FNVNX_RESV_RC_NO_HEAP;
            continue;
        }
        scan_into(ops, &r[i], &res[i]);
        if (res[i].state == FNVNX_RESV_COLLISION || !(r[i].flags & FNVNX_RESV_BACKED)) continue;
        uint64_t size = r[i].end - r[i].start;
        if (heap_hi < heap_lo || heap_hi - heap_lo < used + size) {
            res[i].state = FNVNX_RESV_FAILED;
            res[i].rc = FNVNX_RESV_RC_NO_HEAP;
            continue;
        }
        uint64_t src = heap_hi - used - size;
        uint32_t rc = ops->map(ops->ctx, r[i].start, src, size);
        if (rc) {
            res[i].state = FNVNX_RESV_FAILED;
            res[i].rc = rc;
            continue;
        }
        used += size;
        res[i].state = FNVNX_RESV_CLAIMED;
        res[i].backing = src;
    }
    if (heap_used) *heap_used = used;
    return fnvnx_resv_unusable(r, n, res, NULL);
}

uint32_t fnvnx_resv_late(const FnvnxResvOps *ops, const FnvnxResvRange *r, uint32_t n, FnvnxResvResult *res) {
    for (uint32_t i = 0; i < n; i++) {
        if (res[i].state != FNVNX_RESV_PENDING || !(r[i].flags & FNVNX_RESV_VIRTMEM)) continue;
        scan_into(ops, &r[i], &res[i]); /* services may have mapped something since the early phase */
        if (res[i].state == FNVNX_RESV_COLLISION) continue;
        void *h = NULL;
        uint32_t rc = ops->virtmem_reserve ? ops->virtmem_reserve(ops->ctx, r[i].start, r[i].end - r[i].start, &h)
                                           : FNVNX_RESV_RC_NO_VIRTMEM;
        if (rc) {
            res[i].state = FNVNX_RESV_FAILED;
            res[i].rc = rc;
        } else {
            res[i].state = FNVNX_RESV_RESERVED;
            res[i].virtmem = h;
        }
    }
    return fnvnx_resv_unusable(r, n, res, NULL);
}

uint32_t fnvnx_resv_release(const FnvnxResvOps *ops, const FnvnxResvRange *r, FnvnxResvResult *res,
                            uint64_t *backing) {
    if (res->state != FNVNX_RESV_CLAIMED) return FNVNX_RESV_RC_NO_HEAP;
    uint32_t rc = ops->unmap(ops->ctx, r->start, res->backing, r->end - r->start);
    if (rc) return rc;
    res->state = FNVNX_RESV_RELEASED;
    if (backing) *backing = res->backing;
    return 0;
}

int fnvnx_resv_contains(const FnvnxResvRange *r, uint32_t n, const FnvnxResvResult *res, uint64_t addr,
                        uint64_t size) {
    if (addr + size < addr) return 0;
    for (uint32_t i = 0; i < n; i++)
        if (res[i].state == FNVNX_RESV_CLAIMED && addr >= r[i].start && addr + size <= r[i].end) return 1;
    return 0;
}

const char *fnvnx_resv_type_name(uint32_t type) {
    switch (type) {
        case FNVNX_MEMTYPE_UNMAPPED: return "free";
        case FNVNX_MEMTYPE_CODE: return "NSO code (host image)";
        case FNVNX_MEMTYPE_CODE_DATA: return "NSO data (host image)";
        case FNVNX_MEMTYPE_SHARED: return "shared memory";
        case FNVNX_MEMTYPE_ALIAS_CODE: return "code memory";
        case FNVNX_MEMTYPE_ALIAS_DATA: return "code memory (data)";
        case FNVNX_MEMTYPE_STACK: return "stack / svcMapMemory";
        case FNVNX_MEMTYPE_THREADLOCAL: return "TLS page";
        case 0xFFu: return "unqueryable";
        default: return "other";
    }
}

const char *fnvnx_resv_state_name(uint32_t state) {
    static const char *const names[] = { "pending", "claimed", "reserved", "COLLISION", "FAILED", "released", "code" };
    return state < sizeof names / sizeof names[0] ? names[state] : "?";
}

size_t fnvnx_resv_format(const FnvnxResvRange *r, uint32_t n, const FnvnxResvResult *res, char *buf, size_t cap) {
    size_t len = 0;
    if (!cap) return 0;
    buf[0] = 0;
    for (uint32_t i = 0; i < n && len < cap; i++) {
        int k = snprintf(buf + len, cap - len, "  reserve %-12s %#010llx..%#010llx %s%s: %s", r[i].name ? r[i].name : "?",
                         (unsigned long long)r[i].start, (unsigned long long)r[i].end,
                         (r[i].flags & FNVNX_RESV_REQUIRED) ? "required" : "optional",
                         (r[i].flags & FNVNX_RESV_BACKED) ? ",backed" : (r[i].flags & FNVNX_RESV_VIRTMEM) ? ",virtmem" : "",
                         fnvnx_resv_state_name(res[i].state));
        if (k < 0) break;
        len += (size_t)k < cap - len ? (size_t)k : cap - len - 1;
        if (res[i].state == FNVNX_RESV_CLAIMED || res[i].state == FNVNX_RESV_CODE)
            k = snprintf(buf + len, cap - len, " (heap %#010llx)", (unsigned long long)res[i].backing);
        else if (res[i].state == FNVNX_RESV_FAILED)
            k = snprintf(buf + len, cap - len, " (rc %#x)", res[i].rc);
        else
            k = 0;
        if (k > 0) len += (size_t)k < cap - len ? (size_t)k : cap - len - 1;
        for (uint32_t c = 0; c < res[i].n_conflicts && c < FNVNX_RESV_MAX_CONFLICTS && len < cap; c++) {
            k = snprintf(buf + len, cap - len, " [%#010llx +%#llx %s]", (unsigned long long)res[i].conflict[c].addr,
                         (unsigned long long)res[i].conflict[c].size, fnvnx_resv_type_name(res[i].conflict[c].type));
            if (k > 0) len += (size_t)k < cap - len ? (size_t)k : cap - len - 1;
        }
        if (len + 1 < cap) {
            buf[len++] = '\n';
            buf[len] = 0;
        }
    }
    return len;
}

const char *fnvnx_resv_address_space_name(uint64_t stack_lo, uint64_t stack_hi, uint64_t alias_size) {
    (void)stack_lo;
    if (stack_hi && stack_hi <= FNVNX_RESV_SMALL_HI) return alias_size ? "32Bit" : "32BitNoReserved";
    if (stack_hi <= 0x80000000ull) return "64Bit36";
    return "64Bit39";
}

FnvnxRelaunchRecord fnvnx_relaunch_decode(const void *buf, size_t size) {
    FnvnxRelaunchRecord rec;
    memset(&rec, 0, sizeof rec);
    if (!buf || size < sizeof rec) return rec;
    FnvnxRelaunchRecord in;
    memcpy(&in, buf, sizeof in);
    if (in.magic != FNVNX_RELAUNCH_MAGIC || in.version != 1 || in.attempts > 1000) return rec;
    return in;
}

int fnvnx_relaunch_decide(const FnvnxRelaunchRecord *prev, uint32_t max_attempts, const FnvnxResvRange *r,
                          uint32_t n, const FnvnxResvResult *res, FnvnxRelaunchRecord *next) {
    uint32_t col = 0;
    fnvnx_resv_unusable(r, n, res, &col);
    uint32_t attempts = prev ? prev->attempts : 0;
    if (!col || attempts >= max_attempts) return 0;
    memset(next, 0, sizeof *next);
    next->magic = FNVNX_RELAUNCH_MAGIC;
    next->version = 1;
    next->attempts = attempts + 1;
    for (uint32_t i = 0; i < n; i++) {
        if ((r[i].flags & FNVNX_RESV_REQUIRED) && res[i].state == FNVNX_RESV_COLLISION && res[i].n_conflicts) {
            next->last_type = res[i].conflict[0].type;
            next->last_addr = res[i].conflict[0].addr;
            next->last_range = r[i].start;
            break;
        }
    }
    return 1;
}
