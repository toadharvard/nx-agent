/* loadcheck: run the whole data-image pipeline on the host against an executable and print aggregate numbers.
 * Never prints image bytes. Used by tests/s3 on a synthetic PE and by developers on their own FalloutNV.exe.
 *
 *   loadcheck --exe FalloutNV.exe [--probes probes.txt] [--data-probes dataprobes.txt] [--addrmap addrmap.bin]
 *             [--hints hints.txt]
 *             [--window 0x00400000:0x02100000] [--load-base 0x00400000] [--headers] [--imports imports.txt]
 *             [--fixups-in fixups.bin | --fixups-out fixups.bin] [--min-conf 1] [--no-relocs] [--no-index] [--trap]
 *             [--expect expect.txt] [--dump-vtable VA] [-v]
 *
 * probes.txt:  "<va> <size> <sha256 hex>" per line (loader/tools/mkprobes.py --txt): code fingerprints
 * dataprobes.txt: "<va> <hex bytes>" per line (loader/tools/mkdataprobes.py): bytes expected in the data sections
 *
 * Acceptance (docs/DATAIMAGE.md "Steam executable"): every mapped section except .rsrc must look plain; then either
 * the code probes match (plain exe) or the data probes do (>= 90 %; e.g. the Steam exe, whose .text is encrypted).
 * hints.txt:   one original VA per line (vftable addresses)
 * imports.txt: "<dll> <name|#ordinal> <value>" per line; anything not listed is unresolved
 * expect.txt:  "word <va> <value>", "deref <va> <hex bytes>", "zero <va> <size>", "unmapped <va>",
 *              "stat <key> <value>"; every line is checked after the pipeline, failures make the exit code 1.
 */
#include "fnvnx_host.h"
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define MAXSTATS 128
static struct { char key[48]; unsigned long val; } stats[MAXSTATS];
static unsigned nstats;

static void stat_set(const char *k, unsigned long v) {
    unsigned i;
    for (i = 0; i < nstats; i++) if (!strcmp(stats[i].key, k)) { stats[i].val = v; return; }
    if (nstats < MAXSTATS) { snprintf(stats[nstats].key, sizeof stats[0].key, "%s", k); stats[nstats++].val = v; }
}
static int stat_get(const char *k, unsigned long *v) {
    unsigned i;
    for (i = 0; i < nstats; i++) if (!strcmp(stats[i].key, k)) { *v = stats[i].val; return 1; }
    return 0;
}

static void *slurp(const char *path, uint32_t *size) {
    FILE *f = fopen(path, "rb");
    long n;
    void *p;
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    n = ftell(f);
    fseek(f, 0, SEEK_SET);
    p = malloc(n > 0 ? (size_t)n : 1);
    if (p && n > 0 && fread(p, 1, (size_t)n, f) != (size_t)n) { free(p); p = NULL; }
    fclose(f);
    *size = (uint32_t)(n > 0 ? n : 0);
    return p;
}

static int hexval(int c) {
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}
static int parse_hex(const char *s, uint8_t *out, unsigned max) {
    unsigned n = 0;
    while (s[0] && s[1] && n < max) {
        int a = hexval(s[0]), b = hexval(s[1]);
        if (a < 0 || b < 0) break;
        out[n++] = (uint8_t)(a * 16 + b);
        s += 2;
    }
    return (int)n;
}

/* imports.txt */
typedef struct imp { char dll[64], name[96]; uint32_t value; } imp;
static imp *imps;
static unsigned nimps;

static uint32_t resolver(void *ctx, const char *dll, const char *name, uint32_t ordinal, unsigned hint) {
    unsigned i;
    char key[96];
    (void)ctx; (void)hint;
    if (name) snprintf(key, sizeof key, "%s", name);
    else snprintf(key, sizeof key, "#%u", (unsigned)ordinal);
    for (i = 0; i < nimps; i++)
        if (!strcasecmp(imps[i].dll, dll) && !strcmp(imps[i].name, key)) return imps[i].value;
    return 0;
}

