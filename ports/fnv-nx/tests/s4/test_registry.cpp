/* test_registry.cpp - advapi32 registry: tree, access, buffer protocol, volatility, persistence. */
#include "s4_test.h"

#include <stdlib.h>
#include <string>

extern "C" void fnvnx_registry_set_path(const char *host_path);
extern "C" void fnvnx_registry_reset_for_test(void);

#ifndef ERROR_INVALID_PARAMETER
#define ERROR_INVALID_PARAMETER 87L
#endif
#ifndef ERROR_BADKEY
#define ERROR_BADKEY 1010L
#endif
#ifndef ERROR_CHILD_MUST_BE_VOLATILE
#define ERROR_CHILD_MUST_BE_VOLATILE 1021L
#endif
#ifndef ERROR_BADDB
#define ERROR_BADDB 1009L
#endif
#define T_REG_MULTI_SZ 7
#define T_REG_QWORD 11
#define T_KEY_QUERY 0x1
#define T_KEY_SET 0x2
#define T_KEY_CREATE 0x4
#define T_KEY_READ 0x20019
#define T_KEY_WRITE 0x20006
#define T_KEY_ALL 0xF003F
#define T_MAXIMUM_ALLOWED 0x02000000

static void reg_fresh(const char *name)
{
    static char path[1200];
    snprintf(path, sizeof path, "%s/%s", s4_root(), name);
    remove(path);
    fnvnx_registry_set_path(path);
    fnvnx_registry_reset_for_test();
}

static HKEY mk(HKEY p, const char *sub, REGSAM s = T_KEY_ALL, DWORD opt = 0, DWORD *disp = NULL)
{
    HKEY k = NULL;
    LSTATUS r = RegCreateKeyExA(p, sub, 0, NULL, opt, s, NULL, &k, disp);
    CHECK_EQ(r, ERROR_SUCCESS);
    return k;
}

TEST(registry_tree_and_handles)
{
    reg_fresh("t1.hiv");
    DWORD disp = 0;
    HKEY a = mk(HKEY_CURRENT_USER, "Software\\Foo\\Bar", T_KEY_ALL, 0, &disp);
    CHECK_EQ(disp, REG_CREATED_NEW_KEY);
    HKEY b = mk(HKEY_CURRENT_USER, "software\\FOO\\bar", T_KEY_ALL, 0, &disp); /* case-insensitive */
    CHECK_EQ(disp, REG_OPENED_EXISTING_KEY);
    CHECK(a != b);
    HKEY mid = NULL;
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "SOFTWARE\\foo", 0, T_KEY_READ, &mid), ERROR_SUCCESS);
    HKEY self = NULL, self2 = NULL;
    CHECK_EQ(RegOpenKeyExA(mid, "Bar", 0, T_KEY_READ, &self), ERROR_SUCCESS); /* relative */
    CHECK_EQ(RegOpenKeyExA(mid, NULL, 0, T_KEY_READ, &self2), ERROR_SUCCESS);
    CHECK(self2 != NULL && self2 != mid);
    HKEY s3 = NULL;
    CHECK_EQ(RegOpenKeyExA(mid, "", 0, T_KEY_READ, &s3), ERROR_SUCCESS);
    CHECK(s3 != self2 && s3 != mid);
    HKEY x = NULL;
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "Software\\Missing", 0, T_KEY_READ, &x), ERROR_FILE_NOT_FOUND);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "Software\\Foo\\Bar\\Deeper", 0, T_KEY_READ, &x), ERROR_FILE_NOT_FOUND);
    CHECK_EQ(RegOpenKeyExA((HKEY)(uintptr_t)0x1234, "x", 0, T_KEY_READ, &x), ERROR_INVALID_HANDLE);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "Software", 0, T_KEY_READ, NULL), ERROR_INVALID_PARAMETER);
    CHECK_EQ(RegOpenKeyExA(HKEY_CLASSES_ROOT, NULL, 0, T_KEY_READ, &x), ERROR_SUCCESS);
    RegCloseKey(x);
    CHECK_EQ(RegOpenKeyExA(HKEY_USERS, "", 0, T_KEY_READ, &x), ERROR_SUCCESS);
    RegCloseKey(x);
    /* roots are separate trees */
    CHECK_EQ(RegOpenKeyExA(HKEY_LOCAL_MACHINE, "Software\\Foo", 0, T_KEY_READ, &x), ERROR_FILE_NOT_FOUND);
    /* a value set through one handle is visible through another */
    DWORD v = 99, out = 0, len = sizeof out;
    CHECK_EQ(RegSetValueExA(a, "n", 0, REG_DWORD, (const BYTE *)&v, sizeof v), ERROR_SUCCESS);
    CHECK_EQ(RegQueryValueExA(self, "N", NULL, NULL, (LPBYTE)&out, &len), ERROR_SUCCESS);
    CHECK_EQ(out, 99);
    /* closing */
    CHECK_EQ(RegCloseKey(a), ERROR_SUCCESS);
    CHECK_EQ(RegCloseKey(a), ERROR_INVALID_HANDLE); /* double close */
    CHECK_EQ(RegCloseKey((HKEY)(uintptr_t)0x4321), ERROR_INVALID_HANDLE);
    CHECK_EQ(RegCloseKey(NULL), ERROR_INVALID_HANDLE);
    CHECK_EQ(RegCloseKey(HKEY_LOCAL_MACHINE), ERROR_SUCCESS);
    CHECK_EQ(RegCloseKey(HKEY_LOCAL_MACHINE), ERROR_SUCCESS);
    CHECK_EQ(RegQueryValueExA(a, "n", NULL, NULL, NULL, NULL), ERROR_INVALID_HANDLE);
    CHECK_EQ(RegSetValueExA(a, "n", 0, REG_DWORD, (const BYTE *)&v, sizeof v), ERROR_INVALID_HANDLE);
    RegCloseKey(b); RegCloseKey(mid); RegCloseKey(self); RegCloseKey(self2); RegCloseKey(s3);
}

