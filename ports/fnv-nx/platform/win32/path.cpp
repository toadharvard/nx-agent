/* path.cpp - Windows paths -> host paths (scheme: docs/RUNTIME.md "Paths").
 *
 * The game sees a small virtual Windows file system:
 *     C:\FalloutNV\                       the install (cwd at start; GetModuleFileName = C:\FalloutNV\FalloutNV.exe)
 *     C:\Users\Player\                    profile: Documents\My Games\FalloutNV\ (Fallout.ini, Saves\),
 *                                         AppData\Local\FalloutNV\ (plugins.txt)
 *     any other X:\...                    <root>/drive_x/... (sandboxed, nothing escapes the root)
 * mounted on the host as
 *     <root>  <root>/user  <root>/drive_<x>
 * with <root> = sdmc:/switch/fnv on the Switch (toolchain/include/fnvnx/sdlayout.h: the player copies FalloutNV.exe
 * and Data/ there, next to fnv.nro, as in a PC install) and $FNVNX_ROOT (default ./fnvnx-root) on the host.
 *
 * Normalisation follows Win32: '/' and '\' both separate, "." and ".." are resolved lexically (".." stops at the
 * drive root), empty components collapse, trailing dots/spaces of a component are dropped, relative and
 * drive-rooted ("\x") paths resolve against the virtual current directory. Each existing component is then
 * matched case-insensitively against the host directory (exact spelling first); components that do not exist
 * keep the given spelling, so new files are created as the game names them. The SD card folds case itself,
 * so the directory scan only runs on case-sensitive hosts.
 */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

#define MAX_MOUNTS 16
struct mount { char prefix[W32_PATH_MAX]; size_t ncomp; char host[W32_PATH_MAX]; };
static struct mount g_mounts[MAX_MOUNTS];
static int g_nmounts;
static char g_root[W32_PATH_MAX];
static char g_cwd[W32_PATH_MAX] = "C:\\FalloutNV";
static os_lock_t g_path_lock;


static int is_sep(char c) { return c == '\\' || c == '/'; }

/* ---- Windows 7 canonical names (what RtlGetFullPathName_U returns; reference data: Wine 9.0 ntdll/tests/path.c,
 * whose expected values are Windows results). Rules:
 *  - '/' and '\' separate; empty components collapse; "." drops; ".." removes the previous component and stops at the
 *    root (drive root, UNC "\\server\share", the "\\.\" and "\\?\" prefixes);
 *  - the last component (the text after the final separator) loses ALL trailing dots and spaces; a component that is
 *    followed by a separator loses ONE trailing dot ("test./f" is "test\f", "test../f" is "test.\f", "test.. /f"
 *    stays); a last component that empties out leaves the path ending in a separator;
 *  - a DOS device name (CON PRN AUX NUL COM1-9 LPT1-9 as the last component, with an optional "X:" before it, any
 *    directory, any extension, trailing spaces, a trailing ':') is "\\.\<name as spelled>" (RtlIsDosDeviceName_U of
 *    Windows 7: the name ends at the first '.' or ':', trailing spaces of the name are ignored);
 *  - "X:rest" is relative to the current directory of drive X (the "=X:" variable of the environment block, else
 *    "X:\"); a relative or "\rooted" path uses the current drive and directory. The drive letter keeps its spelling
 *    when the path names the drive absolutely. */
enum { CK_DRIVE, CK_UNC, CK_DEVNS, CK_NTNS, CK_DEVICE };

extern "C" DWORD GetEnvironmentVariableA(LPCSTR, LPSTR, DWORD);   /* crt_kernel32.cpp: the environment block */

static int is_letter(char c) { return (c | 32) >= 'a' && (c | 32) <= 'z'; }

/* s[0..len) starts with a DOS device name; the name's length in *name_len */
static int dev_base(const char *s, size_t len, size_t *name_len)
{
    size_t e = 0;
    while (e < len && s[e] != '.' && s[e] != ':')
        e++;
    while (e && s[e - 1] == ' ')
        e--;
    if (e == 3 && (!w32_strnicmp(s, "NUL", 3) || !w32_strnicmp(s, "CON", 3) || !w32_strnicmp(s, "PRN", 3) ||
                   !w32_strnicmp(s, "AUX", 3))) {
        *name_len = e;
        return 1;
    }
    if (e == 4 && (!w32_strnicmp(s, "COM", 3) || !w32_strnicmp(s, "LPT", 3)) && s[3] >= '1' && s[3] <= '9') {
        *name_len = e;
        return 1;
    }
    return 0;
}

