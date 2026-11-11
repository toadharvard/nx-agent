/* test_k32_contract_fm.cpp - FormatMessageA on its documented domain (task W20 group 4). Expected values are Windows
 * results as recorded by Wine 9.0's kernel32/tests/format_msg.c. Arguments are 32-bit words (the ILP32 guest's va_list
 * and DWORD_PTR arrays): a string argument is a 32-bit address (MAP_32BIT memory on the LP64 host build). */
#include "s4_test.h"

#include <stdint.h>
#include <stdlib.h>
#include <vector>
#include <sys/mman.h>

#include "../../platform/win32/w32.h"

#ifndef ERROR_NO_WORK_DONE
#define ERROR_NO_WORK_DONE 1237L
#endif
#ifndef ERROR_MR_MID_NOT_FOUND
#define ERROR_MR_MID_NOT_FOUND 317L
#endif
#ifndef ERROR_RESOURCE_LANG_NOT_FOUND
#define ERROR_RESOURCE_LANG_NOT_FOUND 1815L
#endif
#ifndef FORMAT_MESSAGE_FROM_HMODULE
#define FORMAT_MESSAGE_FROM_HMODULE 0x800
#endif
#ifndef FORMAT_MESSAGE_ARGUMENT_ARRAY
#define FORMAT_MESSAGE_ARGUMENT_ARRAY 0x2000
#endif
#ifndef FORMAT_MESSAGE_MAX_WIDTH_MASK
#define FORMAT_MESSAGE_MAX_WIDTH_MASK 0xff
#endif
#define FM_STR FORMAT_MESSAGE_FROM_STRING

static uint32_t p32(const char *s, size_t extra = 0)   /* a copy of s below 4 GiB */
{
    static char *pool;
    static size_t used;
    if (!pool) {
        pool = (char *)mmap(NULL, 1 << 16, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT, -1, 0);
        if (pool == MAP_FAILED)
            return 0;
    }
    size_t n = strlen(s) + 1 + extra;
    if (used + n > (1 << 16))
        used = 0;
    char *p = pool + used;
    memcpy(p, s, strlen(s) + 1);
    used += (n + 7) & ~(size_t)7;
    return (uint32_t)(uintptr_t)p;
}
static uint32_t p32w(const WCHAR *w)
{
    size_t n = 0;
    while (w[n])
        n++;
    char *raw = (char *)(uintptr_t)p32("", (n + 1) * 2 + 8);
    memcpy(raw, w, (n + 1) * 2);
    return (uint32_t)(uintptr_t)raw;
}

/* FormatMessageA with a va_list of 32-bit words (the guest ABI: lpArguments is a pointer to the va_list) */
static DWORD fml(DWORD flags, const char *src, char *out, DWORD n, const uint32_t *words, DWORD id = 0, DWORD lang = 0)
{
    const uint32_t *list = words;
    return FormatMessageA(flags, src, id, lang, out, n, words ? (void *)&list : NULL);
}
static DWORD fma(DWORD flags, const char *src, char *out, DWORD n, const uint32_t *words)
{
    return FormatMessageA(flags | FORMAT_MESSAGE_ARGUMENT_ARRAY, src, 0, 0, out, n, (void *)words);
}