TEST(registry_create_params)
{
    reg_fresh("t2.hiv");
    HKEY k = NULL;
    DWORD disp = 0;
    CHECK_EQ(RegCreateKeyExA(HKEY_CURRENT_USER, NULL, 0, NULL, 0, T_KEY_ALL, NULL, &k, &disp), ERROR_BADKEY);
    CHECK_EQ(RegCreateKeyExA((HKEY)(uintptr_t)0x77, "a", 0, NULL, 0, T_KEY_ALL, NULL, &k, &disp), ERROR_INVALID_HANDLE);
    CHECK_EQ(RegCreateKeyExA(HKEY_CURRENT_USER, "a", 0, NULL, 0, T_KEY_ALL, NULL, NULL, &disp), ERROR_INVALID_PARAMETER);
    CHECK_EQ(RegCreateKeyExA(HKEY_CURRENT_USER, "a", 0, NULL, 0x10, T_KEY_ALL, NULL, &k, &disp), ERROR_INVALID_PARAMETER);
    CHECK_EQ(RegCreateKeyExA(HKEY_CURRENT_USER, "a", 0, NULL, 0x100, T_KEY_ALL, NULL, &k, &disp), ERROR_INVALID_PARAMETER);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "a", 0, T_KEY_READ, &k), ERROR_FILE_NOT_FOUND); /* nothing was created */
    CHECK_EQ(RegCreateKeyExA(HKEY_CURRENT_USER, "a\\b", 0, NULL, 0, T_KEY_ALL, NULL, &k, NULL), ERROR_SUCCESS); /* NULL disp ok */
    RegCloseKey(k);
    /* no backup/restore privilege: denied, nothing created */
    k = NULL;
    CHECK_EQ(RegCreateKeyExA(HKEY_CURRENT_USER, "bk\\x", 0, NULL, 4, T_KEY_ALL, NULL, &k, &disp), ERROR_ACCESS_DENIED);
    CHECK_EQ(RegCreateKeyExA(HKEY_CURRENT_USER, "a\\b", 0, NULL, 4, T_KEY_ALL, NULL, &k, &disp), ERROR_ACCESS_DENIED);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "bk", 0, T_KEY_READ, &k), ERROR_FILE_NOT_FOUND);
    CHECK_EQ(RegCreateKeyExA(HKEY_CURRENT_USER, "a\\c", 0, NULL, 8 | 2, T_KEY_ALL, NULL, &k, NULL), ERROR_SUCCESS);
    RegCloseKey(k);
    /* intermediate keys were created */
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "A", 0, T_KEY_READ, &k), ERROR_SUCCESS);
    RegCloseKey(k);
    /* KEY_CREATE_SUB_KEY is needed on the parent to create, not to open an existing key */
    HKEY p = mk(HKEY_CURRENT_USER, "p", T_KEY_READ | T_KEY_SET);
    CHECK_EQ(RegCreateKeyExA(p, "child", 0, NULL, 0, T_KEY_ALL, NULL, &k, &disp), ERROR_ACCESS_DENIED);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "p\\child", 0, T_KEY_READ, &k), ERROR_FILE_NOT_FOUND);
    RegCloseKey(p);
    p = mk(HKEY_CURRENT_USER, "p", T_KEY_CREATE);
    CHECK_EQ(RegCreateKeyExA(p, "child", 0, NULL, 0, T_KEY_ALL, NULL, &k, &disp), ERROR_SUCCESS);
    CHECK_EQ(disp, REG_CREATED_NEW_KEY);
    RegCloseKey(k);
    RegCloseKey(p);
    p = mk(HKEY_CURRENT_USER, "p", T_KEY_READ);
    CHECK_EQ(RegCreateKeyExA(p, "child", 0, NULL, 0, 0, NULL, &k, &disp), ERROR_SUCCESS);
    CHECK_EQ(disp, REG_OPENED_EXISTING_KEY);
    RegCloseKey(k);
    RegCloseKey(p);
}

