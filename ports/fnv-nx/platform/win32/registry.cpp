/* registry.cpp - advapi32 registry: RegCreateKeyExA, RegOpenKeyExA, RegQueryValueExA, RegSetValueExA, RegCloseKey.
 *
 * Model: a tree of keys under the four predefined roots; each key owns any number of values (any type, any
 * size) and child keys, held in std::map / std::vector, so nothing is capped. Names are case-insensitive
 * (ASCII fold, as w32_stricmp) and keep the spelling of their creator. A handle is an opaque id (a multiple of
 * 4 below 2^31, so it never meets a predefined root, whose low 32 bits are 0x8000000x) mapped to {key, access,
 * dirty}; ids are never reused while open, so a stale handle yields ERROR_INVALID_HANDLE. Keys are never
 * deleted (the API set has no delete), so a handle's key pointer stays valid until the test reset.
 * Persistence: non-volatile keys and values are written to the hive file by RegCloseKey of a handle (or root)
 * that modified non-volatile data: whole tree, temp file, fsync, rename. The hive is loaded on first use.
 * Hive format (little endian): "FNVREG01", then the four roots in order; a key is
 *   u32 nvalues { u32 namelen, name, u32 type, u32 size, data }*, u32 nkids { u32 namelen, name, key }*
 * written iteratively (preorder), so nesting depth is bounded by memory only. A hive that does not parse
 * (impossible after an atomic write, so only foreign damage) loads as the empty tree.
 * The first boot's content comes from the installer's seed (registry_seed.cpp), not from this file.
 * Errors are returned as LSTATUS, never via SetLastError. One lock covers the tree and the handle table. */
#include "w32.h"

#include <map>
#include <new>
#include <string>
#include <vector>

#ifndef ERROR_INVALID_PARAMETER
#define ERROR_INVALID_PARAMETER 87L
#endif
#ifndef ERROR_BADDB
#define ERROR_BADDB 1009L
#endif
#ifndef ERROR_BADKEY
#define ERROR_BADKEY 1010L
#endif
#ifndef ERROR_CHILD_MUST_BE_VOLATILE
#define ERROR_CHILD_MUST_BE_VOLATILE 1021L
#endif
#ifndef ERROR_REGISTRY_IO_FAILED
#define ERROR_REGISTRY_IO_FAILED 1016L
#endif

