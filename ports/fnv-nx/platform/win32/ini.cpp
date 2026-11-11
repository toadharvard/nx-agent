/* ini.cpp - GetPrivateProfile* / WritePrivateProfile* with the semantics of the Win32 profile API.
 *
 * The engine reads Fallout.ini, FalloutPrefs.ini and Fallout_default.ini through these (hundreds of calls at
 * start-up), so files are parsed once and cached by host path, re-read when size or mtime change, and written
 * through on every Write call (the change is visible to the next read, as on Windows).
 *
 * Rules (matching kernel32 / Wine):
 *  - section and key names are case-insensitive; leading/trailing blanks are ignored; the first matching section
 *    and the first matching key win;
 *  - lines starting with ';' are comments; a line without '=' inside a section is a key with an empty value;
 *  - a value is trimmed and one pair of matching surrounding quotes (" or ') is removed;
 *  - a NULL section lists the section names, a NULL key lists the keys of the section, each 0-terminated plus a
 *    final 0; on truncation those lists return size - 2, a single value returns size - 1;
 *  - a missing value copies the default with trailing blanks removed (NULL default = "");
 *  - GetPrivateProfileInt reads leading blanks, one optional sign and decimal digits up to the first non-digit (no
 *    radix prefixes: "0x10" is 0); an empty or missing value returns the default;
 *  - a file name without a directory refers to the Windows directory (C:\Windows\name).
 *
 * Allocation: every container allocates through w32_alloc (libc malloc); a failure throws std::bad_alloc, which the
 * entry points turn into their documented failure (0 / FALSE with ERROR_NOT_ENOUGH_MEMORY), the lock released.
 */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

#include <new>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

/* Everything here lives on libc malloc, not the global operator new: the guest's operator new is the game's
 * MemoryManager once the image is mapped (toolchain/game/global_new.cpp), and these functions are kernel32's, which on
 * Windows never allocate from the exe's replacement new. A game heap in a bad state (stand-ins, D26) had corrupted
 * the profile cache's strings there (S5's --alloc-check: 22,251 unknown-block and 4,215 double frees from
 * section_of / key_of). */
template <class T> struct w32_alloc : std::allocator<T> {
    using std::allocator<T>::allocator;   /* stateless: equality and copies are std::allocator's */
    template <class U> struct rebind { typedef w32_alloc<U> other; };
    T *allocate(size_t n)
    {
        if (n > (size_t)-1 / sizeof(T))
            throw std::bad_array_new_length();
        size_t bytes = n * sizeof(T);
        void *p = malloc(bytes ? bytes : 1);
        if (!p)
            throw std::bad_alloc();
        return (T *)p;
    }
    void deallocate(T *p, size_t) noexcept { free(p); }
};
typedef std::basic_string<char, std::char_traits<char>, w32_alloc<char>> pstring;
template <class T> using pvec = std::vector<T, w32_alloc<T>>;
struct phash {
    size_t operator()(const pstring &s) const
    {
        size_t h = 2166136261u;
        for (char c : s)
            h = (h ^ (unsigned char)c) * 16777619u;
        return h;
    }
};
template <class V> using pmap = std::unordered_map<pstring, V, phash, std::equal_to<pstring>,
                                                    w32_alloc<std::pair<const pstring, V>>>;
template <class T> T *pnew()   /* a T on malloc */
{
    void *m = malloc(sizeof(T));
    if (!m)
        throw std::bad_alloc();
    return new (m) T();
}
template <class T> void pdelete(T *p)
{
    if (p) {
        p->~T();
        free(p);
    }
}

/* The lookup index of a parsed file (built once per parse or write, not per call: the engine reads its settings one
 * key at a time, thousands of calls against files of ~1,500 lines, and a scan of every line per call cost minutes of
 * emulation in the boot's INI phase). Names are ASCII-folded, as w32_stricmp compares; the first section of a name
 * and the first key of a name in it win, as the scans did. */