TEST(registry_access)
{
    reg_fresh("t3.hiv");
    HKEY w = mk(HKEY_CURRENT_USER, "acc");
    DWORD v = 5;
    CHECK_EQ(RegSetValueExA(w, "v", 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_SUCCESS);
    RegCloseKey(w);
    HKEY k;
    DWORD len = 4, out = 0;
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "acc", 0, T_KEY_SET, &k), ERROR_SUCCESS);
    CHECK_EQ(RegQueryValueExA(k, "v", NULL, NULL, (LPBYTE)&out, &len), ERROR_ACCESS_DENIED);
    CHECK_EQ(RegSetValueExA(k, "v", 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_SUCCESS);
    RegCloseKey(k);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "acc", 0, T_KEY_READ, &k), ERROR_SUCCESS);
    CHECK_EQ(RegSetValueExA(k, "v", 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_ACCESS_DENIED);
    len = 4;
    CHECK_EQ(RegQueryValueExA(k, "v", NULL, NULL, (LPBYTE)&out, &len), ERROR_SUCCESS);
    RegCloseKey(k);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "acc", 0, 0, &k), ERROR_SUCCESS);
    CHECK_EQ(RegQueryValueExA(k, "v", NULL, NULL, NULL, NULL), ERROR_ACCESS_DENIED);
    CHECK_EQ(RegSetValueExA(k, "v", 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_ACCESS_DENIED);
    RegCloseKey(k);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "acc", 0, T_KEY_WRITE, &k), ERROR_SUCCESS);
    CHECK_EQ(RegSetValueExA(k, "v", 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_SUCCESS);
    CHECK_EQ(RegQueryValueExA(k, "v", NULL, NULL, NULL, NULL), ERROR_ACCESS_DENIED);
    RegCloseKey(k);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "acc", 0, T_MAXIMUM_ALLOWED, &k), ERROR_SUCCESS);
    CHECK_EQ(RegQueryValueExA(k, "v", NULL, NULL, NULL, NULL), ERROR_SUCCESS);
    CHECK_EQ(RegSetValueExA(k, "v", 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_SUCCESS);
    RegCloseKey(k);
    /* predefined roots have all access */
    CHECK_EQ(RegSetValueExA(HKEY_CURRENT_USER, "rootval", 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_SUCCESS);
    CHECK_EQ(RegQueryValueExA(HKEY_CURRENT_USER, "rootval", NULL, NULL, NULL, NULL), ERROR_SUCCESS);
}

TEST(registry_query_protocol)
{
    reg_fresh("t4.hiv");
    HKEY k = mk(HKEY_LOCAL_MACHINE, "Q");
    const char s[] = "hello";
    CHECK_EQ(RegSetValueExA(k, "str", 0, REG_SZ, (const BYTE *)s, sizeof s), ERROR_SUCCESS);
    DWORD type = 99, len = 0;
    CHECK_EQ(RegQueryValueExA(k, "STR", NULL, &type, NULL, &len), ERROR_SUCCESS);
    CHECK_EQ(len, 6);
    CHECK_EQ(type, REG_SZ);
    char buf[16];
    memset(buf, 'x', sizeof buf);
    len = 3;
    CHECK_EQ(RegQueryValueExA(k, "str", NULL, &type, (LPBYTE)buf, &len), ERROR_MORE_DATA);
    CHECK_EQ(len, 6);
    CHECK_EQ(buf[0], 'x');
    len = 6;
    CHECK_EQ(RegQueryValueExA(k, "str", NULL, NULL, (LPBYTE)buf, &len), ERROR_SUCCESS);
    CHECK_STR(buf, "hello");
    memset(buf, 'x', sizeof buf);
    len = 16;
    CHECK_EQ(RegQueryValueExA(k, "str", NULL, NULL, (LPBYTE)buf, &len), ERROR_SUCCESS);
    CHECK_EQ(len, 6);
    CHECK_EQ(buf[6], 'x'); /* nothing added after the data */
    CHECK_EQ(RegQueryValueExA(k, "str", NULL, &type, (LPBYTE)buf, NULL), ERROR_INVALID_PARAMETER);
    CHECK_EQ(RegQueryValueExA(k, "str", NULL, &type, NULL, NULL), ERROR_SUCCESS); /* type only */
    CHECK_EQ(type, REG_SZ);
    len = 16;
    CHECK_EQ(RegQueryValueExA(k, "nope", NULL, &type, (LPBYTE)buf, &len), ERROR_FILE_NOT_FOUND);
    /* unterminated REG_SZ comes back unterminated */
    const char u[] = { 'a', 'b', 'c' };
    CHECK_EQ(RegSetValueExA(k, "u", 0, REG_SZ, (const BYTE *)u, 3), ERROR_SUCCESS);
    memset(buf, 'x', sizeof buf);
    len = 16;
    CHECK_EQ(RegQueryValueExA(k, "u", NULL, NULL, (LPBYTE)buf, &len), ERROR_SUCCESS);
    CHECK_EQ(len, 3);
    CHECK_EQ(buf[3], 'x');
    /* empty value */
    CHECK_EQ(RegSetValueExA(k, "empty", 0, REG_NONE, NULL, 0), ERROR_SUCCESS);
    len = 5;
    CHECK_EQ(RegQueryValueExA(k, "empty", NULL, &type, (LPBYTE)buf, &len), ERROR_SUCCESS);
    CHECK_EQ(len, 0);
    CHECK_EQ(type, REG_NONE);
    RegCloseKey(k);
}

TEST(registry_set_semantics)
{
    reg_fresh("t5.hiv");
    HKEY k = mk(HKEY_CURRENT_USER, "S");
    BYTE d[4] = { 1, 2, 3, 4 };
    CHECK_EQ(RegSetValueExA(k, "x", 0, REG_BINARY, NULL, 4), ERROR_INVALID_PARAMETER);
    CHECK_EQ(RegSetValueExA(k, "x", 0, REG_SZ, NULL, 1), ERROR_INVALID_PARAMETER);
    CHECK_EQ(RegSetValueExA(k, "x", 0, REG_BINARY, NULL, 0), ERROR_SUCCESS);
    CHECK_EQ(RegSetValueExA(k, "x", 0, REG_DWORD, d, 4), ERROR_SUCCESS);
    DWORD type = 0, len = 0;
    CHECK_EQ(RegQueryValueExA(k, "x", NULL, &type, NULL, &len), ERROR_SUCCESS);
    CHECK_EQ(type, REG_DWORD); CHECK_EQ(len, 4);
    CHECK_EQ(RegSetValueExA(k, "X", 0, REG_BINARY, d, 3), ERROR_SUCCESS); /* replaces, new type and size */
    CHECK_EQ(RegQueryValueExA(k, "x", NULL, &type, NULL, &len), ERROR_SUCCESS);
    CHECK_EQ(type, REG_BINARY); CHECK_EQ(len, 3);
    /* default value: NULL and "" are the same */
    CHECK_EQ(RegSetValueExA(k, NULL, 0, REG_SZ, (const BYTE *)"dflt", 5), ERROR_SUCCESS);
    char buf[8];
    len = sizeof buf;
    CHECK_EQ(RegQueryValueExA(k, "", NULL, NULL, (LPBYTE)buf, &len), ERROR_SUCCESS);
    CHECK_STR(buf, "dflt");
    CHECK_EQ(RegSetValueExA(k, "", 0, REG_SZ, (const BYTE *)"d2", 3), ERROR_SUCCESS);
    len = sizeof buf;
    CHECK_EQ(RegQueryValueExA(k, NULL, NULL, NULL, (LPBYTE)buf, &len), ERROR_SUCCESS);
    CHECK_STR(buf, "d2");
    /* all types round trip */
    for (DWORD t = 0; t <= T_REG_QWORD; t++) {
        BYTE q[8] = { (BYTE)t, 0, 0xff, 0, 7, 8, 9, 10 };
        char nm[16];
        snprintf(nm, sizeof nm, "type%u", (unsigned)t);
        CHECK_EQ(RegSetValueExA(k, nm, 0, t, q, 8), ERROR_SUCCESS);
        BYTE r[8] = { 0 };
        DWORD ty = 99, l = 8;
        CHECK_EQ(RegQueryValueExA(k, nm, NULL, &ty, r, &l), ERROR_SUCCESS);
        CHECK_EQ(ty, t); CHECK_EQ(l, 8); CHECK(!memcmp(q, r, 8));
    }
    /* multi-sz */
    const char ms[] = "a\0bb\0";
    CHECK_EQ(RegSetValueExA(k, "ms", 0, T_REG_MULTI_SZ, (const BYTE *)ms, 6), ERROR_SUCCESS);
    char mb[8];
    len = sizeof mb;
    CHECK_EQ(RegQueryValueExA(k, "ms", NULL, &type, (LPBYTE)mb, &len), ERROR_SUCCESS);
    CHECK_EQ(type, T_REG_MULTI_SZ); CHECK_EQ(len, 6); CHECK(!memcmp(mb, ms, 6));
    RegCloseKey(k);
}

TEST(registry_big)
{
    reg_fresh("t6.hiv");
    HKEY k = mk(HKEY_CURRENT_USER, "Big");
    for (int i = 0; i < 1000; i++) {
        char nm[32];
        snprintf(nm, sizeof nm, "value-%d", i);
        DWORD v = (DWORD)i * 3 + 1;
        CHECK_EQ(RegSetValueExA(k, nm, 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_SUCCESS);
    }
    int bad = 0;
    for (int i = 0; i < 1000; i++) {
        char nm[32];
        snprintf(nm, sizeof nm, "VALUE-%d", i);
        DWORD v = 0, l = 4;
        if (RegQueryValueExA(k, nm, NULL, NULL, (LPBYTE)&v, &l) != ERROR_SUCCESS || v != (DWORD)i * 3 + 1)
            bad++;
    }
    CHECK_EQ(bad, 0);
    const size_t N = 100 * 1024;
    BYTE *big = (BYTE *)malloc(N), *back = (BYTE *)malloc(N);
    for (size_t i = 0; i < N; i++)
        big[i] = (BYTE)(i * 31 + (i >> 8));
    CHECK_EQ(RegSetValueExA(k, "blob", 0, REG_BINARY, big, (DWORD)N), ERROR_SUCCESS);
    DWORD l = (DWORD)N;
    CHECK_EQ(RegQueryValueExA(k, "blob", NULL, NULL, back, &l), ERROR_SUCCESS);
    CHECK_EQ(l, N);
    CHECK(!memcmp(big, back, N));
    /* 1000 keys under one parent, a deep path, and long names */
    for (int i = 0; i < 1000; i++) {
        char nm[32];
        snprintf(nm, sizeof nm, "sub%d", i);
        HKEY c = mk(k, nm);
        RegCloseKey(c);
    }
    std::string deep;
    for (int i = 0; i < 200; i++)
        deep += "d\\";
    deep += "leaf";
    HKEY d = mk(k, deep.c_str());
    RegCloseKey(d);
    RegCloseKey(k);
    /* persistence of all of it */
    fnvnx_registry_reset_for_test();
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "Big", 0, T_KEY_READ, &k), ERROR_SUCCESS);
    memset(back, 0, N);
    l = (DWORD)N;
    CHECK_EQ(RegQueryValueExA(k, "blob", NULL, NULL, back, &l), ERROR_SUCCESS);
    CHECK(l == N && !memcmp(big, back, N));
    DWORD v = 0, vl = 4;
    CHECK_EQ(RegQueryValueExA(k, "value-999", NULL, NULL, (LPBYTE)&v, &vl), ERROR_SUCCESS);
    CHECK_EQ(v, 2998);
    HKEY c;
    CHECK_EQ(RegOpenKeyExA(k, "sub999", 0, T_KEY_READ, &c), ERROR_SUCCESS);
    RegCloseKey(c);
    CHECK_EQ(RegOpenKeyExA(k, deep.c_str(), 0, T_KEY_READ, &c), ERROR_SUCCESS);
    RegCloseKey(c);
    RegCloseKey(k);
    free(big); free(back);
}