namespace {
enum { K_QUERY = 1, K_SET = 2, K_CREATE = 4, K_ALL = 0xF003F, K_READ = 0x20019, K_WRITE = 0x20006 };
enum { OPT_VOLATILE = 1, OPT_KNOWN = 0xF };

struct Val { std::string name; DWORD type; std::string data; };
struct Key {
    std::string name;
    bool vol;
    std::map<std::string, Key *> kids;
    std::map<std::string, Val> vals;
    Key(const std::string &n, bool v) : name(n), vol(v) {}
};
struct Hnd { Key *key; DWORD sam; bool dirty; };

os_lock_t g_lock;
Key *g_roots[4];
bool g_loaded, g_corrupt; /* g_corrupt: the hive exists but does not parse; never overwritten */
std::string g_path; /* empty: the install's hive, C:\\FalloutNV\\registry.hiv (hive_path) */
std::map<uintptr_t, Hnd> g_h;
uintptr_t g_next = 0x100;
Hnd g_rootH[4];

std::string fold(const std::string &s)
{
    std::string r(s);
    for (size_t i = 0; i < r.size(); i++)
        if (r[i] >= 'A' && r[i] <= 'Z')
            r[i] = (char)(r[i] + 32);
    return r;
}

void free_tree(Key *root)
{
    std::vector<Key *> st(1, root);
    while (!st.empty()) {
        Key *k = st.back();
        st.pop_back();
        for (std::map<std::string, Key *>::iterator i = k->kids.begin(); i != k->kids.end(); ++i)
            st.push_back(i->second);
        delete k;
    }
}

void drop_tree(void)
{
    for (int i = 0; i < 4; i++) {
        if (g_roots[i])
            free_tree(g_roots[i]);
        g_roots[i] = NULL;
        g_rootH[i].dirty = false;
    }
    g_h.clear();
}

/* ---- hive file */
/* host path of the hive: the one fnvnx_registry_set_path gave, else the install's own C:\FalloutNV\registry.hiv
 * through the path mounts (the hive is a file beside the game, on the SD card) */
std::string hive_path(void)
{
    if (!g_path.empty()) return g_path;
    char h[W32_PATH_MAX];
    return w32_map_path("C:\\FalloutNV\\registry.hiv", h, sizeof h) == 0 ? std::string(h) : std::string("registry.hiv");
}

void put32(std::string &o, uint32_t v) { for (int i = 0; i < 4; i++) o.push_back((char)(v >> (8 * i))); }

std::string serialize(void)
{
    std::string o("FNVREG01");
    for (int r = 0; r < 4; r++) {
        std::vector<std::pair<Key *, std::map<std::string, Key *>::iterator> > st;
        /* iterative preorder: a key record is values, kid count, then each kid (name, record) */
        st.push_back(std::make_pair(g_roots[r], g_roots[r]->kids.begin()));
        Key *k = g_roots[r];
        put32(o, (uint32_t)k->vals.size());
        for (std::map<std::string, Val>::iterator v = k->vals.begin(); v != k->vals.end(); ++v) {
            put32(o, (uint32_t)v->second.name.size()); o += v->second.name;
            put32(o, v->second.type); put32(o, (uint32_t)v->second.data.size()); o += v->second.data;
        }
        uint32_t nk = 0;
        for (std::map<std::string, Key *>::iterator c = k->kids.begin(); c != k->kids.end(); ++c) nk += !c->second->vol;
        put32(o, nk);
        while (!st.empty()) {
            Key *cur = st.back().first;
            std::map<std::string, Key *>::iterator &it = st.back().second;
            while (it != cur->kids.end() && it->second->vol)
                ++it;
            if (it == cur->kids.end()) { st.pop_back(); continue; }
            Key *c = it->second;
            ++it;
            put32(o, (uint32_t)c->name.size()); o += c->name;
            put32(o, (uint32_t)c->vals.size());
            for (std::map<std::string, Val>::iterator v = c->vals.begin(); v != c->vals.end(); ++v) {
                put32(o, (uint32_t)v->second.name.size()); o += v->second.name;
                put32(o, v->second.type); put32(o, (uint32_t)v->second.data.size()); o += v->second.data;
            }
            uint32_t n = 0;
            for (std::map<std::string, Key *>::iterator q = c->kids.begin(); q != c->kids.end(); ++q) n += !q->second->vol;
            put32(o, n);
            st.push_back(std::make_pair(c, c->kids.begin()));
        }
    }
    return o;
}

struct Rd {
    const std::string &s; size_t p; bool ok;
    Rd(const std::string &str) : s(str), p(8), ok(true) {}
    uint32_t u32() { if (p + 4 > s.size()) { ok = false; return 0; } uint32_t v = 0; for (int i = 0; i < 4; i++) v |= (uint32_t)(uint8_t)s[p + i] << (8 * i); p += 4; return v; }
    std::string bytes(uint32_t n) { if (n > s.size() - p) { ok = false; return std::string(); } std::string r(s, p, n); p += n; return r; }
};

/* reads values and the kid count of key k */
uint32_t read_body(Rd &rd, Key *k)
{
    uint32_t nv = rd.u32();
    for (uint32_t i = 0; i < nv && rd.ok; i++) {
        Val v;
        v.name = rd.bytes(rd.u32()); v.type = rd.u32(); v.data = rd.bytes(rd.u32());
        if (rd.ok) k->vals[fold(v.name)] = v;
    }
    return rd.u32();
}

bool deserialize(const std::string &s)
{
    if (s.size() < 8 || s.compare(0, 8, "FNVREG01")) return false;
    Rd rd(s);
    for (int r = 0; r < 4 && rd.ok; r++) {
        std::vector<std::pair<Key *, uint32_t> > st; /* key, kids still to read */
        uint32_t n = read_body(rd, g_roots[r]);
        st.push_back(std::make_pair(g_roots[r], n));
        while (!st.empty() && rd.ok) {
            if (st.back().second == 0) { st.pop_back(); continue; }
            st.back().second--;
            std::string nm = rd.bytes(rd.u32());
            if (!rd.ok) break;
            Key *c = new Key(nm, false);
            Key *&slot = st.back().first->kids[fold(nm)];
            if (slot) { delete c; rd.ok = false; break; }
            slot = c;
            uint32_t cn = read_body(rd, c);
            st.push_back(std::make_pair(c, cn));
        }
    }
    return rd.ok && rd.p == s.size();
}

bool save(void)
{
    if (g_corrupt) return false;
    std::string o = serialize(), path = hive_path(), tmp = path + ".tmp";
    int fd = os_file_open(tmp.c_str(), OS_O_WRITE | OS_O_CREATE | OS_O_TRUNC);
    if (fd < 0) return false;
    size_t off = 0;
    bool ok = true;
    while (off < o.size()) {
        int64_t w = os_file_write(fd, o.data() + off, o.size() - off);
        if (w <= 0) { ok = false; break; }
        off += (size_t)w;
    }
    ok = ok && os_file_sync(fd) == 0;
    os_file_close(fd);
    if (ok && os_rename(tmp.c_str(), path.c_str()) != 0) ok = false;
    if (!ok) os_remove(tmp.c_str());
    return ok;
}

void ensure_loaded(void)
{
    if (g_loaded) return;
    drop_tree();
    g_corrupt = false;
    for (int i = 0; i < 4; i++) g_roots[i] = new Key("", false);
    g_loaded = true;
    int fd = os_file_open(hive_path().c_str(), OS_O_READ);
    if (fd < 0) return;
    std::string s;
    char buf[65536];
    int64_t r;
    while ((r = os_file_read(fd, buf, sizeof buf)) > 0) s.append(buf, (size_t)r);
    os_file_close(fd);
    if (!deserialize(s)) {
        for (int i = 0; i < 4; i++) { free_tree(g_roots[i]); g_roots[i] = new Key("", false); }
        g_corrupt = true;
    }
}

/* ---- handles */
Hnd *lookup(HKEY h)
{
    uint32_t lo = (uint32_t)(uintptr_t)h;
    if (lo >= 0x80000000u && lo <= 0x80000003u) {
        Hnd *r = &g_rootH[lo - 0x80000000u];
        r->key = g_roots[lo - 0x80000000u];
        r->sam = K_ALL;
        return r;
    }
    std::map<uintptr_t, Hnd>::iterator i = g_h.find((uintptr_t)h);
    return i == g_h.end() ? NULL : &i->second;
}

DWORD effective_sam(DWORD s)
{
    DWORD r = s & 0xFFFF;
    if (s & 0x02000000u) return K_ALL;                       /* MAXIMUM_ALLOWED */
    if (s & (0x80000000u | 0x20000000u)) r |= K_READ;        /* GENERIC_READ, GENERIC_EXECUTE */
    if (s & 0x40000000u) r |= K_WRITE;                       /* GENERIC_WRITE */
    if (s & 0x10000000u) return K_ALL;                       /* GENERIC_ALL */
    return r | (s & K_ALL);
}

HKEY new_handle(Key *k, DWORD sam)
{
    while (g_h.count(g_next) || g_next < 0x100 || g_next >= 0x7FFFFFFCu)
        g_next = (g_next >= 0x7FFFFFFCu || g_next < 0x100) ? 0x100 : g_next + 4;
    uintptr_t id = g_next;
    g_next += 4;
    Hnd h = { k, effective_sam(sam), false };
    g_h[id] = h;
    return (HKEY)id;
}

void split(const char *sub, std::vector<std::string> &out)
{
    std::string cur;
    for (; sub && *sub; sub++) {
        if (*sub == '\\') { if (!cur.empty()) out.push_back(cur); cur.clear(); }
        else cur.push_back(*sub);
    }
    if (!cur.empty()) out.push_back(cur);
}

struct Guard {
    Guard() { os_lock(&g_lock); ensure_loaded(); }
    bool bad() const { return g_corrupt; }
    ~Guard() { os_unlock(&g_lock); }
};

LSTATUS do_open(HKEY parent, LPCSTR sub, DWORD sam, PHKEY out, bool create, DWORD opts, LPDWORD disp)
{
    Guard g;
    if (g.bad()) return ERROR_BADDB;
    Hnd *p = lookup(parent);
    if (!p) return ERROR_INVALID_HANDLE;
    std::vector<std::string> parts;
    split(sub, parts);
    Key *cur = p->key;
    size_t i = 0;
    for (; i < parts.size(); i++) {
        std::map<std::string, Key *>::iterator f = cur->kids.find(fold(parts[i]));
        if (f == cur->kids.end()) break;
        cur = f->second;
    }
    bool created = i < parts.size();
    if (created) {
        if (!create) return ERROR_FILE_NOT_FOUND;
        bool vol = (opts & OPT_VOLATILE) != 0;
        if (!(p->sam & K_CREATE)) return ERROR_ACCESS_DENIED;
        if (cur->vol && !vol) return ERROR_CHILD_MUST_BE_VOLATILE;
        for (; i < parts.size(); i++) {
            Key *k = new Key(parts[i], vol);
            cur->kids[fold(parts[i])] = k;
            cur = k;
        }
        if (!vol) p->dirty = true;
    }
    if (disp) *disp = created ? REG_CREATED_NEW_KEY : REG_OPENED_EXISTING_KEY;
    *out = new_handle(cur, sam);
    return ERROR_SUCCESS;
}
} // namespace