static uint32_t trap_fn(void *ctx, uint32_t orig) { (void)ctx; return 0xee000000u | (orig & 0x00ffffffu); }
/* a 64-bit host cannot store adapter addresses in image words: a token stands for "adapter over this D0" */
static uint32_t deldtor_token(void *ctx, uint32_t orig, uint32_t d0) { (void)ctx; (void)orig; return 0xdd000000u | (d0 & 0x00ffffffu); }

static const char *class_name(unsigned k) {
    switch (k) {
    case FNVNX_FX_VTABLE_COL: return "vtable_col";
    case FNVNX_FX_VTABLE_HINT: return "vtable_hint";
    case FNVNX_FX_TLS_CALLBACK: return "tls";
    case FNVNX_FX_RELOC: return "reloc";
    case FNVNX_FX_RUN: return "run";
    case FNVNX_FX_ISOLATED: return "isolated";
    case FNVNX_FX_VPTR: return "vptr";
    case FNVNX_FX_REJ_STRING: return "rej_string";
    case FNVNX_FX_REJ_ROUND: return "rej_round";
    case FNVNX_FX_UNKNOWN: return "unknown";
    case FNVNX_FX_MID: return "mid";
    default: return NULL;
    }
}

static int check_expect(const char *path, const fnvnx_image *img) {
    FILE *f = fopen(path, "r");
    char line[1024], kind[16], a[64], b[900];
    int fails = 0, lines = 0;
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return 1; }
    while (fgets(line, sizeof line, f)) {
        unsigned long va, v, want;
        uint32_t got;
        int n;
        if (line[0] == '#' || line[0] == '\n') continue;
        n = sscanf(line, "%15s %63s %899s", kind, a, b);
        if (n < 2) continue;
        lines++;
        va = strtoul(a, NULL, 0);
        if (!strcmp(kind, "word") && n == 3) {
            want = strtoul(b, NULL, 0);
            if (fnvnx_image_read32(img, (uint32_t)va, &got) || got != want) {
                fprintf(stderr, "FAIL word 0x%08lx: got 0x%08x want 0x%08lx\n", va, got, want);
                fails++;
            }
        } else if (!strcmp(kind, "deref") && n == 3) {
            uint8_t want_b[450];
            int len = parse_hex(b, want_b, sizeof want_b);
            const void *p;
            if (fnvnx_image_read32(img, (uint32_t)va, &got) || !(p = fnvnx_image_ptr(img, got, (uint32_t)len)) ||
                memcmp(p, want_b, (size_t)len)) {
                fprintf(stderr, "FAIL deref 0x%08lx\n", va);
                fails++;
            }
        } else if (!strcmp(kind, "zero") && n == 3) {
            unsigned long size = strtoul(b, NULL, 0), i;
            const uint8_t *p = (const uint8_t *)fnvnx_image_ptr(img, (uint32_t)va, (uint32_t)size);
            for (i = 0; p && i < size && !p[i]; i++) {}
            if (!p || i != size) { fprintf(stderr, "FAIL zero 0x%08lx+0x%lx\n", va, size); fails++; }
        } else if (!strcmp(kind, "unmapped")) {
            if (fnvnx_image_ptr(img, (uint32_t)va, 1)) { fprintf(stderr, "FAIL unmapped 0x%08lx\n", va); fails++; }
        } else if (!strcmp(kind, "stat") && n == 3) {
            want = strtoul(b, NULL, 0);
            if (!stat_get(a, &v)) v = 0;
            if (v != want) { fprintf(stderr, "FAIL stat %s: got %lu want %lu\n", a, v, want); fails++; }
        } else {
            fprintf(stderr, "bad expect line: %s", line);
            fails++;
        }
    }
    fclose(f);
    printf("expect_lines=%d expect_failures=%d\n", lines, fails);
    return fails ? 1 : 0;
}

static void usage(void) {
    fprintf(stderr, "usage: loadcheck --exe FILE [options] (see the source header)\n");
    exit(2);
}

