/* registry_seed.cpp - the registry's first-boot content: the keys and values the GOG installer of this game version
 * writes (ENV_TRUTH F3). boot.cpp calls fnvnx_registry_first_run() before WinMain; the hive file is made only when
 * it does not exist, so what the game or the player changed is never overwritten.
 *
 * The seed is C:\FalloutNV\registry_seed.tsv, shipped beside the game. It is generated from the installer's script
 * (platform/manifest/gen_registry_seed.py) and is installer-derived data: it lives in the private artifacts repository,
 * never in this one. Format, text, LF or CRLF lines:
 *     FNVREGSEED 1                                  first line: format version
 *     # comment                                      comment lines and blank lines are skipped
 *     <root> TAB <key> TAB <name> TAB <type> TAB <data>     one row each, exactly five fields
 *   root   HKLM HKCU HKCR HKU
 *   key    backslash path below the root (not empty), the 32-bit view the exe opens, in the installer's own spelling.
 *          Wine's server/registry.c (open_key, create_key) redirects a 32-bit process's HKLM\Software to Wow6432Node;
 *          this hive holds only that one view, so no redirect exists here.
 *   name   value name, "" is the default value
 *   type   KEY (create the key only; name and data empty), REG_SZ, REG_EXPAND_SZ, REG_DWORD, REG_QWORD, REG_BINARY,
 *          REG_MULTI_SZ
 *   data   REG_SZ/REG_EXPAND_SZ: the text, with the installer constant {app} = the game directory with its trailing
 *          backslash ("C:\FalloutNV\"); "{{" is a literal "{"; any other "{" is a loud failure.
 *          REG_DWORD/REG_QWORD: decimal or 0x hex. REG_BINARY/REG_MULTI_SZ: the raw bytes as hex pairs.
 * The whole file is parsed and checked before the first key is made, so a bad seed leaves no hive behind. Any failure
 * (no file, wrong version, a bad row, a hive that cannot be written) returns -1 with the reason. */
#include "w32.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string>
#include <vector>

extern "C" int fnvnx_registry_hive_present(void);

#define SEED_FILE "C:\\FalloutNV\\registry_seed.tsv"