extern "C" void fnvnx_registry_set_path(const char *host_path)
{
    os_lock(&g_lock);
    if (g_loaded) {
        bool dirty = false;
        for (int i = 0; i < 4; i++) dirty = dirty || g_rootH[i].dirty;
        for (std::map<uintptr_t, Hnd>::iterator i = g_h.begin(); i != g_h.end(); ++i) dirty = dirty || i->second.dirty;
        if (dirty) save(); /* pending changes go to the old path; open handles are dropped with the tree */
        drop_tree();
        g_loaded = false;
        g_corrupt = false;
    }
    g_path = host_path ? host_path : "";
    os_unlock(&g_lock);
}

/* 1 if the hive file exists (registry_seed.cpp: the first boot seeds only when it does not) */
extern "C" int fnvnx_registry_hive_present(void)
{
    os_lock(&g_lock);
    os_stat_t st;
    int r = os_stat(hive_path().c_str(), &st) == 0;
    os_unlock(&g_lock);
    return r;
}

extern "C" void fnvnx_registry_reset_for_test(void)
{
    os_lock(&g_lock);
    drop_tree();
    g_loaded = false;
    g_corrupt = false;
    os_unlock(&g_lock);
}

extern "C" LSTATUS RegOpenKeyExA(HKEY k, LPCSTR sub, DWORD o, REGSAM s, PHKEY out)
{
    W32_UNUSED(o);
    if (!out) return ERROR_INVALID_PARAMETER;
    try { return do_open(k, sub, s, out, false, 0, NULL); } catch (const std::bad_alloc &) { return ERROR_NOT_ENOUGH_MEMORY; }
}