TEST(registry_persistence)
{
    reg_fresh("t7.hiv");
    DWORD disp;
    HKEY nv = mk(HKEY_CURRENT_USER, "Per\\NonVol", T_KEY_ALL, 0, &disp);
    HKEY vol = mk(HKEY_CURRENT_USER, "Per\\Vol", T_KEY_ALL, 1, &disp);
    CHECK_EQ(disp, REG_CREATED_NEW_KEY);
    DWORD v = 0xdeadbeef;
    /* odd name bytes (quote, backslash-free high bytes, embedded spaces) */
    const char odd[] = "na me\xe9\x01\xff\"=";
    CHECK_EQ(RegSetValueExA(nv, odd, 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_SUCCESS);
    CHECK_EQ(RegSetValueExA(nv, NULL, 0, REG_SZ, (const BYTE *)"def", 4), ERROR_SUCCESS);
    CHECK_EQ(RegSetValueExA(vol, "vv", 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_SUCCESS);
    /* a non-volatile key cannot live under a volatile parent */
    HKEY bad = NULL;
    CHECK_EQ(RegCreateKeyExA(vol, "child", 0, NULL, 0, T_KEY_ALL, NULL, &bad, NULL), ERROR_CHILD_MUST_BE_VOLATILE);
    CHECK_EQ(RegCreateKeyExA(vol, "child", 0, NULL, 1, T_KEY_ALL, NULL, &bad, NULL), ERROR_SUCCESS);
    RegCloseKey(bad);
    /* before the handle is closed or flushed, in-memory state is live */
    RegCloseKey(nv);
    RegCloseKey(vol);
    fnvnx_registry_reset_for_test();
    HKEY k;
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "per\\nonvol", 0, T_KEY_READ, &k), ERROR_SUCCESS);
    DWORD out = 0, len = 4;
    CHECK_EQ(RegQueryValueExA(k, odd, NULL, NULL, (LPBYTE)&out, &len), ERROR_SUCCESS);
    CHECK_EQ(out, 0xdeadbeef);
    char b[8];
    len = sizeof b;
    CHECK_EQ(RegQueryValueExA(k, "", NULL, NULL, (LPBYTE)b, &len), ERROR_SUCCESS);
    CHECK_STR(b, "def");
    RegCloseKey(k);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "Per\\Vol", 0, T_KEY_READ, &k), ERROR_FILE_NOT_FOUND);
    /* case preserved: the stored spelling is the first one used */
    CHECK_EQ(RegCreateKeyExA(HKEY_CURRENT_USER, "PER\\nonvol", 0, NULL, 0, T_KEY_ALL, NULL, &k, &disp), ERROR_SUCCESS);
    CHECK_EQ(disp, REG_OPENED_EXISTING_KEY);
    RegCloseKey(k);
    /* a handle that modified data flushes on close; a reload sees the change, and the old value stays */
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "Per\\NonVol", 0, T_KEY_ALL, &k), ERROR_SUCCESS);
    v = 1234;
    CHECK_EQ(RegSetValueExA(k, "second", 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_SUCCESS);
    RegCloseKey(k);
    fnvnx_registry_reset_for_test();
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "Per\\NonVol", 0, T_KEY_READ, &k), ERROR_SUCCESS);
    len = 4; out = 0;
    CHECK_EQ(RegQueryValueExA(k, "second", NULL, NULL, (LPBYTE)&out, &len), ERROR_SUCCESS);
    CHECK_EQ(out, 1234);
    len = 4;
    CHECK_EQ(RegQueryValueExA(k, odd, NULL, NULL, (LPBYTE)&out, &len), ERROR_SUCCESS);
    RegCloseKey(k);
    /* no temp file is left behind */
    char tmp[1300];
    snprintf(tmp, sizeof tmp, "%s/t7.hiv.tmp", s4_root());
    FILE *f = fopen(tmp, "rb");
    CHECK(f == NULL);
    if (f) fclose(f);
}

