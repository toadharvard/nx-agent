/* path.cpp - Windows paths -> host paths (scheme: docs/RUNTIME.md "Paths").
 *
 * The game sees a small virtual Windows file system:
 *     C:\FalloutNV\                       the install (cwd at start; GetModuleFileName = C:\FalloutNV\FalloutNV.exe)
 *     C:\Users\Player\                    profile: Documents\My Games\FalloutNV\ (Fallout.ini, Saves\),
 *                                         AppData\Local\FalloutNV\ (plugins.txt)
 *     any other X:\...                    <root>/drive_x/... (sandboxed, nothing escapes the root)
 * mounted on the host as
 *     <root>/game  <root>/user  <root>/drive_<x>
 * with <root> = sdmc:/fnv-nx on the Switch and $FNVNX_ROOT (default ./fnvnx-root) on the host.
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
static const char GAME_DIR[] = "C:\\FalloutNV";
static const char EXE_PATH[] = "C:\\FalloutNV\\FalloutNV.exe";
static const char USER_DIR[] = "C:\\Users\\Player";

const char *w32_exe_path(void) { return EXE_PATH; }
const char *w32_user_dir(void) { return USER_DIR; }

static int is_sep(char c) { return c == '\\' || c == '/'; }

/* normalise into "x:\comp\comp" (drive lower-case, no trailing separator; root is "x:\") */
static DWORD normalize(const char *in, char *out, size_t n)
{
    char buf[W32_PATH_MAX * 2];
    if (!in || !*in)
        return ERROR_PATH_NOT_FOUND;
    if (is_sep(in[0]) && is_sep(in[1]) && (in[2] == '?' || in[2] == '.') && is_sep(in[3]))
        in += 4;                                       /* \\?\C:\... and \\.\ */
    else if (is_sep(in[0]) && is_sep(in[1]))
        return ERROR_PATH_NOT_FOUND;                   /* UNC shares do not exist here */
    char cwd[W32_PATH_MAX];
    w32_virtual_cwd(cwd, sizeof cwd);
    char drive;
    const char *rest;
    if (((in[0] | 32) >= 'a' && (in[0] | 32) <= 'z') && in[1] == ':') {
        drive = (char)(in[0] | 32);
        rest = in + 2;
        if (!is_sep(*rest)) {                          /* "C:foo": relative to the cwd of that drive */
            if (drive == (cwd[0] | 32))
                snprintf(buf, sizeof buf, "%s\\%s", cwd + 2, rest);
            else
                snprintf(buf, sizeof buf, "\\%s", rest);
            rest = buf;
        }
    } else if (is_sep(in[0])) {
        drive = (char)(cwd[0] | 32);
        rest = in;
    } else {
        drive = (char)(cwd[0] | 32);
        snprintf(buf, sizeof buf, "%s\\%s", cwd + 2, in);
        rest = buf;
    }
    if (strlen(rest) >= sizeof buf - 1)
        return 206; /* ERROR_FILENAME_EXCED_RANGE */
    /* split and resolve */
    const char *comps[256];
    size_t lens[256];
    size_t nc = 0;
    const char *p = rest;
    while (*p) {
        while (is_sep(*p))
            p++;
        if (!*p)
            break;
        const char *s = p;
        while (*p && !is_sep(*p))
            p++;
        size_t len = (size_t)(p - s);
        if (len == 1 && s[0] == '.')
            continue;
        if (len == 2 && s[0] == '.' && s[1] == '.') {
            if (nc)
                nc--;
            continue;
        }
        while (len && (s[len - 1] == '.' || s[len - 1] == ' '))
            len--;                                     /* Win32 drops trailing dots and spaces */
        if (!len)
            continue;
        if (nc == 256)
            return 206;
        comps[nc] = s;
        lens[nc] = len;
        nc++;
    }
    size_t o = 0;
    if (n < 4)
        return 206;
    out[o++] = drive;
    out[o++] = ':';
    out[o++] = '\\';
    for (size_t i = 0; i < nc; i++) {
        if (o + lens[i] + 2 > n)
            return 206;
        if (i)
            out[o++] = '\\';
        memcpy(out + o, comps[i], lens[i]);
        o += lens[i];
    }
    out[o] = 0;
    return 0;
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
    strcpy(g_cwd, GAME_DIR);
    os_unlock(&g_path_lock);
    char h[W32_PATH_MAX + 64];
    snprintf(h, sizeof h, "%s/game", g_root);
    mkdirs(h);
    fnvnx_path_mount(GAME_DIR, h);
    snprintf(h, sizeof h, "%s/user", g_root);
    fnvnx_path_mount(USER_DIR, h);
    /* the profile folders the game writes to must exist */
    snprintf(h, sizeof h, "%s/user/Documents/My Games/FalloutNV", g_root);
    mkdirs(h);
    snprintf(h, sizeof h, "%s/user/AppData/Local/FalloutNV", g_root);
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
    char norm[W32_PATH_MAX];
    DWORD e = normalize(in, norm, sizeof norm);
    if (e) {
        SetLastError(e);
        return 0;
    }
    norm[0] = (char)(norm[0] & ~32);
    /* keep a trailing separator of the input */
    size_t l = strlen(in);
    if (l && is_sep(in[l - 1]) && strlen(norm) > 3)
        strcat(norm, "\\");
    DWORD len = (DWORD)strlen(norm);
    if (!buf || W32_DW(n) <= len)
        return len + 1;
    memcpy(buf, norm, len + 1);
    if (file_part) {
        char *s = strrchr(buf, '\\');
        *file_part = s && s[1] ? s + 1 : NULL;
    }
    return len;
}