static DWORD canon(const char *in, char *out, size_t n, int *kind, int *root_len)
{
    if (!in || !*in)
        return ERROR_PATH_NOT_FOUND;
    char pre[W32_PATH_MAX * 2 + 8];     /* the input with the current directory glued on when it is relative */
    char head[16];                      /* the root text: "C:\", "\\.\", "\\?\", "\\" */
    const char *rest;
    size_t minc = 0;                    /* components ".." cannot remove */
    int k = CK_DRIVE;
    if (strlen(in) >= W32_PATH_MAX)
        return 206; /* ERROR_FILENAME_EXCED_RANGE */
    if (is_sep(in[0]) && is_sep(in[1])) {
        if ((in[2] == '.' || in[2] == '?') && (is_sep(in[3]) || !in[3])) {
            k = in[2] == '.' ? CK_DEVNS : CK_NTNS;
            snprintf(head, sizeof head, "\\\\%c\\", in[2]);
            rest = in[3] ? in + 4 : in + 3;
        } else {
            k = CK_UNC;
            snprintf(head, sizeof head, "\\\\");
            rest = in + 2;
            minc = 2;
        }
    } else {
        const char *base = is_letter(in[0]) && in[1] == ':' ? in + 2 : in;   /* behind an optional "X:" */
        const char *last = base;
        for (const char *q = base; *q; q++)
            if (is_sep(*q))
                last = q + 1;
        size_t dl;
        if (dev_base(last, strlen(last), &dl)) {
            if (n < dl + 5)
                return 206;
            snprintf(out, n, "\\\\.\\%.*s", (int)dl, last);
            *kind = CK_DEVICE;
            *root_len = 4;
            return 0;
        }
        char cwd[W32_PATH_MAX];
        w32_virtual_cwd(cwd, sizeof cwd);
        if (base != in && !is_sep(*base)) {                  /* "C:foo": the current directory of drive C */
            char var[4] = { '=', (char)(in[0] & ~32), ':', 0 }, val[W32_PATH_MAX];
            if ((in[0] | 32) == (cwd[0] | 32))
                snprintf(pre, sizeof pre, "%s\\%s", cwd, base);
            else if (GetEnvironmentVariableA(var, val, sizeof val) && val[0])
                snprintf(pre, sizeof pre, "%s\\%s", val, base);
            else
                snprintf(pre, sizeof pre, "%c:\\%s", in[0] & ~32, base);
            in = pre;
        } else if (base == in && !is_sep(in[0])) {           /* relative: the current directory */
            snprintf(pre, sizeof pre, "%s\\%s", cwd, in);
            in = pre;
        } else if (base == in) {                              /* "\rooted": the current drive */
            snprintf(pre, sizeof pre, "%c:%s", cwd[0], in);
            in = pre;
        }
        snprintf(head, sizeof head, "%c:\\", in[0]);
        rest = in + 2;
    }
    const char *comps[256];
    size_t lens[256], nc = 0;
    int trail = 0;
    const char *p = rest;
    while (*p) {
        while (is_sep(*p)) {
            p++;
            trail = 1;
        }
        if (!*p)
            break;
        trail = 0;
        const char *s = p;
        while (*p && !is_sep(*p))
            p++;
        size_t len = (size_t)(p - s);
        if (len == 1 && s[0] == '.')
            continue;
        if (len == 2 && s[0] == '.' && s[1] == '.') {
            if (nc > minc)
                nc--;
            continue;
        }
        if (!*p) {                                             /* the last component */
            while (len && (s[len - 1] == '.' || s[len - 1] == ' '))
                len--;
            if (!len)
                trail = 1;
        } else if (s[len - 1] == '.')
            len--;
        if (!len || (len <= 2 && s[0] == '.' && (len == 1 || s[1] == '.')))
            continue;       /* a component that strips down to nothing, "." or ".." names nothing */
        if (nc == 256)
            return 206;
        comps[nc] = s;
        lens[nc] = len;
        nc++;
    }
    size_t o = strlen(head);
    if (o + 1 > n)
        return 206;
    memcpy(out, head, o);
    for (size_t i = 0; i < nc; i++) {
        if (o + lens[i] + 3 > n)
            return 206;
        if (i)
            out[o++] = '\\';
        memcpy(out + o, comps[i], lens[i]);
        o += lens[i];
    }
    if (trail && nc)
        out[o++] = '\\';
    out[o] = 0;
    *kind = k;
    *root_len = (int)strlen(head);
    return 0;
}