TEST(registry_threads_empty_start)
{
    reg_fresh("t8.hiv");
    HKEY k;
    CHECK_EQ(RegOpenKeyExA(HKEY_LOCAL_MACHINE, "SOFTWARE", 0, T_KEY_READ, &k), ERROR_FILE_NOT_FOUND); /* empty tree */
}

static std::string slurp(const char *path, bool *exists)
{
    std::string r;
    FILE *f = fopen(path, "rb");
    *exists = f != NULL;
    if (!f) return r;
    char b[4096];
    size_t n;
    while ((n = fread(b, 1, sizeof b, f)) > 0) r.append(b, n);
    fclose(f);
    return r;
}

TEST(registry_corrupt_hive)
{
    static char path[1200];
    snprintf(path, sizeof path, "%s/t9.hiv", s4_root());
    remove(path);
    fnvnx_registry_set_path(path);
    fnvnx_registry_reset_for_test();
    const char garbage[] = "this is not a hive \x01\x02\xff";
    FILE *f = fopen(path, "wb");
    CHECK(f != NULL);
    if (!f) return;
    fwrite(garbage, 1, sizeof garbage, f);
    fclose(f);
    HKEY k = NULL;
    DWORD v = 1, len = 4;
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "x", 0, T_KEY_READ, &k), ERROR_BADDB);
    CHECK_EQ(RegCreateKeyExA(HKEY_CURRENT_USER, "x", 0, NULL, 0, T_KEY_ALL, NULL, &k, NULL), ERROR_BADDB);
    CHECK_EQ(RegQueryValueExA(HKEY_CURRENT_USER, "n", NULL, NULL, (LPBYTE)&v, &len), ERROR_BADDB);
    CHECK_EQ(RegSetValueExA(HKEY_CURRENT_USER, "n", 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_BADDB);
    CHECK_EQ(RegCloseKey(HKEY_CURRENT_USER), ERROR_BADDB);
    bool ex;
    std::string after = slurp(path, &ex);
    CHECK(ex && after == std::string(garbage, sizeof garbage));
    /* a truncated valid hive is also damaged */
    reg_fresh("t9b.hiv");
    HKEY a = mk(HKEY_CURRENT_USER, "K\\L");
    RegSetValueExA(a, "n", 0, REG_DWORD, (const BYTE *)&v, 4);
    RegCloseKey(a);
    static char p2[1200];
    snprintf(p2, sizeof p2, "%s/t9b.hiv", s4_root());
    std::string full = slurp(p2, &ex), cut = full.substr(0, full.size() - 3);
    f = fopen(p2, "wb");
    fwrite(cut.data(), 1, cut.size(), f);
    fclose(f);
    fnvnx_registry_reset_for_test();
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "K", 0, T_KEY_READ, &k), ERROR_BADDB);
    CHECK(slurp(p2, &ex) == cut);
    /* a missing file is the empty tree */
    reg_fresh("t9c.hiv");
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "K", 0, T_KEY_READ, &k), ERROR_FILE_NOT_FOUND);
}