struct IniSection {
    size_t first, end;                                    /* [header line, next header) */
    pmap<pstring> values;  /* folded key -> value (trimmed, one pair of quotes off) */
};
struct IniFile {
    pstring host;
    pvec<pstring> lines;
    bool crlf = true;
    bool exists = false;
    uint64_t size = 0;
    int64_t mtime = 0;
    bool indexed = false;                                 /* index matches lines */
    pmap<IniSection> index;    /* folded section name -> section */
};

os_lock_t g_ini_lock;
struct IniLock {   /* released on every exit, an exception included */
    IniLock() { os_lock(&g_ini_lock); }
    ~IniLock() { os_unlock(&g_ini_lock); }
};
/* process-lifetime cache; a heap vector so no static destructor runs at exit (other threads may still read). Made on
 * first use, so a game static that reads an ini file before the platform's constructors ran still finds it. */
pvec<IniFile *> &ini_cache()
{
    static pvec<IniFile *> &c = *pnew<pvec<IniFile *>>();
    return c;
}

pstring trim(const pstring &s)
{
    size_t b = 0, e = s.size();
    while (b < e && (s[b] == ' ' || s[b] == '\t'))
        b++;
    while (e > b && (s[e - 1] == ' ' || s[e - 1] == '\t' || s[e - 1] == '\r'))
        e--;
    return s.substr(b, e - b);
}

pstring fold(const char *s)
{
    pstring r(s);
    for (char &c : r)
        if (c >= 'A' && c <= 'Z')
            c = (char)(c - 'A' + 'a');
    return r;
}

bool ieq(const pstring &a, const char *b) { return w32_stricmp(a.c_str(), b) == 0; }

/* the section name of a "[name]" line, or false */
bool section_of(const pstring &line, pstring &name)
{
    pstring t = trim(line);
    if (t.empty() || t[0] != '[')
        return false;
    size_t e = t.find(']');
    name = trim(t.substr(1, e == pstring::npos ? pstring::npos : e - 1));
    return true;
}

/* key/value of a key line; false for comments and blank lines */
bool key_of(const pstring &line, pstring &key, pstring *val)
{
    pstring t = trim(line);
    if (t.empty() || t[0] == ';' || t[0] == '[')
        return false;
    size_t eq = t.find('=');
    key = trim(t.substr(0, eq));
    if (val) {
        *val = eq == pstring::npos ? pstring() : trim(t.substr(eq + 1));
        if (val->size() >= 2 && ((*val)[0] == '"' || (*val)[0] == '\'') && val->back() == (*val)[0])
            *val = val->substr(1, val->size() - 2);
    }
    return true;
}

bool load(IniFile *f)
{
    os_stat_t st;
    if (os_stat(f->host.c_str(), &st) != 0 || st.is_dir) {
        f->indexed = false;
        f->lines.clear();
        f->exists = false;
        f->size = 0;
        f->mtime = 0;
        return false;
    }
    if (f->exists && st.size == f->size && st.mtime_us == f->mtime)
        return true;
    f->lines.clear();
    f->indexed = false;
    f->exists = true;
    f->size = st.size;
    f->mtime = st.mtime_us;
    int fd = os_file_open(f->host.c_str(), OS_O_READ);
    if (fd < 0)
        return false;
    pstring data;
    char buf[16384];
    int64_t r;
    while ((r = os_file_read(fd, buf, sizeof buf)) > 0)
        data.append(buf, (size_t)r);
    os_file_close(fd);
    f->crlf = data.find("\r\n") != pstring::npos || data.find('\n') == pstring::npos;
    size_t p = 0;
    if (data.size() >= 3 && (unsigned char)data[0] == 0xEF && (unsigned char)data[1] == 0xBB && (unsigned char)data[2] == 0xBF)
        p = 3; /* UTF-8 BOM */
    while (p < data.size()) {
        size_t e = data.find('\n', p);
        pstring line = data.substr(p, e == pstring::npos ? pstring::npos : e - p);
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        f->lines.push_back(line);
        if (e == pstring::npos)
            break;
        p = e + 1;
    }
    return true;
}