/* normalise into "x:\comp\comp" (drive lower-case, no trailing separator; root is "x:\"), the form the mount table
 * and the host mapping use. Names outside the file system have no such form: UNC (no network, ENV N1), DOS devices
 * and device-namespace names are not host paths. */
static DWORD normalize(const char *in, char *out, size_t n)
{
    char c[W32_PATH_MAX * 2];
    int kind, rl;
    DWORD e = canon(in, c, sizeof c, &kind, &rl);
    if (e)
        return e;
    const char *p = c;
    if (kind == CK_UNC)
        return ERROR_PATH_NOT_FOUND;                  /* UNC shares do not exist here */
    if (kind == CK_DEVICE)
        return ERROR_FILE_NOT_FOUND;
    if (kind == CK_DEVNS || kind == CK_NTNS) {
        p = c + 4;                                     /* \\?\C:\... and \\.\C:\... name a drive path */
        if (!(is_letter(p[0]) && p[1] == ':' && (!p[2] || p[2] == '\\')))
            return ERROR_FILE_NOT_FOUND;               /* a device or object name, not a file */
    }
    size_t l = strlen(p);
    if (l + 2 > n)
        return 206;
    memcpy(out, p, l + 1);
    out[0] = (char)(out[0] | 32);
    if (l == 2) {                                      /* "x:" */
        out[2] = '\\';
        out[3] = 0;
        l = 3;
    }
    if (l > 3 && out[l - 1] == '\\')
        out[--l] = 0;
    return 0;
}

/* the classification CreateFile needs before it maps a name: 0 for a name in the file system, else the kind */
int w32_name_class(const char *in, char *dev, size_t n)
{
    char c[W32_PATH_MAX * 2];
    int kind, rl;
    if (!in || canon(in, c, sizeof c, &kind, &rl))
        return W32_NC_FILE;
    if (kind == CK_UNC)
        return W32_NC_UNC;
    const char *name = NULL;
    if (kind == CK_DEVICE)
        name = c + 4;
    else if (kind == CK_DEVNS || kind == CK_NTNS) {
        const char *r = c + 4;
        if (!(is_letter(r[0]) && r[1] == ':' && (!r[2] || r[2] == '\\'))) {
            size_t dl;
            if (!w32_strnicmp(r, "UNC\\", 4) && kind == CK_NTNS)
                return W32_NC_UNC;
            if (!w32_strnicmp(r, "pipe", 4) && (!r[4] || r[4] == '\\'))
                return W32_NC_PIPE;
            if (!w32_stricmp(r, "CONIN$") || !w32_stricmp(r, "CONOUT$"))
                return W32_NC_CONSOLE;
            if (dev_base(r, strlen(r), &dl) && dl == strlen(r))
                name = r;
            else
                return W32_NC_NONE;                    /* no such object in the device namespace */
        }
    } else if (!w32_stricmp(in, "CONIN$") || !w32_stricmp(in, "CONOUT$"))
        return W32_NC_CONSOLE;                         /* kernel32 opens these two names on the console */
    if (!name)
        return W32_NC_FILE;
    if (n)
        snprintf(dev, n, "%.3s", name);                /* NUL CON PRN AUX COM LPT */
    if (!w32_strnicmp(name, "NUL", 3))
        return W32_NC_NUL;
    if (!w32_strnicmp(name, "CON", 3))
        return W32_NC_CONSOLE;
    return W32_NC_PORT;                                /* PRN AUX COMn LPTn: no such hardware */
}

static size_t count_comps(const char *norm) /* "x:\a\b" -> 2 */
{
    if (!norm[3])
        return 0;
    size_t c = 1;
    for (const char *p = norm + 3; *p; p++)
        c += *p == '\\';
    return c;
}

/* does norm start with the mount prefix, component-wise? */
static int prefix_match(const struct mount *m, const char *norm)
{
    size_t pl = strlen(m->prefix);
    if (w32_strnicmp(norm, m->prefix, pl))
        return 0;
    return norm[pl] == 0 || norm[pl] == '\\' || m->prefix[pl - 1] == '\\';
}