TEST(registry_set_path_flushes_and_reloads)
{
    reg_fresh("t10a.hiv");
    static char pa[1200], pb[1200];
    snprintf(pa, sizeof pa, "%s/t10a.hiv", s4_root());
    snprintf(pb, sizeof pb, "%s/t10b.hiv", s4_root());
    remove(pb);
    HKEY k = mk(HKEY_CURRENT_USER, "Moved");
    DWORD v = 42, out = 0, len = 4;
    CHECK_EQ(RegSetValueExA(k, "n", 0, REG_DWORD, (const BYTE *)&v, 4), ERROR_SUCCESS); /* handle left open */
    fnvnx_registry_set_path(pb);
    HKEY x = NULL;
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "Moved", 0, T_KEY_READ, &x), ERROR_FILE_NOT_FOUND); /* new hive: empty */
    bool ex;
    slurp(pb, &ex);
    CHECK(!ex);
    fnvnx_registry_set_path(pa);
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "Moved", 0, T_KEY_READ, &x), ERROR_SUCCESS); /* flushed to the old path */
    CHECK_EQ(RegQueryValueExA(x, "n", NULL, NULL, (LPBYTE)&out, &len), ERROR_SUCCESS);
    CHECK_EQ(out, 42);
    RegCloseKey(x);
}