bool save(IniFile *f)
{
    pstring out;
    const char *eol = f->crlf ? "\r\n" : "\n";
    for (const pstring &l : f->lines) {
        out += l;
        out += eol;
    }
    int fd = os_file_open(f->host.c_str(), OS_O_WRITE | OS_O_CREATE | OS_O_TRUNC);
    if (fd < 0) {
        SetLastError(w32_os_error(fd) == ERROR_FILE_NOT_FOUND ? ERROR_PATH_NOT_FOUND : w32_os_error(fd));
        return false;
    }
    int64_t w = os_file_write(fd, out.data(), out.size());
    os_file_close(fd);
    os_stat_t st;
    if (os_stat(f->host.c_str(), &st) == 0) {
        f->exists = true;
        f->size = st.size;
        f->mtime = st.mtime_us;
    }
    return w == (int64_t)out.size();
}

/* cached file for a profile name; NULL + last error on a bad name. Caller holds g_ini_lock. */
pmap<IniFile *> &ini_names()
{
    static pmap<IniFile *> &m = *pnew<pmap<IniFile *>>();
    return m;
}

IniFile *open_ini(const char *name)
{
    if (!name || !*name) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    /* a name that does not depend on the current directory (a drive, or no directory: C:\Windows) maps to the same
     * host path every time: skip the path mapping on later calls */
    bool fixed = strchr(name, ':') || (!strchr(name, '\\') && !strchr(name, '/'));
    if (fixed) {
        auto it = ini_names().find(name);
        if (it != ini_names().end()) {
            load(it->second);
            return it->second;
        }
    }
    pstring win = name;
    if (win.find('\\') == pstring::npos && win.find('/') == pstring::npos && win.find(':') == pstring::npos)
        win = "C:\\Windows\\" + win;
    char host[W32_PATH_MAX];
    DWORD e = w32_map_path(win.c_str(), host, sizeof host);
    if (e) {
        SetLastError(e);
        return NULL;
    }
    for (IniFile *f : ini_cache())
        if (f->host == host) {
            load(f);
            if (fixed)
                ini_names()[name] = f;
            return f;
        }
    IniFile *f = pnew<IniFile>();
    f->host = host;
    load(f);
    ini_cache().push_back(f);
    if (fixed)
        ini_names()[name] = f;
    return f;
}

void build_index(IniFile *f)
{
    if (f->indexed)
        return;
    f->index.clear();
    IniSection *cur = NULL;
    pstring n, k, v;
    for (size_t i = 0; i < f->lines.size(); i++) {
        if (section_of(f->lines[i], n)) {
            if (cur)
                cur->end = i;
            auto r = f->index.emplace(fold(n.c_str()), IniSection{i, i + 1, {}});
            cur = r.second ? &r.first->second : NULL;   /* a later section of the same name is never read */
            continue;
        }
        if (cur && key_of(f->lines[i], k, &v))
            cur->values.emplace(fold(k.c_str()), v);    /* the first key of a name wins */
    }
    if (cur)
        cur->end = f->lines.size();
    f->indexed = true;
}

const IniSection *section_ix(IniFile *f, const char *sec)
{
    build_index(f);
    auto it = f->index.find(fold(sec));   /* the caller's name as given, as the scans compared it */
    return it == f->index.end() ? NULL : &it->second;
}

/* [first, end) line range of the first section called sec (first = header line), or false */
bool find_section(IniFile *f, const char *sec, size_t &first, size_t &end)
{
    const IniSection *s = section_ix(f, sec);
    if (!s)
        return false;
    first = s->first;
    end = s->end;
    return true;
}