namespace {
struct Row { unsigned root; std::string key, name; DWORD type; std::string data; bool keyonly; };

bool fail(char *why, size_t n, int line, const char *msg, const std::string &what)
{
    if (line) snprintf(why, n, "%s line %d: %s %s", SEED_FILE, line, msg, what.c_str());
    else snprintf(why, n, "%s: %s %s", SEED_FILE, msg, what.c_str());
    return false;
}

bool hexbytes(const std::string &s, std::string &out)
{
    if (s.size() % 2) return false;
    for (size_t i = 0; i < s.size(); i += 2) {
        char b[3] = { s[i], s[i + 1], 0 };
        if (!isxdigit((unsigned char)b[0]) || !isxdigit((unsigned char)b[1])) return false;
        out.push_back((char)strtoul(b, NULL, 16));
    }
    return true;
}

bool number(const std::string &s, unsigned long long max, unsigned long long &v)
{
    if (s.empty() || isspace((unsigned char)s[0]) || s[0] == '-' || s[0] == '+') return false;
    char *e = NULL;
    errno = 0;
    v = strtoull(s.c_str(), &e, 0);
    return *e == 0 && errno == 0 && v <= max;
}

bool expand(const std::string &in, std::string &out)
{
    static const char app[] = "{app}";
    for (size_t i = 0; i < in.size(); i++) {
        if (in[i] != '{') { out.push_back(in[i]); continue; }
        if (in.compare(i, 2, "{{") == 0) { out.push_back('{'); i++; }
        else if (in.compare(i, sizeof app - 1, app) == 0) { out += W32_GAME_DIR; out.push_back('\\'); i += sizeof app - 2; }
        else return false;
    }
    return true;
}

bool parse_row(const std::vector<std::string> &f, Row &r, std::string &err)
{
    static const char *const roots[] = { "HKLM", "HKCU", "HKCR", "HKU" };
    static const unsigned rootid[] = { 0x80000002u, 0x80000001u, 0x80000000u, 0x80000003u };
    r.root = 0;
    for (int i = 0; i < 4; i++) if (f[0] == roots[i]) r.root = rootid[i];
    if (!r.root) { err = "unknown root " + f[0]; return false; }
    r.key = f[1]; r.name = f[2];
    if (r.key.empty() || r.key[0] == '\\' || r.key[r.key.size() - 1] == '\\') { err = "bad key path \"" + r.key + "\""; return false; }
    const std::string &t = f[3], &d = f[4];
    r.keyonly = false;
    unsigned long long v;
    if (t == "KEY") {
        r.keyonly = true; r.type = 0;
        if (!r.name.empty() || !d.empty()) { err = "KEY row with a name or data"; return false; }
        return true;
    }
    if (t == "REG_SZ" || t == "REG_EXPAND_SZ") {
        r.type = t == "REG_SZ" ? 1 : 2;
        if (!expand(d, r.data)) { err = "bad installer constant in \"" + d + "\" (only {app} and {{ are known)"; return false; }
        r.data.push_back(0);
        return true;
    }
    if (t == "REG_DWORD" || t == "REG_QWORD") {
        unsigned n = t == "REG_DWORD" ? 4 : 8;
        r.type = n == 4 ? 4 : 11;
        if (!number(d, n == 4 ? 0xFFFFFFFFull : ~0ull, v)) { err = "bad number \"" + d + "\""; return false; }
        for (unsigned i = 0; i < n; i++) r.data.push_back((char)(v >> (8 * i)));
        return true;
    }
    if (t == "REG_BINARY" || t == "REG_MULTI_SZ") {
        r.type = t == "REG_BINARY" ? 3 : 7;
        if (!hexbytes(d, r.data)) { err = "bad hex data \"" + d + "\""; return false; }
        return true;
    }
    err = "unknown type " + t;
    return false;
}

bool read_seed(std::string &s, char *why, size_t n)
{
    HANDLE h = CreateFileA(SEED_FILE, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return fail(why, n, 0, "the registry seed is missing, Win32 error", std::to_string((unsigned)GetLastError()));
    char buf[8192];
    DWORD got;
    while (ReadFile(h, buf, sizeof buf, &got, NULL) && got) s.append(buf, got);
    CloseHandle(h);
    return true;
}

bool parse(const std::string &s, std::vector<Row> &rows, char *why, size_t n)
{
    size_t p = 0;
    int line = 0;
    bool head = false;
    while (p < s.size()) {
        size_t e = s.find('\n', p);
        std::string l = s.substr(p, e == std::string::npos ? std::string::npos : e - p);
        p = e == std::string::npos ? s.size() : e + 1;
        line++;
        if (!l.empty() && l[l.size() - 1] == '\r') l.erase(l.size() - 1);
        if (l.empty() || l[0] == '#') continue;
        if (!head) {
            if (l != "FNVREGSEED 1") return fail(why, n, line, "not a registry seed of format 1, first line is", "\"" + l + "\"");
            head = true;
            continue;
        }
        std::vector<std::string> f;
        for (size_t a = 0;;) {
            size_t t = l.find('\t', a);
            f.push_back(l.substr(a, t == std::string::npos ? std::string::npos : t - a));
            if (t == std::string::npos) break;
            a = t + 1;
        }
        std::string err;
        Row r;
        if (f.size() != 5) return fail(why, n, line, "expected 5 tab-separated fields, got", std::to_string(f.size()));
        if (!parse_row(f, r, err)) return fail(why, n, line, "bad row:", err);
        rows.push_back(r);
    }
    if (!head) return fail(why, n, 0, "the file is empty,", "no FNVREGSEED header");
    if (rows.empty()) return fail(why, n, 0, "the seed has no rows,", "an installer that writes no registry data is not this game's");
    return true;
}
} // namespace

/* Makes the hive from the seed when the hive file does not exist. Returns the number of rows applied, 0 when the hive
 * already exists (nothing is touched), -1 with the reason in why. */
extern "C" int fnvnx_registry_first_run(char *why, size_t why_size)
{
    why[0] = 0;
    if (fnvnx_registry_hive_present()) return 0;
    std::string s;
    std::vector<Row> rows;
    if (!read_seed(s, why, why_size) || !parse(s, rows, why, why_size)) {
        W32Log().s("registry first run: ").s(why).s("\n").emit();
        return -1;
    }
    /* every row is applied with its handles open; the closes then write the finished tree in one atomic save */
    std::vector<HKEY> open;
    LSTATUS st = ERROR_SUCCESS;
    std::string at;
    for (size_t i = 0; i < rows.size() && st == ERROR_SUCCESS; i++) {
        const Row &r = rows[i];
        HKEY k = NULL;
        st = RegCreateKeyExA((HKEY)(uintptr_t)r.root, r.key.c_str(), 0, NULL, 0, 0xF003F, NULL, &k, NULL);
        if (st == ERROR_SUCCESS) {
            open.push_back(k);
            if (!r.keyonly) st = RegSetValueExA(k, r.name.c_str(), 0, r.type, (const BYTE *)r.data.data(), (DWORD)r.data.size());
        }
        if (st != ERROR_SUCCESS) at = "row " + std::to_string(i + 1) + " (" + r.key + ")";
    }
    for (size_t i = 0; i < open.size(); i++) {
        LSTATUS c = RegCloseKey(open[i]);
        if (st == ERROR_SUCCESS && c != ERROR_SUCCESS) { st = c; at = "writing the hive"; }
    }
    for (unsigned r = 0x80000000u; r <= 0x80000003u; r++) {   /* a key made straight under a root dirties the root */
        LSTATUS c = RegCloseKey((HKEY)(uintptr_t)r);
        if (st == ERROR_SUCCESS && c != ERROR_SUCCESS) { st = c; at = "writing the hive"; }
    }
    if (st == ERROR_SUCCESS && !fnvnx_registry_hive_present()) { st = 1016L; at = "no hive file after the seed"; }
    if (st != ERROR_SUCCESS) {
        snprintf(why, why_size, "%s: registry error %ld at %s", SEED_FILE, (long)st, at.c_str());
        W32Log().s("registry first run: ").s(why).s("\n").emit();
        return -1;
    }
    W32Log().s("registry first run: ").u((unsigned)rows.size()).s(" rows from the installer seed\n").emit();
    return (int)rows.size();
}