int main(int argc, char **argv) {
    const char *exe = NULL, *probes_path = NULL, *map_path = NULL, *hints_path = NULL, *imports_path = NULL;
    const char *fx_in = NULL, *fx_out = NULL, *expect = NULL, *dprobes_path = NULL;
    int code_ok = -1, data_ok = -1, data_plain = 1;
    uint32_t dump_vt = 0;
    uint32_t window_lo = 0x00400000u, window_hi = 0x02100000u, load_base = 0, min_conf = FNVNX_CONF_LOW;
    unsigned flags = 0, scan_flags = 0;
    int use_trap = 0, verbose = 0, i, r, rc = 0;
    fnvnx_host host;
    fnvnx_os os;
    static fnvnx_pe pe;
    static fnvnx_image img;
    fnvnx_codemap map = {NULL, 0};
    void *map_blob = NULL;
    uint32_t *hints = NULL, nhints = 0;
    fnvnx_fixup *fx = NULL;
    uint32_t nfx = 0;

    for (i = 1; i < argc; i++) {
        const char *a = argv[i];
#define ARG() (i + 1 < argc ? argv[++i] : (usage(), (char *)0))
        if (!strcmp(a, "--exe")) exe = ARG();
        else if (!strcmp(a, "--probes")) probes_path = ARG();
        else if (!strcmp(a, "--data-probes")) dprobes_path = ARG();
        else if (!strcmp(a, "--addrmap")) map_path = ARG();
        else if (!strcmp(a, "--hints")) hints_path = ARG();
        else if (!strcmp(a, "--imports")) imports_path = ARG();
        else if (!strcmp(a, "--fixups-in")) fx_in = ARG();
        else if (!strcmp(a, "--fixups-out")) fx_out = ARG();
        else if (!strcmp(a, "--expect")) expect = ARG();
        else if (!strcmp(a, "--min-conf")) min_conf = (uint32_t)strtoul(ARG(), NULL, 0);
        else if (!strcmp(a, "--load-base")) load_base = (uint32_t)strtoul(ARG(), NULL, 0);
        else if (!strcmp(a, "--window")) {
            char *s = ARG(), *colon = strchr(s, ':');
            if (!colon) usage();
            window_lo = (uint32_t)strtoul(s, NULL, 0);
            window_hi = (uint32_t)strtoul(colon + 1, NULL, 0);
        } else if (!strcmp(a, "--headers")) flags |= FNVNX_MAP_HEADERS;
        else if (!strcmp(a, "--map-code")) flags |= FNVNX_MAP_CODE;
        else if (!strcmp(a, "--no-relocs")) scan_flags |= FNVNX_SCAN_NO_RELOCS;
        else if (!strcmp(a, "--no-index")) scan_flags |= FNVNX_SCAN_NO_INDEX;
        else if (!strcmp(a, "--trap")) use_trap = 1;
        else if (!strcmp(a, "--dump-vtable")) dump_vt = (uint32_t)strtoul(ARG(), NULL, 0);
        else if (!strcmp(a, "-v")) verbose = 1;
        else usage();
    }
    if (!exe) usage();

    if ((r = fnvnx_host_open(&host, exe, window_lo, window_hi, &os))) {
        fprintf(stderr, "open: %s\n", fnvnx_strerror(r));
        return 3;
    }
    host.verbose = verbose;
    if ((r = fnvnx_pe_open(&pe, &os, host.file_size))) {
        printf("pe_open=%d\n", r);
        fprintf(stderr, "pe: %s\n", fnvnx_strerror(r));
        return 3;
    }
    printf("pe_open=0 sections=%u image_base=0x%08x size_of_image=0x%x text=0x%08x..0x%08x\n", pe.nsections,
           pe.image_base, pe.size_of_image, pe.image_base + pe.text_lo_rva, pe.image_base + pe.text_hi_rva);

    if (probes_path) {
        FILE *f = fopen(probes_path, "r");
        fnvnx_probe pr[64];
        unsigned n = 0, matched = 0;
        char line[256], hex[80];
        unsigned long va, size;
        if (!f) { fprintf(stderr, "cannot open %s\n", probes_path); return 2; }
        while (n < 64 && fgets(line, sizeof line, f))
            if (sscanf(line, "%lx %lu %79s", &va, &size, hex) == 3 && parse_hex(hex, pr[n].sha256, 32) == 32) {
                pr[n].va = (uint32_t)va; pr[n].size = (uint32_t)size; n++;
            }
        fclose(f);
        r = fnvnx_pe_check_probes(&pe, &os, pr, n, 0, &matched);
        stat_set("probes_matched", matched);
        printf("probes=%u matched=%u\n", n, matched);
        code_ok = r == FNVNX_OK;
    }
    stat_set("wrapped", (unsigned long)pe.wrapped);

    if (!load_base) load_base = pe.image_base;
    if ((r = fnvnx_image_map(&img, &pe, &os, load_base, host.bias, window_lo, window_hi, flags))) {
        fprintf(stderr, "map: %s\n", fnvnx_strerror(r));
        return 5;
    }
    stat_set("sections_mapped", img.nmapped);
    if (flags & FNVNX_MAP_CODE) {   /* code sections read as data: the file's bytes, read-only, never executable */
        unsigned k, ncode = 0, match = 1, prot_ok = 1;
        for (k = 0; k < img.nmapped; k++) {
            const fnvnx_mapping *m = &img.map[k];
            if (!m->code) continue;
            ncode++;
            prot_ok &= m->prot == FNVNX_PROT_R;
            uint8_t *want = (uint8_t *)malloc(m->file_bytes ? m->file_bytes : 1);
            if (!want || fnvnx_pe_read_rva(&pe, &os, pe.sec[m->sec].rva, want, m->file_bytes) ||
                memcmp(want, fnvnx_image_ptr(&img, m->va, m->file_bytes), m->file_bytes))
                match = 0;
            free(want);
        }
        printf("code_map=%u match=%u prot_r=%u\n", ncode, match, prot_ok);
    }
    {   /* are the sections we use plain? */
        unsigned k;
        for (k = 0; k < img.nmapped; k++) {
            fnvnx_section_stats ss;
            unsigned sec = img.map[k].sec;
            if (FNVNX_MAPPING_SKIP(&img.map[k]) || !strcmp(pe.sec[sec].name, ".rsrc")) continue;
            if (fnvnx_pe_section_stats(&pe, &os, sec, &ss)) return 3;
            printf("section %-8s raw=0x%x zeros=%u chi2=%llu plain=%d\n", pe.sec[sec].name, ss.bytes, ss.zeros,
                   (unsigned long long)ss.chi2, ss.plain);
            data_plain &= ss.plain;
        }
        stat_set("data_plain", (unsigned long)data_plain);
    }
    if (dprobes_path) {
        FILE *f = fopen(dprobes_path, "r");
        static fnvnx_bytes_probe bp[65536];
        static uint8_t pool[1 << 20];
        static char line[2200];
        uint32_t n = 0, used = 0, matched = 0;
        if (!f) { fprintf(stderr, "cannot open %s\n", dprobes_path); return 2; }
        while (n < 65536 && fgets(line, sizeof line, f)) {
            char hex[2100];
            unsigned long va;
            int len;
            if (sscanf(line, "%lx %2099s", &va, hex) != 2) continue;
            if (used + 1024 > sizeof pool) break;
            len = parse_hex(hex, pool + used, 1024);
            if (len <= 0) continue;
            bp[n].va = (uint32_t)va; bp[n].len = (uint32_t)len; bp[n].bytes = pool + used;
            used += (uint32_t)len;
            n++;
        }
        fclose(f);
        data_ok = fnvnx_image_check_bytes(&img, bp, n, 90, &matched) == FNVNX_OK;
        stat_set("data_probes_matched", matched);
        printf("data_probes=%u matched=%u\n", n, matched);
    }
    if (!data_plain) {
        fprintf(stderr, "image: %s\n", fnvnx_strerror(FNVNX_E_DRM));
        return 4;
    }
    if (code_ok == 0 && data_ok != 1) {
        fprintf(stderr, "image: %s%s\n", fnvnx_strerror(FNVNX_E_PROBES),
                pe.wrapped ? " (wrapped exe: pass --data-probes to verify its data instead)" : "");
        return 4;
    }
    printf("verdict=%s\n", code_ok == 1 ? "code-and-data-checked" : data_ok == 1 ? "data-checked" : "unchecked");
    {
        uint32_t tva = 0, told = 0, tnow = 0;
        r = fnvnx_image_tls_index_reset(&img, &tva, &told);
        if (tva && r == FNVNX_OK) memcpy(&tnow, (const void *)((uintptr_t)tva + img.bias), 4);
        printf("tls_index=0x%08x old=0x%08x now=0x%08x rc=%d\n", tva, told, tnow, r);
        {
            uint32_t cbs[8], ncb = 0;
            int cr = fnvnx_image_tls_callbacks(&img, cbs, 8, &ncb);
            printf("tls_callbacks=%u first=0x%08x rc=%d\n", ncb, ncb ? cbs[0] : 0, cr);
        }
    }
    if ((r = fnvnx_image_load_relocs(&img, &os))) { fprintf(stderr, "relocs: %s\n", fnvnx_strerror(r)); return 5; }
    stat_set("relocs", img.nrelocs);
    {
        uint32_t applied = 0;
        if ((r = fnvnx_image_relocate(&img, &os, &applied))) {
            fprintf(stderr, "relocate: %s\n", fnvnx_strerror(r));
            return 5;
        }
        stat_set("relocs_applied", applied);
    }

    if (imports_path) {
        FILE *f = fopen(imports_path, "r");
        char line[256];
        imps = (imp *)calloc(4096, sizeof *imps);
        while (f && nimps < 4096 && fgets(line, sizeof line, f)) {
            char v[32];
            if (sscanf(line, "%63s %95s %31s", imps[nimps].dll, imps[nimps].name, v) == 3)
                imps[nimps++].value = (uint32_t)strtoul(v, NULL, 0);
        }
        if (f) fclose(f);
    }
    {
        fnvnx_import_stats st;
        if ((r = fnvnx_imports_bind(&img, resolver, NULL, 0xdead0000u, &st))) {
            fprintf(stderr, "imports: %s\n", fnvnx_strerror(r));
            return 6;
        }
        stat_set("imp_dlls", st.dlls); stat_set("imp_slots", st.slots); stat_set("imp_resolved", st.resolved);
        stat_set("imp_unresolved", st.unresolved); stat_set("delay_dlls", st.delay_dlls);
        stat_set("delay_slots", st.delay_slots);
    }

    if (map_path) {
        uint32_t size;
        map_blob = slurp(map_path, &size);
        if (!map_blob || (r = fnvnx_codemap_from_blob(&map, map_blob, size))) {
            fprintf(stderr, "addrmap: %s\n", map_blob ? fnvnx_strerror(r) : "cannot read");
            return 2;
        }
        stat_set("addrmap_entries", map.n);
    }
    if (hints_path) {
        FILE *f = fopen(hints_path, "r");
        char line[64];
        hints = (uint32_t *)malloc(65536 * 4);
        while (f && nhints < 65536 && fgets(line, sizeof line, f))
            if (line[0] != '#' && line[0] != '\n') hints[nhints++] = (uint32_t)strtoul(line, NULL, 0);
        if (f) fclose(f);
    }

    if (dump_vt) {   /* before any fixup: the slots still hold original addresses */
        uint32_t slots[256], ns = 0, k;
        fnvnx_image_vtable_slots(&img, dump_vt, slots, 256, &ns);
        printf("vtable 0x%08x slots=%u", dump_vt, ns);
        for (k = 0; k < ns; k++) printf(" 0x%08x", slots[k]);
        printf("\n");
    }
    if (fx_in) {
        uint32_t size;
        void *blob = slurp(fx_in, &size);
        if (!blob) { fprintf(stderr, "cannot read %s\n", fx_in); return 2; }
        fnvnx_fixups_parse(&pe, blob, size, NULL, 0, &nfx);
        fx = (fnvnx_fixup *)malloc((nfx ? nfx : 1) * sizeof *fx);
        if ((r = fnvnx_fixups_parse(&pe, blob, size, fx, nfx, &nfx))) {
            fprintf(stderr, "fixups: %s\n", fnvnx_strerror(r));
            return 2;
        }
        free(blob);
        stat_set("fixups_loaded", nfx);
    } else {
        fnvnx_scan_opts so;
        static fnvnx_scan_stats ss;
        unsigned k;
        so.vtable_hints = hints; so.nhints = nhints; so.flags = scan_flags;
        if ((r = fnvnx_scan_codeptrs(&img, &os, &map, &so, NULL, 0, &nfx, &ss))) {
            fprintf(stderr, "scan: %s\n", fnvnx_strerror(r));
            return 7;
        }
        fx = (fnvnx_fixup *)malloc((nfx ? nfx : 1) * sizeof *fx);
        if ((r = fnvnx_scan_codeptrs(&img, &os, &map, &so, fx, nfx, &nfx, &ss))) {
            fprintf(stderr, "scan: %s\n", fnvnx_strerror(r));
            return 7;
        }
        stat_set("scan_words", ss.words); stat_set("scan_textptrs", ss.textptrs);
        stat_set("vtables_col", ss.vtables_col); stat_set("vtables_hint", ss.vtables_hint);
        stat_set("vtable_slots", ss.vtable_slots); stat_set("vptr_refs", ss.vptr_refs);
        for (k = 0; k < 256; k++) {
            char key[48];
            if (!class_name(k)) continue;
            snprintf(key, sizeof key, "fx_%s", class_name(k));
            stat_set(key, ss.by_class[k]);
        }
        stat_set("fx_total", nfx);
        if (fx_out) {
            uint32_t need = fnvnx_fixups_serialize(&pe, fx, nfx, NULL, 0);
            void *buf = malloc(need);
            FILE *f = fopen(fx_out, "wb");
            fnvnx_fixups_serialize(&pe, fx, nfx, buf, need);
            if (!f || fwrite(buf, 1, need, f) != need) { fprintf(stderr, "cannot write %s\n", fx_out); return 2; }
            fclose(f);
            free(buf);
        }
    }
    {
        fnvnx_apply_stats as;
        fnvnx_apply_hooks hk;
        hk.ctx = NULL; hk.trap = use_trap ? trap_fn : NULL; hk.deldtor = deldtor_token;
        r = fnvnx_fixups_apply(&img, &map, fx, nfx, min_conf, &hk, &as);
        stat_set("apply_applied", as.applied); stat_set("apply_unresolved", as.unresolved);
        stat_set("apply_trapped", as.trapped); stat_set("apply_mismatched", as.mismatched);
        stat_set("apply_skipped", as.skipped); stat_set("apply_adapted", as.adapted);
        if (r) fprintf(stderr, "apply: %s\n", fnvnx_strerror(r));
    }
    if ((r = fnvnx_image_finalize(&img, &os))) { fprintf(stderr, "finalize: %s\n", fnvnx_strerror(r)); rc = 8; }
    stat_set("protect_calls", host.protect_calls);
    if (expect && check_expect(expect, &img)) rc = 1;   /* the checks only read */
    for (i = 0; i < (int)nstats; i++) printf("%s=%lu\n", stats[i].key, stats[i].val);
    fnvnx_image_release(&img, &os);
    fnvnx_host_close(&host);
    free(fx); free(hints); free(map_blob); free(imps);
    return rc;
}