/* copy a 0-terminated list (names separated by 0, final extra 0) with the truncation rule */
DWORD copy_list(const pvec<pstring> &items, LPSTR buf, DWORD size)
{
    if (size == 0)
        return 0;
    if (size == 1) {
        buf[0] = 0;
        return 0;
    }
    DWORD o = 0;
    for (const pstring &s : items) {
        for (char c : s) {
            if (o + 2 >= size) { /* room for the two terminators */
                buf[o] = 0;
                buf[o + 1] = 0;
                return size - 2;
            }
            buf[o++] = c;
        }
        if (o + 2 >= size) {
            buf[o] = 0;
            buf[o + 1] = 0;
            return size - 2;
        }
        buf[o++] = 0;
    }
    buf[o] = 0;
    return o;
}

DWORD copy_value(const pstring &v, LPSTR buf, DWORD size)
{
    if (size == 0)
        return 0;
    DWORD n = (DWORD)v.size();
    if (n > size - 1)
        n = size - 1;
    memcpy(buf, v.data(), n);
    buf[n] = 0;
    return n;
}

DWORD get_string(const char *sec, const char *key, const char *def, LPSTR buf, DWORD size, const char *file)
{
    size = W32_DW(size);
    if (!buf)
        return 0;
    IniLock guard;
    IniFile *f = open_ini(file);
    DWORD r;
    if (!f) {
        r = copy_value("", buf, size);
    } else if (!sec) {
        pvec<pstring> names;
        pstring n;
        for (const pstring &l : f->lines)
            if (section_of(l, n))
                names.push_back(n);
        r = copy_list(names, buf, size);
    } else {
        size_t a, b;
        bool have = find_section(f, sec, a, b);
        if (!key) {
            pvec<pstring> keys;
            pstring k;
            if (have)
                for (size_t i = a + 1; i < b; i++)
                    if (key_of(f->lines[i], k, NULL) && !k.empty())
                        keys.push_back(k);
            r = copy_list(keys, buf, size);
        } else {
            pstring v;
            bool found = false;
            if (have) {
                const IniSection *s = section_ix(f, sec);
                auto it = s->values.find(fold(key));
                if (it != s->values.end()) {
                    v = it->second;
                    found = true;
                }
            }
            if (!found) {
                v = def ? def : "";
                while (!v.empty() && v.back() == ' ')
                    v.pop_back();
                SetLastError(f->exists ? ERROR_SUCCESS : ERROR_FILE_NOT_FOUND);
            } else {
                SetLastError(ERROR_SUCCESS);
            }
            r = copy_value(v, buf, size);
        }
    }
    return r;
}

/* the entry points' failure: out of memory, the only way a profile call fails here besides its own error codes */
template <class F> auto guarded(F body, decltype(body()) failed) -> decltype(body())
{
    try {
        return body();
    } catch (const std::bad_alloc &) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return failed;
    }
}

} // namespace

static BOOL write_string(LPCSTR sec, LPCSTR key, LPCSTR val, LPCSTR file);

extern "C" DWORD GetPrivateProfileStringA(LPCSTR sec, LPCSTR key, LPCSTR def, LPSTR buf, DWORD size, LPCSTR file)
{
    return guarded([&] { return get_string(sec, key, def, buf, size, file); }, (DWORD)0);
}

extern "C" UINT GetPrivateProfileIntA(LPCSTR sec, LPCSTR key, INT def, LPCSTR file)
{
    char buf[30];
    if (!GetPrivateProfileStringA(sec, key, "", buf, sizeof buf, file) || !buf[0])
        return (UINT)def;
    const char *p = buf;
    while (*p == ' ' || *p == '\t')
        p++;
    bool neg = *p == '-';
    if (*p == '-' || *p == '+')
        p++;
    uint32_t v = 0;
    for (; *p >= '0' && *p <= '9'; p++)
        v = v * 10u + (uint32_t)(*p - '0');
    return (UINT)(neg ? 0u - v : v);
}