static void ensure_defaults(void)
{
    if (!g_nmounts)
        fnvnx_path_reset(NULL);
}

extern "C" int fnvnx_path_mount(const char *win_prefix, const char *host_dir)
{
    char norm[W32_PATH_MAX];
    if (normalize(win_prefix, norm, sizeof norm) || strlen(host_dir) >= W32_PATH_MAX)
        return -1;
    os_lock(&g_path_lock);
    int i;
    for (i = 0; i < g_nmounts; i++)
        if (!w32_stricmp(g_mounts[i].prefix, norm))
            break;
    if (i == g_nmounts) {
        if (g_nmounts == MAX_MOUNTS) {
            os_unlock(&g_path_lock);
            return -1;
        }
        g_nmounts++;
    }
    strcpy(g_mounts[i].prefix, norm);
    g_mounts[i].ncomp = count_comps(norm);
    strcpy(g_mounts[i].host, host_dir);
    size_t hl = strlen(g_mounts[i].host);
    while (hl > 1 && g_mounts[i].host[hl - 1] == '/')
        g_mounts[i].host[--hl] = 0;
    os_unlock(&g_path_lock);
    return 0;
}

static void mkdirs(const char *host)
{
    char b[W32_PATH_MAX + 64];
    snprintf(b, sizeof b, "%s", host);
    for (char *p = b + 1; *p; p++)
        if (*p == '/' && p[-1] != ':') {
            *p = 0;
            os_mkdir(b);
            *p = '/';
        }
    os_mkdir(b);
}

extern "C" void fnvnx_path_reset(const char *root)
{
    os_lock(&g_path_lock);
    g_nmounts = 0;
    snprintf(g_root, sizeof g_root, "%s", root ? root : os_default_root());
    strcpy(g_cwd, W32_GAME_DIR);
    os_unlock(&g_path_lock);
    char h[W32_PATH_MAX + 64];
    mkdirs(g_root);
    fnvnx_path_mount(W32_GAME_DIR, g_root);   /* the install is the root itself: <root>/FalloutNV.exe, <root>/Data */
    snprintf(h, sizeof h, "%s/user", g_root);
    fnvnx_path_mount(W32_USER_DIR, h);
    /* the profile folders Windows creates for a user (Documents, AppData/Local, Temp) exist; a game subdirectory of AppData/Local does not
     (no static site creates it; the game creates what it needs). Documents/My Games/FalloutNV holds the declared FalloutPrefs.ini. */
    snprintf(h, sizeof h, "%s/user/Documents/My Games/FalloutNV", g_root);
    mkdirs(h);
    snprintf(h, sizeof h, "%s/user/AppData/Local/Temp", g_root);   /* the TEMP/TMP directory of the environment */
    mkdirs(h);
    snprintf(h, sizeof h, "%s/drive_c", g_root); /* C:\ itself exists */
    mkdirs(h);
}

void w32_virtual_cwd(char *out, size_t n)
{
    os_lock(&g_path_lock);
    snprintf(out, n, "%s", g_cwd);
    os_unlock(&g_path_lock);
}

DWORD w32_full_path(const char *win, char *out, size_t n) { return normalize(win, out, n); }

static int ci_lookup(const char *dir, const char *name, size_t len, char *found, size_t fn)
{
    os_dir *d = os_dir_open(dir);
    if (!d)
        return 0;
    const char *e;
    int ok = 0;
    while ((e = os_dir_next(d)))
        if (strlen(e) == len && !w32_strnicmp(e, name, len)) {
            snprintf(found, fn, "%s", e);
            ok = 1;
            break;
        }
    os_dir_close(d);
    return ok;
}