TEST(k32c_formatmessage_string)
{
    char out[300];
    struct { const char *src; const char *want; uint32_t a[6]; } t[] = {
        { "test", "test", {} },
        { "%1!s!", "test", { p32("test") } },
        { "%1", "test", { p32("test") } },
        { "%1%2", "test", { p32("te"), p32("st") } },
        { "%1%3%2%1", "test", { p32("t"), p32("s"), p32("e") } },
        { "%1!c!%2!c!%3!c!%1!c!", "test", { 't', 'e', 's' } },
        { "%1!lc!%2!lc!%3!lc!%1!lc!", "test", { 't', 'e', 's' } },
        { "%1!C!%2!C!%3!C!%1!C!", "test", { 't', 'e', 's' } },
        { "%1!d!%2!d!%3!d!", "123", { 1, 2, 3 } },
        { "%1!4d!", "   1", { 1 } },
        { "%1!-4d!", "1   ", { 1 } },
        { "%1!4x!", "   b", { 11 } },
        { "%1!4X!", "   B", { 11 } },
        { "%1!-4X!", "B   ", { 11 } },
        { "%1!4X!", " 1AB", { 0x1ab } },
        { " %%%% ", " %% ", {} },
        { " %.%. %1!d!", " .. 427", { 0x1ab } },
        { "test%0test", "test", {} },
        { "yah%!%0   ", "yah!", {} },
        { "% %   ", "    ", {} },
        { "%n%r%t", "\r\n\r\t", {} },
        { "hi\n", "hi\r\n", {} },
        { "hi\r\n", "hi\r\n", {} },
        { "\r", "\r\n", {} },
        { "\r\r\n", "\r\n\r\n", {} },
        { "%1", "(null)", { 0 } },
        { "%1!3s!", "  t", { p32("t") } },
        { "%1!*s!", "   t", { 4, p32("t") } },
        { "%1!4.2u!", "  03", { 3 } },
        { "%1!*.*u!", "  001", { 5, 3, 1 } },
        { "%1!*.*u!,%1!*.*u!", "  001, 0002", { 5, 3, 1, 4, 2 } },
        { "%1!*.*u!,%3!*.*u!", "  001,  0002", { 5, 3, 1, 6, 4, 2 } },
    };
    for (size_t i = 0; i < sizeof t / sizeof *t; i++) {
        memset(out, 'x', sizeof out);
        DWORD r = fml(FM_STR, t[i].src, out, sizeof out, t[i].a);
        s4_checks++;
        if (r != strlen(t[i].want) || strcmp(out, t[i].want)) {
            s4_failures++;
            fprintf(stderr, "  FAIL FormatMessageA(\"%s\"): r=%lu out=[%s] want [%s]\n", t[i].src, (unsigned long)r, out, t[i].want);
        }
    }
    /* ls / S / ws take UTF-16 strings */
    static const WCHAR tw[] = { 't', 'e', 's', 't', 0 };
    uint32_t w1[1] = { p32w(tw) };
    CHECK_EQ(fml(FM_STR, "%1!ls!", out, sizeof out, w1), 4);
    CHECK_STR(out, "test");
    CHECK_EQ(fml(FM_STR, "%1!S!", out, sizeof out, w1), 4);
    CHECK_STR(out, "test");
    CHECK_EQ(fml(FM_STR, "%1!ws!", out, sizeof out, w1), 4);
    CHECK_STR(out, "test");
    /* argument arrays number their arguments differently when stars are used */
    uint32_t arr[] = { 6, 4, 2, 5, 3, 1 };
    CHECK_EQ(fma(FM_STR, "%1!*.*u!,%1!*.*u!", out, sizeof out, arr), 13);
    CHECK_STR(out, "  0002, 00003");
    CHECK_EQ(fma(FM_STR, "%1!*.*u!,%4!*.*u!", out, sizeof out, arr), 12);
    CHECK_STR(out, "  0002,  001");
    uint32_t one[] = { p32("arr") };
    CHECK_EQ(fma(FM_STR, "<%1>", out, sizeof out, one), 5);
    CHECK_STR(out, "<arr>");
    /* the errors: no text, an insert without arguments, a lone %, a bad spec */
    memset(out, 'x', 6);
    SetLastError(0xdeadbeef);
    CHECK_EQ(FormatMessageA(FM_STR, "", 0, 0, out, sizeof out, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_NO_WORK_DONE);
    CHECK(!memcmp(out, "xxxxxx", 6));
    CHECK_EQ(FormatMessageA(FM_STR, "%", 0, 0, out, sizeof out, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK_EQ(FormatMessageA(FM_STR, "test%", 0, 0, out, sizeof out, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK_EQ(FormatMessageA(FM_STR, "%1", 0, 0, out, sizeof out, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK_EQ(FormatMessageA(FM_STR | FORMAT_MESSAGE_ARGUMENT_ARRAY, "%1", 0, 0, out, sizeof out, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    uint32_t a1[] = { 1 };
    CHECK_EQ(fml(FM_STR, "%1!q!", out, sizeof out, a1), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK_EQ(fml(FM_STR, "%1!d", out, sizeof out, a1), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK_EQ(fml(FM_STR, "%1!*.*.*d!", out, sizeof out, a1), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* IGNORE_INSERTS keeps the inserts, %n still breaks */
    CHECK_EQ(FormatMessageA(FM_STR | FORMAT_MESSAGE_IGNORE_INSERTS, "a%1!d!%n%2 %%", 0, 0, out, sizeof out, NULL), 13);
    CHECK_STR(out, "a%1!d!\r\n%2 %%");
    /* a 64-bit conversion takes two words of a va_list */
    uint32_t q[] = { 0x89abcdefu, 0x01234567u, 9 };
    CHECK_EQ(fml(FM_STR, "%1!I64x! %2!d!", out, sizeof out, q), 17);
    CHECK_STR(out, "123456789abcdef 9");
}

TEST(k32c_formatmessage_buffers_and_width)
{
    char *out = (char *)malloc(40000);
    /* the buffer protocol */
    char small[5];
    memset(small, 'x', sizeof small);
    SetLastError(0);
    CHECK_EQ(FormatMessageA(FM_STR, "test", 0, 0, small, 0, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
    CHECK_EQ(FormatMessageA(FM_STR, "test", 0, 0, small, 4, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
    CHECK(!memcmp(small, "xxxxx", 5));
    CHECK_EQ(FormatMessageA(FM_STR, "test", 0, 0, small, 5, NULL), 4);
    CHECK_STR(small, "test");
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM, NULL, 0, 0, NULL, 1, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_INSUFFICIENT_BUFFER);
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_ALLOCATE_BUFFER, NULL, 0, 0, NULL, 1, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_NOT_ENOUGH_MEMORY);
    /* sizes of 32768 and more are refused; the longest message fits 32767 */
    char *big = (char *)malloc(32767);
    memset(big, 'A', 32766);
    big[32766] = 0;
    CHECK_EQ(FormatMessageA(FM_STR, big, 0, 0, out, 32767, NULL), 32766);
    CHECK_EQ(FormatMessageA(FM_STR, big, 0, 0, out, 32768, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    free(big);
    /* ALLOCATE_BUFFER: LocalAlloc'd result, NULL pointer for no result */
    char *p = (char *)0xdeadbeef;
    CHECK_EQ(FormatMessageA(FM_STR | FORMAT_MESSAGE_ALLOCATE_BUFFER, "", 0, 0, (char *)&p, 0, NULL), 0);
    CHECK(p == NULL);
    CHECK_EQ(GetLastError(), ERROR_NO_WORK_DONE);
    CHECK_EQ(FormatMessageA(FM_STR | FORMAT_MESSAGE_ALLOCATE_BUFFER, "test", 0, 0, (char *)&p, 1024, NULL), 4);
    CHECK(p && !strcmp(p, "test"));
    if (p)
        LocalFree(p);
    /* MAX_WIDTH_MASK line breaking: the width is a count of characters per line, strictly less than width */
    struct { DWORD w; const char *src, *want; } t[] = {
        { 20, "short long line", "short long line" },
        { 11, "short long line", "short long\r\nline" },
        { 20, "short long long line", "short long long\r\nline" },
        { 10, "short long line", "short\r\nlong line" },
        { 16, "short long    line", "short long\r\nline" },
        { 16, "short long    wordlongerthanaline", "short long\r\nwordlongerthanal\r\nine" },
        { 12, "short long    line", "short long\r\n  line" },
        { 12, "short long    wordlongerthanaline", "short long\r\n\r\nwordlongerth\r\nanaline" },
        { 15, "   short line", "   short line" },
        { 11, "   shortlong line", "\r\nshortlong\r\nline" },
        { 11, "l1%n   shortlong line", "l1\r\n\r\nshortlong\r\nline" },
        { 5, "                ", "\r\n\r\n\r\n " },
        { 5, "l1               ", "l1\r\n\r\n\r\n  " },
        { 8, "shortlon", "shortlon\r\n" },
        { 8, "shortlongline", "shortlon\r\ngline" },
        { 7, "short long line", "short\r\nlong\r\nline" },
        { 11, "short\nlong line", "short long\r\nline" },
        { 8, "shortlon%n", "shortlon\r\n\r\n" },
        { 10, "short%nlong line", "short\r\nlong line" },
        { 8, "short%nlong line", "short\r\nlong\r\nline" },
        { 10, "short%rlong line", "short\rlong line" },
        { 8, "short%rlong line", "short\rlong\r\nline" },
        { 11, "short long\tline", "short\r\nlong\tline" },
        { 11, "short long-line", "short\r\nlong-line" },
        { 0xff, "hi\n", "hi " },
        { 0xff, "hi\r\n", "hi " },
        { 0xff, "\r", " " },
        { 0xff, "\r\r\n", "  " },
    };
    for (size_t i = 0; i < sizeof t / sizeof *t; i++) {
        DWORD r = FormatMessageA(FM_STR | t[i].w, t[i].src, 0, 0, out, 30000, NULL);
        s4_checks++;
        if (r != strlen(t[i].want) || strcmp(out, t[i].want)) {
            s4_failures++;
            fprintf(stderr, "  FAIL wrap %lu of \"%s\": r=%lu [%s] want [%s]\n", (unsigned long)t[i].w, t[i].src, (unsigned long)r, out, t[i].want);
        }
    }
    CHECK_EQ(FormatMessageA(FM_STR | FORMAT_MESSAGE_IGNORE_INSERTS | 8, "short%nlong line%1", 0, 0, out, 100, NULL), 19);
    CHECK_STR(out, "short\r\nlong\r\nline%1");
    free(out);
}

/* a module image whose resource directory holds RT_MESSAGETABLE / name 1 / language 0x409: ids 0x100..0x102 */
extern "C" void fnvnx_resource_set_image_for_test(HMODULE, const void *, size_t);
static void le32(std::vector<uint8_t> &b, size_t at, uint32_t v) { for (int i = 0; i < 4; i++) b[at + i] = (uint8_t)(v >> (8 * i)); }
static std::vector<uint8_t> msgtable_image(void)
{
    std::vector<uint8_t> blob(4 + 12, 0);
    le32(blob, 0, 1);                                  /* NumberOfBlocks */
    le32(blob, 4, 0x100); le32(blob, 8, 0x102); le32(blob, 12, 16);
    auto entry = [&](const char *s, bool wide) {
        std::vector<uint8_t> t;
        if (wide)
            for (const char *p = s; ; p++) { t.push_back((uint8_t)*p); t.push_back(0); if (!*p) break; }
        else
            t.insert(t.end(), s, s + strlen(s) + 1);
        while (t.size() & 3)
            t.push_back(0);
        blob.push_back((uint8_t)((t.size() + 4) & 255)); blob.push_back((uint8_t)((t.size() + 4) >> 8));
        blob.push_back(wide ? 1 : 0); blob.push_back(0);
        blob.insert(blob.end(), t.begin(), t.end());
    };
    entry("Hello %1!s!\r\n", false);
    entry("Wide %1!d!\r\n", true);
    entry("Last\r\n", false);
    std::vector<uint8_t> rs(0x58, 0);
    le32(rs, 12, 0x00010000); le32(rs, 16, 11); le32(rs, 20, 0x80000018u);
    le32(rs, 0x18 + 12, 0x00010000); le32(rs, 0x18 + 16, 1); le32(rs, 0x18 + 20, 0x80000030u);
    le32(rs, 0x30 + 12, 0x00010000); le32(rs, 0x30 + 16, 0x409); le32(rs, 0x30 + 20, 0x48);
    le32(rs, 0x48, 0x1000 + 0x58); le32(rs, 0x4C, (uint32_t)blob.size());
    rs.insert(rs.end(), blob.begin(), blob.end());
    std::vector<uint8_t> im(0x1000 + rs.size(), 0);
    im[0] = 'M'; im[1] = 'Z'; le32(im, 0x3C, 0x80);
    memcpy(&im[0x80], "PE\0\0", 4);
    im[0x84] = 0x4C; im[0x85] = 0x01; im[0x86] = 1;
    im[0x84 + 16] = 224;
    im[0x98] = 0x0B; im[0x99] = 0x01;
    le32(im, 0x98 + 56, (uint32_t)im.size());
    le32(im, 0x98 + 92, 16);
    le32(im, 0xF8 + 16, 0x1000); le32(im, 0xF8 + 20, (uint32_t)rs.size());
    memcpy(&im[0x178], ".rsrc", 5);
    le32(im, 0x178 + 8, (uint32_t)rs.size()); le32(im, 0x178 + 12, 0x1000);
    le32(im, 0x178 + 16, (uint32_t)rs.size()); le32(im, 0x178 + 20, 0x1000);
    memcpy(&im[0x1000], rs.data(), rs.size());
    return im;
}

TEST(k32c_formatmessage_hmodule)
{
    std::vector<uint8_t> im = msgtable_image();
    HMODULE m = (HMODULE)(uintptr_t)0x560000;
    fnvnx_resource_set_image_for_test(m, im.data(), im.size());
    char out[128];
    uint32_t a1[] = { p32("world") };
    CHECK_EQ(fml(FORMAT_MESSAGE_FROM_HMODULE, (const char *)m, out, sizeof out, a1, 0x100), strlen("Hello world\r\n"));
    CHECK_STR(out, "Hello world\r\n");
    uint32_t a2[] = { 42 };                            /* a UTF-16 entry is narrowed */
    CHECK_EQ(fml(FORMAT_MESSAGE_FROM_HMODULE, (const char *)m, out, sizeof out, a2, 0x101, 0x409), strlen("Wide 42\r\n"));
    CHECK_STR(out, "Wide 42\r\n");
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_HMODULE, m, 0x102, 0, out, sizeof out, NULL), 6);
    CHECK_STR(out, "Last\r\n");
    SetLastError(0);
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_HMODULE, m, 0x103, 0, out, sizeof out, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_MR_MID_NOT_FOUND);
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_HMODULE, m, 0x100, 0x0407, out, sizeof out, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_RESOURCE_LANG_NOT_FOUND);
    /* the module first, then the system table for ids the module lacks */
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_HMODULE | FORMAT_MESSAGE_FROM_SYSTEM, m, 2, 0, out, sizeof out, NULL), strlen("File not found.\r\n"));
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_HMODULE | FORMAT_MESSAGE_FROM_SYSTEM, m, 0x102, 0, out, sizeof out, NULL), 6);
    fnvnx_resource_set_image_for_test(m, NULL, 0);
}

TEST(k32c_formatmessage_system_and_module)
{
    char out[512];
    /* the system table: the Win32 error codes' message text (Wine 9.0's English table), CRLF-terminated */
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM, NULL, 2, 0, out, sizeof out, NULL), strlen("File not found.\r\n"));
    CHECK_STR(out, "File not found.\r\n");
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM, NULL, 5, 0x0409, out, sizeof out, NULL), strlen("Access denied.\r\n"));
    CHECK_STR(out, "Access denied.\r\n");
    /* an error HRESULT of FACILITY_WIN32 is folded to its code */
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM, NULL, 0x80070005u, 0, out, sizeof out, NULL), strlen("Access denied.\r\n"));
    CHECK_STR(out, "Access denied.\r\n");
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM, NULL, 0x80004005u, 0, out, sizeof out, NULL), strlen("Call failed.\r\n"));
    /* inserts of a system message: "Message 0x%1 not found in file %2." */
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, NULL, ERROR_MR_MID_NOT_FOUND, 0, out, sizeof out, NULL),
             strlen("Message 0x%1 not found in file %2.\r\n"));
    uint32_t a2[] = { p32("1A"), p32("x.dll") };
    CHECK_EQ(fml(FORMAT_MESSAGE_FROM_SYSTEM, NULL, out, sizeof out, a2, ERROR_MR_MID_NOT_FOUND), strlen("Message 0x1A not found in file x.dll.\r\n"));
    CHECK_STR(out, "Message 0x1A not found in file x.dll.\r\n");
    /* with MAX_WIDTH_MASK the trailing line break of the table text becomes a space */
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_MAX_WIDTH_MASK, NULL, 2, 0, out, sizeof out, NULL), strlen("File not found. "));
    /* errors: unknown id, a language the table lacks, no source flag */
    SetLastError(0);
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM, NULL, 0x7fff0000u, 0, out, sizeof out, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_MR_MID_NOT_FOUND);
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM, NULL, 2, 0x0407, out, sizeof out, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_RESOURCE_LANG_NOT_FOUND);
    CHECK_EQ(FormatMessageA(0, NULL, 2, 0, out, sizeof out, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* a module without a message table falls through to the system table when asked to */
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_HMODULE, GetModuleHandleA(NULL), 2, 0, out, sizeof out, NULL), 0);
    CHECK(GetLastError() != 0);
    CHECK_EQ(FormatMessageA(FORMAT_MESSAGE_FROM_HMODULE | FORMAT_MESSAGE_FROM_SYSTEM, GetModuleHandleA(NULL), 2, 0, out, sizeof out, NULL),
             strlen("File not found.\r\n"));
}
