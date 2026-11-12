/* s6b_unit - unit tests: SDP reader on synthetic packages, API edge cases, DKSH validator. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_sdp.h"
#include "fnvnx_shaders.h"
#include "fnvnx_sha256.h"

static int fails, total;
#define CHECK(c) do { total++; if (!(c)) { fails++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); } } while (0)

static void put32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }

static void test_sdp(void)
{
    char err[256];
    fnvnx_sdp s;
    uint8_t a[] = { 1, 2, 3, 4 }, b[] = { 9, 8, 7, 6, 5, 4, 3, 2 };
    fnvnx_sdp_entry in[3];
    memset(in, 0, sizeof in);
    strcpy(in[0].name, "SLS2000.vso"); in[0].data = a; in[0].size = 4;
    memset(in[1].name, 'x', 256); in[1].data = b; in[1].size = 8;          /* full-width name, no NUL */
    strcpy(in[2].name, "EMPTY.pso"); in[2].data = NULL; in[2].size = 0;      /* zero-length entry */
    uint8_t *buf; size_t len;
    CHECK(fnvnx_sdp_write(in, 3, &buf, &len) == 0);
    CHECK(len == 12 + 3 * 260 + 12);
    CHECK(fnvnx_sdp_parse(buf, len, &s, err, sizeof err) == 0);
    CHECK(s.count == 3 && s.magic == 100 && s.data_size == len - 12);
    CHECK(!strcmp(s.entries[0].name, "SLS2000.vso") && s.entries[0].size == 4 && !memcmp(s.entries[0].data, a, 4));
    CHECK(s.entries[0].offset == 12 + 260);
    CHECK(strlen(s.entries[1].name) == 256 && s.entries[1].size == 8 && !memcmp(s.entries[1].data, b, 8));
    CHECK(s.entries[2].size == 0 && !strcmp(s.entries[2].name, "EMPTY.pso"));
    fnvnx_sdp_free(&s);

    /* malformed variants */
    CHECK(fnvnx_sdp_parse(buf, 8, &s, err, sizeof err) != 0);                /* short header */
    CHECK(fnvnx_sdp_parse(buf, len - 1, &s, err, sizeof err) != 0);          /* data size mismatch */
    uint8_t *m = (uint8_t *)malloc(len);
    memcpy(m, buf, len); put32(m, 101);
    CHECK(fnvnx_sdp_parse(m, len, &s, err, sizeof err) != 0);                /* magic */
    memcpy(m, buf, len); put32(m + 4, 0x40000000u);
    CHECK(fnvnx_sdp_parse(m, len, &s, err, sizeof err) != 0);                /* count overflow */
    memcpy(m, buf, len); put32(m + 4, 2);
    CHECK(fnvnx_sdp_parse(m, len, &s, err, sizeof err) != 0);                /* trailing bytes */
    memcpy(m, buf, len); put32(m + 12 + 256, 0xFFFFFFF0u);
    CHECK(fnvnx_sdp_parse(m, len, &s, err, sizeof err) != 0);                /* entry size past end */
    memcpy(m, buf, len); put32(m + 4, 4);
    CHECK(fnvnx_sdp_parse(m, len, &s, err, sizeof err) != 0);                /* truncated entry header */
    free(m);
    free(buf);

    CHECK(fnvnx_sdp_write(NULL, 0, &buf, &len) == 0 && len == 12);
    CHECK(fnvnx_sdp_parse(buf, len, &s, err, sizeof err) == 0 && s.count == 0);
    fnvnx_sdp_free(&s);
    free(buf);
}

static void test_api(void)
{
    fnvnx_shader_result r;
    CHECK(fnvnx_shader_translate(NULL, 0, NULL, &r) == 0);
    fnvnx_shader_result_free(&r);
    uint32_t junk[4] = { 0x12345678, 1, 2, 3 };
    CHECK(fnvnx_shader_translate(junk, sizeof junk, NULL, &r) == 0 && r.error[0]);
    fnvnx_shader_result_free(&r);
    CHECK(fnvnx_shader_translate(junk, 6, NULL, &r) == 0);                   /* not a multiple of 4 */
    fnvnx_shader_result_free(&r);
    fnvnx_shader_opts o = { 0, "nope" };
    uint32_t vs[] = { 0xFFFE0101, 0x0000FFFF };                              /* vs_1_1, end */
    CHECK(fnvnx_shader_translate(vs, sizeof vs, &o, &r) == 0 && strstr(r.error, "backend"));
    fnvnx_shader_result_free(&r);
    { uint8_t k[32]; char hx[FNVNX_SHADER_KEY_HEX]; fnvnx_sha256("abc", 3, k); fnvnx_shader_key_hex(k, hx);
      CHECK(!strcmp(hx, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")); }   /* FIPS 180-4 "abc" */
    CHECK(fnvnx_shader_backends()[0] && !strcmp(fnvnx_shader_backends()[0], "mojoshader-glsldk"));

    uint8_t d[512]; char err[128];
    memset(d, 0, sizeof d);
    put32(d, 0x48534B44); put32(d + 4, 24); put32(d + 8, 256); put32(d + 12, 256); put32(d + 16, 24); put32(d + 20, 1); put32(d + 24, 1);
    CHECK(fnvnx_dksh_validate(d, 512, FNVNX_STAGE_PIXEL, err, sizeof err) == 0);
    CHECK(fnvnx_dksh_validate(d, 512, FNVNX_STAGE_VERTEX, err, sizeof err) != 0);
    CHECK(fnvnx_dksh_validate(d, 511, FNVNX_STAGE_PIXEL, err, sizeof err) != 0);
    put32(d, 0);
    CHECK(fnvnx_dksh_validate(d, 512, FNVNX_STAGE_PIXEL, err, sizeof err) != 0);
}

int main(void)
{
    test_sdp();
    test_api();
    printf("s6b_unit: %d/%d checks passed\n", total - fails, total);
    return fails != 0;
}