/* ---- the first boot's seed (registry_seed.cpp), synthetic rows */
extern "C" int fnvnx_registry_first_run(char *why, size_t why_size);
extern "C" int fnvnx_registry_hive_present(void);
#define SEED_WIN "C:\\FalloutNV\\registry_seed.tsv"

TEST(registry_first_run_seed)
{
    reg_fresh("seed1.hiv");
    s4_write_file(SEED_WIN,
        "# synthetic\r\nFNVREGSEED 1\r\n"
        "HKLM\tSoftware\\Synth Co\\Game\tInstalled Path\tREG_SZ\t{app}\r\n"
        "HKLM\tSoftware\\Synth Co\\Game\tVersion\tREG_DWORD\t0x10\r\n"
        "HKLM\tSoftware\\Synth Co\\Game\tBlob\tREG_BINARY\t0a0B\r\n"
        "HKLM\tSoftware\\Synth Co\\Game\tLit\tREG_SZ\ta{{b\r\n"
        "HKCU\tSoftware\\Synth Co\\Empty\t\tKEY\t\r\n");
    char why[512];
    CHECK_EQ(fnvnx_registry_first_run(why, sizeof why), 5);
    CHECK(fnvnx_registry_hive_present());
    fnvnx_registry_reset_for_test();   /* the values below come from the hive file, not from memory */
    HKEY k = NULL;
    CHECK_EQ(RegOpenKeyExA(HKEY_LOCAL_MACHINE, "Software\\Synth Co\\Game", 0, T_KEY_READ, &k), ERROR_SUCCESS);
    DWORD type = 0, len = 0;
    CHECK_EQ(RegQueryValueExA(k, "Installed Path", NULL, &type, NULL, &len), ERROR_SUCCESS);   /* NULL data: size only */
    CHECK_EQ(type, REG_SZ);
    CHECK_EQ(len, 14);                                   /* "C:\FalloutNV\" + NUL */
    char small[3] = { 'x', 'x', 'x' };
    len = 3;
    CHECK_EQ(RegQueryValueExA(k, "installed path", NULL, &type, (LPBYTE)small, &len), ERROR_MORE_DATA);   /* case-insensitive */
    CHECK_EQ(len, 14);
    char buf[64];
    len = sizeof buf;
    CHECK_EQ(RegQueryValueExA(k, "Installed Path", NULL, &type, (LPBYTE)buf, &len), ERROR_SUCCESS);
    CHECK_EQ(len, 14);
    CHECK_STR(buf, "C:\\FalloutNV\\");
    unsigned char dw[4];
    len = 4;
    CHECK_EQ(RegQueryValueExA(k, "Version", NULL, &type, (LPBYTE)dw, &len), ERROR_SUCCESS);
    CHECK_EQ(type, REG_DWORD);
    CHECK(dw[0] == 0x10 && dw[1] == 0 && dw[2] == 0 && dw[3] == 0);
    len = sizeof buf;
    CHECK_EQ(RegQueryValueExA(k, "Blob", NULL, &type, (LPBYTE)buf, &len), ERROR_SUCCESS);
    CHECK_EQ(type, REG_BINARY);
    CHECK(len == 2 && buf[0] == 0x0a && buf[1] == 0x0b);
    len = sizeof buf;
    CHECK_EQ(RegQueryValueExA(k, "Lit", NULL, &type, (LPBYTE)buf, &len), ERROR_SUCCESS);
    CHECK_STR(buf, "a{b");
    RegCloseKey(k);
    HKEY e = NULL;
    CHECK_EQ(RegOpenKeyExA(HKEY_CURRENT_USER, "Software\\Synth Co\\Empty", 0, T_KEY_READ, &e), ERROR_SUCCESS);   /* a key row */
    RegCloseKey(e);

    /* a change is never re-seeded over: the second boot finds the hive and does not even read the seed */
    CHECK_EQ(RegOpenKeyExA(HKEY_LOCAL_MACHINE, "Software\\Synth Co\\Game", 0, T_KEY_ALL, &k), ERROR_SUCCESS);
    const char mine[] = "D:\\Mine\\";
    CHECK_EQ(RegSetValueExA(k, "Installed Path", 0, REG_SZ, (const BYTE *)mine, sizeof mine), ERROR_SUCCESS);
    CHECK_EQ(RegCloseKey(k), ERROR_SUCCESS);
    DeleteFileA(SEED_WIN);
    fnvnx_registry_reset_for_test();
    CHECK_EQ(fnvnx_registry_first_run(why, sizeof why), 0);
    CHECK_EQ(RegOpenKeyExA(HKEY_LOCAL_MACHINE, "Software\\Synth Co\\Game", 0, T_KEY_READ, &k), ERROR_SUCCESS);
    len = sizeof buf;
    CHECK_EQ(RegQueryValueExA(k, "Installed Path", NULL, &type, (LPBYTE)buf, &len), ERROR_SUCCESS);
    CHECK_STR(buf, "D:\\Mine\\");
    RegCloseKey(k);
}