extern "C" DWORD GetPrivateProfileSectionNamesA(LPSTR buf, DWORD size, LPCSTR file)
{
    return GetPrivateProfileStringA(NULL, NULL, NULL, buf, size, file);
}

extern "C" DWORD GetPrivateProfileSectionA(LPCSTR sec, LPSTR buf, DWORD size, LPCSTR file)
{
    size = W32_DW(size);
    return guarded([&]() -> DWORD {
    IniLock guard;
    IniFile *f = sec ? open_ini(file) : NULL;
    pvec<pstring> items;
    size_t a, b;
    if (f && find_section(f, sec, a, b))
        for (size_t i = a + 1; i < b; i++) {
            pstring t = trim(f->lines[i]);
            if (!t.empty() && t[0] != ';')
                items.push_back(t);
        }
    return copy_list(items, buf, size);
    }, (DWORD)0);
}

static BOOL write_string(LPCSTR sec, LPCSTR key, LPCSTR val, LPCSTR file)
{
    if (!sec) {
        /* (NULL, NULL, NULL, file): flush; we write through, so drop the cache instead */
        IniLock guard;
        for (IniFile *f : ini_cache())
            pdelete(f);
        ini_cache().clear();
        ini_names().clear();
        return TRUE;
    }
    IniLock guard;
    IniFile *f = open_ini(file);
    BOOL ok = FALSE;
    if (f) {
        size_t a, b;
        bool have = find_section(f, sec, a, b);
        bool changed = false;
        if (!key) {
            if (have) {
                f->lines.erase(f->lines.begin() + (long)a, f->lines.begin() + (long)b);
                changed = true;
            }
        } else {
            pstring k;
            size_t at = (size_t)-1, last_key = a;
            if (have)
                for (size_t i = a + 1; i < b; i++)
                    if (key_of(f->lines[i], k, NULL)) {
                        last_key = i;
                        if (at == (size_t)-1 && ieq(k, key))
                            at = i;
                    }
            if (!val) {
                if (at != (size_t)-1) {
                    f->lines.erase(f->lines.begin() + (long)at);
                    changed = true;
                }
            } else {
                pstring line = trim(key) + "=" + val;
                if (at != (size_t)-1) {
                    f->lines[at] = line;
                } else if (have) {
                    f->lines.insert(f->lines.begin() + (long)last_key + 1, line);
                } else {
                    if (!f->lines.empty() && !trim(f->lines.back()).empty())
                        f->lines.push_back("");
                    f->lines.push_back("[" + pstring(sec) + "]");
                    f->lines.push_back(line);
                }
                changed = true;
            }
        }
        if (changed)
            f->indexed = false;
        ok = changed ? save(f) : TRUE;
    }
    return ok;
}

extern "C" BOOL WritePrivateProfileStringA(LPCSTR sec, LPCSTR key, LPCSTR val, LPCSTR file)
{
    return guarded([&] { return write_string(sec, key, val, file); }, (BOOL)FALSE);
}

extern "C" BOOL WritePrivateProfileSectionA(LPCSTR sec, LPCSTR data, LPCSTR file)
{
    /* data: "key=value\0key=value\0\0"; replaces the whole section; NULL deletes it */
    if (!sec)
        return FALSE;
    return guarded([&]() -> BOOL {
    if (!write_string(sec, NULL, NULL, file))
        return FALSE;
    if (!data)
        return TRUE;
    for (const char *p = data; *p; p += strlen(p) + 1) {
        pstring l = p, k = l, v;
        size_t eq = l.find('=');
        if (eq != pstring::npos) {
            k = l.substr(0, eq);
            v = l.substr(eq + 1);
        }
        if (!write_string(sec, k.c_str(), v.c_str(), file))
            return FALSE;
    }
    return TRUE;
    }, (BOOL)FALSE);
}