/* the 16-bit-era wrapper: no subkey (NULL or empty) hands back the same key handle, else RegOpenKeyExA with
 * MAXIMUM_ALLOWED access */
extern "C" LSTATUS RegOpenKeyA(HKEY k, LPCSTR sub, PHKEY out)
{
    if (!out) return ERROR_INVALID_PARAMETER;
    if (!sub || !*sub) {
        *out = k;
        return ERROR_SUCCESS;
    }
    return RegOpenKeyExA(k, sub, 0, 0x02000000u, out);
}

extern "C" LSTATUS RegCreateKeyExA(HKEY k, LPCSTR sub, DWORD r, LPSTR c, DWORD o, REGSAM s, void *sa, PHKEY out, LPDWORD disp)
{
    W32_UNUSED(r); W32_UNUSED(c); W32_UNUSED(sa);
    if (!out || (o & ~(DWORD)OPT_KNOWN)) return ERROR_INVALID_PARAMETER;
    if (!sub) return ERROR_BADKEY;
    if (o & 4) return ERROR_ACCESS_DENIED; /* REG_OPTION_BACKUP_RESTORE needs a privilege this process lacks */
    try { return do_open(k, sub, s, out, true, o, disp); } catch (const std::bad_alloc &) { return ERROR_NOT_ENOUGH_MEMORY; }
}