TEST(registry_first_run_seed_loud_failures)
{
    char why[512];
    reg_fresh("seed2.hiv");
    DeleteFileA(SEED_WIN);
    CHECK_EQ(fnvnx_registry_first_run(why, sizeof why), -1);   /* no seed: no empty registry */
    CHECK(strstr(why, "registry seed is missing") != NULL);
    CHECK(!fnvnx_registry_hive_present());
    static const struct { const char *text, *reason; } bad[] = {
        { "", "no FNVREGSEED header" },
        { "FNVREGSEED 2\n", "format 1" },
        { "FNVREGSEED 1\n", "no rows" },
        { "FNVREGSEED 1\nHKLM\tK\tn\tREG_SZ\n", "5 tab-separated fields" },
        { "FNVREGSEED 1\nHKXX\tK\tn\tREG_SZ\tv\n", "unknown root" },
        { "FNVREGSEED 1\nHKLM\tK\tn\tREG_FOO\tv\n", "unknown type" },
        { "FNVREGSEED 1\nHKLM\tK\tn\tREG_SZ\t{pf}\\x\n", "bad installer constant" },
        { "FNVREGSEED 1\nHKLM\tK\tn\tREG_DWORD\t4294967296\n", "bad number" },
        { "FNVREGSEED 1\nHKLM\tK\tn\tREG_BINARY\t0g\n", "bad hex" },
        { "FNVREGSEED 1\nHKLM\t\tn\tREG_SZ\tv\n", "bad key path" },
        { "FNVREGSEED 1\nHKLM\tK\tn\tREG_SZ\tv\nHKLM\tK\tn\tREG_SZ\t{x}\n", "line 3" },
    };
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        reg_fresh("seed2.hiv");
        s4_write_file(SEED_WIN, bad[i].text);
        CHECK_EQ(fnvnx_registry_first_run(why, sizeof why), -1);
        CHECK(strstr(why, bad[i].reason) != NULL);
        CHECK(!fnvnx_registry_hive_present());   /* a bad seed leaves no hive behind */
    }
    DeleteFileA(SEED_WIN);
}