DWORD w32_map_path(const char *win, char *host, size_t n)
{
    ensure_defaults();
    char norm[W32_PATH_MAX];
    DWORD e = normalize(win, norm, sizeof norm);
    if (e)
        return e;
    /* longest matching mount */
    const struct mount *best = NULL;
    os_lock(&g_path_lock);
    for (int i = 0; i < g_nmounts; i++)
        if (prefix_match(&g_mounts[i], norm) && (!best || g_mounts[i].ncomp > best->ncomp))
            best = &g_mounts[i];
    char base[W32_PATH_MAX + 16];
    const char *tail;
    if (best) {
        snprintf(base, sizeof base, "%s", best->host);
        tail = norm + strlen(best->prefix);
    } else {
        snprintf(base, sizeof base, "%s/drive_%c", g_root, norm[0]);
        tail = norm + 2;
    }
    os_unlock(&g_path_lock);
    size_t o = strlen(base);
    if (o >= n)
        return 206;
    memcpy(host, base, o + 1);
    int resolving = !os_fs_case_insensitive();
    while (*tail) {
        while (*tail == '\\')
            tail++;
        if (!*tail)
            break;
        const char *s = tail;
        while (*tail && *tail != '\\')
            tail++;
        size_t len = (size_t)(tail - s);
        char comp[W32_PATH_MAX];
        memcpy(comp, s, len);
        comp[len] = 0;
        if (resolving) {
            char probe[W32_PATH_MAX * 2];
            os_stat_t st;
            snprintf(probe, sizeof probe, "%s/%s", host, comp);
            if (os_stat(probe, &st) != 0) {
                char found[W32_PATH_MAX];
                if (ci_lookup(host, comp, len, found, sizeof found))
                    snprintf(comp, sizeof comp, "%s", found);
                else
                    resolving = 0; /* the rest does not exist: keep the given spelling */
            }
        }
        size_t cl = strlen(comp);
        if (o + cl + 2 > n)
            return 206;
        host[o++] = '/';
        memcpy(host + o, comp, cl + 1);
        o += cl;
    }
    return 0;
}

extern "C" DWORD fnvnx_path_map(const char *win, char *host, size_t n) { return w32_map_path(win, host, n); }

extern "C" DWORD GetCurrentDirectoryA(DWORD n, LPSTR buf)
{
    char cwd[W32_PATH_MAX];
    w32_virtual_cwd(cwd, sizeof cwd);
    DWORD len = (DWORD)strlen(cwd);
    if (!buf || W32_DW(n) <= len)
        return len + 1;
    memcpy(buf, cwd, len + 1);
    return len;
}

extern "C" BOOL SetCurrentDirectoryA(LPCSTR dir)
{
    char norm[W32_PATH_MAX], host[W32_PATH_MAX];
    DWORD e = normalize(dir, norm, sizeof norm);
    if (!e)
        e = w32_map_path(norm, host, sizeof host);
    os_stat_t st;
    if (!e && (os_stat(host, &st) || !st.is_dir))
        e = ERROR_PATH_NOT_FOUND;
    if (e) {
        SetLastError(e == ERROR_FILE_NOT_FOUND ? ERROR_PATH_NOT_FOUND : e);
        return FALSE;
    }
    norm[0] = (char)(norm[0] & ~32); /* "C:\..." like Windows reports it */
    os_lock(&g_path_lock);
    strcpy(g_cwd, norm);
    os_unlock(&g_path_lock);
    return TRUE;
}

extern "C" DWORD GetFullPathNameA(LPCSTR in, DWORD n, LPSTR buf, LPSTR *file_part)
{
    char c[W32_PATH_MAX * 2];
    int kind, rl;
    if (!in || !*in) {
        SetLastError(ERROR_SUCCESS);
        return 0;       /* an empty name has no full path */
    }
    DWORD e = canon(in, c, sizeof c, &kind, &rl);
    if (e) {
        SetLastError(e);
        return 0;
    }
    DWORD len = (DWORD)strlen(c);
    if (!buf || W32_DW(n) <= len)
        return len + 1;                                /* too small: the size needed, with the NUL */
    memcpy(buf, c, len + 1);
    if (kind == CK_DRIVE)
        buf[0] = (char)(buf[0] & ~32);                 /* the drive letter is returned in upper case */
    if (file_part) {
        /* the text after the last separator, unless the path ends in one, is a device, or is a root */
        char *s = strrchr(buf, '\\');
        int seps = 0;
        for (char *q = buf + 2; *q; q++)
            seps += *q == '\\';
        int unc_root = kind == CK_UNC && seps < 2;     /* "\\server" and "\\server\share" are roots */
        *file_part = kind == CK_DEVICE || unc_root || !s || !s[1] || (int)(s + 1 - buf) < rl ? NULL : s + 1;
    }
    return len;
}