extern "C" LSTATUS RegQueryValueExA(HKEY k, LPCSTR name, LPDWORD res, LPDWORD type, LPBYTE data, LPDWORD len)
{
    W32_UNUSED(res);
    Guard g;
    if (g.bad()) return ERROR_BADDB;
    Hnd *h = lookup(k);
    if (!h) return ERROR_INVALID_HANDLE;
    if (!(h->sam & K_QUERY)) return ERROR_ACCESS_DENIED;
    if (data && !len) return ERROR_INVALID_PARAMETER;
    try {
        std::map<std::string, Val>::iterator v = h->key->vals.find(fold(name ? name : ""));
        if (v == h->key->vals.end()) return ERROR_FILE_NOT_FOUND;
        const Val &val = v->second;
        DWORD n = (DWORD)val.data.size();
        if (type) *type = val.type;
        if (data) {
            DWORD cap = *len;
            *len = n;
            if (cap < n) return ERROR_MORE_DATA;
            if (n) memcpy(data, val.data.data(), n);
        } else if (len) {
            *len = n;
        }
        return ERROR_SUCCESS;
    } catch (const std::bad_alloc &) { return ERROR_NOT_ENOUGH_MEMORY; }
}

extern "C" LSTATUS RegSetValueExA(HKEY k, LPCSTR name, DWORD res, DWORD type, const BYTE *data, DWORD len)
{
    W32_UNUSED(res);
    Guard g;
    if (g.bad()) return ERROR_BADDB;
    Hnd *h = lookup(k);
    if (!h) return ERROR_INVALID_HANDLE;
    if (!(h->sam & K_SET)) return ERROR_ACCESS_DENIED;
    if (!data && len) return ERROR_INVALID_PARAMETER;
    try {
        Val v;
        v.name = name ? name : "";
        v.type = type;
        if (len) v.data.assign((const char *)data, len);
        h->key->vals[fold(v.name)] = v;
        if (!h->key->vol) h->dirty = true;
        return ERROR_SUCCESS;
    } catch (const std::bad_alloc &) { return ERROR_NOT_ENOUGH_MEMORY; }
}

extern "C" LSTATUS RegCloseKey(HKEY k)
{
    Guard g;
    if (g.bad()) return ERROR_BADDB;
    Hnd *h = lookup(k);
    if (!h) return ERROR_INVALID_HANDLE;
    bool dirty = h->dirty;
    h->dirty = false;
    if ((uint32_t)(uintptr_t)k < 0x80000000u || (uint32_t)(uintptr_t)k > 0x80000003u) g_h.erase((uintptr_t)k);
    if (!dirty) return ERROR_SUCCESS;
    try { return save() ? ERROR_SUCCESS : ERROR_REGISTRY_IO_FAILED; } catch (const std::bad_alloc &) { return ERROR_NOT_ENOUGH_MEMORY; }
}
