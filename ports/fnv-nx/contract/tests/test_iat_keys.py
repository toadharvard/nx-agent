#!/usr/bin/env python3
"""Synthetic check of the import-key table: keys.tsv, import_keys.c + loader/src/importkey.c through a C driver."""
import contextlib, hashlib, os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import iat


@contextlib.contextmanager
def scratch_dir():
    d = tempfile.mkdtemp()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)

LOADER = os.path.join(HERE, '..', '..', 'loader')
fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r, want %r' % (name, got, want))


HDR = 'dll\tname\tordinal\tconv\tret\targs\tnative_symbol\tbind\n'
ROWS = [('wsock32.dll', 'send', '19', 'stdcall', 'i', 'iiii', 'my_send', 'sym'),
        ('wsock32.dll', 'recv', '16', 'stdcall', 'i', 'iiii', 'my_recv', 'sym'),
        ('KERNEL32.dll', 'Sleep', '', 'stdcall', 'v', 'i', 'Sleep', 'sym'),
        ('kernel32.dll', 'sleep', '', 'stdcall', 'v', 'i', 'sleep2', 'sym'),
        ('user32', 'Sleep', '', 'stdcall', 'v', 'i', 'usleep', 'sym'),
        ('msvcrt.dll', '_iob', '', 'data', 'i', '', 'my_iob', 'data'),
        ('msvcrt.dll', '??2@YAPAXI@Z', '', 'cdecl', 'i', 'i', '-', 'lookup'),
        ('msvcrt.dll', '_pioinfo', '', 'data', 'i', '', '-', 'data')]


def write_bridge(d, rows, name='bridge.tsv'):
    p = os.path.join(d, name)
    with open(p, 'w', newline='\n') as fh:
        fh.write(HDR + ''.join('\t'.join(r) + '\n' for r in rows))
    return p


def stage_error(d, rows):
    try:
        iat.bridge_rows(write_bridge(d, rows, 'bad.tsv'))
    except ValueError:
        return True
    return False


with scratch_dir() as d:
    bp = write_bridge(d, ROWS)
    rows, tid = iat.bridge_rows(bp)
    check('table id', tid, hashlib.sha256(open(bp, 'rb').read()).hexdigest())
    ktxt = iat.keys_tsv(rows, tid)
    lines = ktxt.splitlines()
    check('keys header', lines[0], '# table_id ' + tid)
    check('keys rows', len(lines) - 1, 6)
    ks = [l.split('\t') for l in lines[1:]]
    check('keys sorted dense', [int(k[0], 16) for k in ks], [iat.IMPORT_BASE + iat.STEP * i for i in range(6)])
    check('keys order', [(k[1], k[2]) for k in ks][:3], [('kernel32.dll', 'Sleep'), ('kernel32.dll', 'sleep'), ('msvcrt.dll', '??2@YAPAXI@Z')])
    check('keys cols', ks[2], ['00100020', 'msvcrt.dll', '??2@YAPAXI@Z', '', 'cdecl', 'i', 'i', '-'])
    check('user32 normalised', [k[1] for k in ks if k[2] == 'Sleep'], ['kernel32.dll', 'user32.dll'])
    ctxt, skipped = iat.c_source(rows, tid)
    check('skipped data rows', skipped, 1)
    check('deterministic', iat.c_source(rows, tid), (ctxt, skipped))
    check('order independent', iat.c_source(iat.bridge_rows(write_bridge(d, ROWS[::-1], 'rev.tsv'))[0], tid)[0].replace(tid, ''), ctxt.replace(tid, ''))
    check('table id in C', tid in ctxt, True)
    for bad, why in ((ROWS + [ROWS[2]], 'duplicate (dll,name)'),
                     (ROWS + [('wsock32.dll', 'other', '19', 'stdcall', 'i', '', 'o', 'sym')], 'same (dll, ordinal)'),
                     (ROWS + [('x.dll', 'f', '', 'data', 'i', '', 'a-b', 'data')], 'data symbol not an identifier')):
        try:
            r2, _ = iat.bridge_rows(write_bridge(d, bad, 'bad.tsv'))
            iat.c_source(r2, tid)
            fails.append('accepted: ' + why)
        except ValueError:
            pass
    check('same name in two dlls, one ordinal in two dlls', stage_error(d, ROWS + [('user32.dll', 'send', '19', 'stdcall', 'i', '', 's', 'sym')]), False)

    cc = shutil.which('cc') or shutil.which('gcc')
    if cc is None:
        fails.append('no host C compiler')
    else:
        open(os.path.join(d, 'import_keys.c'), 'w').write(ctxt)
        drv = os.path.join(d, 'drv.c')
        want = {(k[1], k[2]): int(k[0], 16) for k in ks}
        T = ['    {%s, %s, %du, 0x%08xu},\n' % ('"%s"' % dll, '"%s"' % n.replace('?', '\\?'), o, v) for (dll, n), v, o in
             ((k, v, int(next(r['ordinal'] for r in rows if (r['dll'], r['name']) == k) or 0xffffffff)) for k, v in sorted(want.items()))]
        open(drv, 'w').write('''#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "fnvnx_importkey.h"
char my_iob[4];
int my_send, my_recv;
static const struct { const char *dll, *name; uint32_t ord, key; } T[] = {
%s};
static int bad;
static void eq(const char *what, uint32_t got, uint32_t want) { if (got != want) { printf("%%s: %%x != %%x\\n", what, got, want); bad++; } }
int main(void) {
    size_t i;
    for (i = 0; i < sizeof T / sizeof T[0]; i++) {
        char up[64]; size_t j;
        eq(T[i].name, fnvnx_import_key(T[i].dll, T[i].name, FNVNX_BY_NAME), T[i].key);
        for (j = 0; T[i].dll[j]; j++) up[j] = (T[i].dll[j] >= 'a' && T[i].dll[j] <= 'z') ? (char)(T[i].dll[j] - 32) : T[i].dll[j];
        up[j] = 0;
        eq("upper", fnvnx_import_key(up, T[i].name, FNVNX_BY_NAME), T[i].key);
        up[j - 4] = 0;
        eq("no .dll", fnvnx_import_key(up, T[i].name, FNVNX_BY_NAME), T[i].key);
        if (T[i].ord != 0xffffffffu) {
            eq("by ordinal", fnvnx_import_key(T[i].dll, NULL, T[i].ord), T[i].key);
            eq("ordinal wrong dll", fnvnx_import_key("kernel32.dll", NULL, T[i].ord), 0);
        }
        eq("by key", fnvnx_import_by_key(T[i].key) != NULL && strcmp(fnvnx_import_by_key(T[i].key)->name, T[i].name) == 0, 1);
    }
    eq("case of name matters", fnvnx_import_key("kernel32.dll", "SLEEP", FNVNX_BY_NAME), 0);
    eq("unknown name", fnvnx_import_key("kernel32.dll", "Nope", FNVNX_BY_NAME), 0);
    eq("unknown dll", fnvnx_import_key("nope.dll", "Sleep", FNVNX_BY_NAME), 0);
    eq("unknown ordinal", fnvnx_import_key("wsock32.dll", NULL, 77), 0);
    eq("no name no ordinal", fnvnx_import_key("wsock32.dll", NULL, FNVNX_BY_NAME), 0);
    eq("NULL dll", fnvnx_import_key(NULL, "Sleep", FNVNX_BY_NAME), 0);
    eq("dll too long", fnvnx_import_key("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "Sleep", FNVNX_BY_NAME), 0);
    eq("data row", (uint32_t)(uintptr_t)my_iob == (uint32_t)(uintptr_t)(void *)my_iob, 1);
    { const fnvnx_import_row *r = NULL; size_t k;
      for (k = 0; k < fnvnx_import_row_count; k++) if (!strcmp(fnvnx_import_rows[k].name, "_iob")) r = &fnvnx_import_rows[k];
      eq("data kind", r != NULL && r->kind == FNVNX_IMPORT_DATA && r->value == (uintptr_t)my_iob, 1);
      eq("data lookup", fnvnx_import_key("MSVCRT", "_iob", FNVNX_BY_NAME), (uint32_t)(uintptr_t)my_iob); }
    eq("pioinfo has no object", fnvnx_import_key("msvcrt.dll", "_pioinfo", FNVNX_BY_NAME), 0);
    eq("below base", fnvnx_import_by_key(0x000fff0) == NULL, 1);
    eq("unaligned", fnvnx_import_by_key(FNVNX_IMPORT_BASE + 4) == NULL, 1);
    eq("past end", fnvnx_import_by_key(FNVNX_IMPORT_BASE + FNVNX_IMPORT_STEP * (uint32_t)fnvnx_import_key_count) == NULL, 1);
    eq("count", (uint32_t)fnvnx_import_key_count, 6);
    eq("id", strcmp(fnvnx_import_table_id, "%s") == 0, 1);
    printf(bad ? "FAIL\\n" : "ok\\n");
    return bad != 0;
}
''' % (''.join(T), tid))
        exe = os.path.join(d, 'drv')
        p = subprocess.run([cc, '-std=c99', '-Wall', '-Wextra', '-Werror', '-I' + os.path.join(LOADER, 'include'), drv,
                            os.path.join(d, 'import_keys.c'), os.path.join(LOADER, 'src', 'importkey.c'), '-o', exe],
                           capture_output=True, text=True)
        if p.returncode:
            fails.append('C compile: ' + p.stderr[-800:])
        else:
            p = subprocess.run([exe], capture_output=True, text=True)
            if p.returncode or p.stdout.strip() != 'ok':
                fails.append('C driver: ' + p.stdout[-800:])
for f in fails:
    print('FAIL', f)
print('ok' if not fails else 'FAILED %d' % len(fails))
sys.exit(1 if fails else 0)
