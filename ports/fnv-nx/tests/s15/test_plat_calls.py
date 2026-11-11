#!/usr/bin/env python3
"""Self-test of tools/perf/plat_calls.py (S4's Win32 call baseline): the import directory of a hand-built PE
(by name and by ordinal), `list` from it (CreateFileA at every call, ordinals left out), and `report` on a synthetic
fnvtrace log with API entries: logged calls, `count` lines past the cap, the detach counts (exact), first-call order,
threads, return classes, string hashes and no string text. And tests/s5/boot_trace.py leaves API entries out of the
lockstep's call forest. Our own data only.

    python3 -I tests/s15/test_plat_calls.py
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(P, 'tools', 'perf', 'plat_calls.py')
sys.path.insert(0, os.path.join(P, 'tools', 'perf'))
sys.path.insert(0, os.path.join(P, 'tests', 's5'))
import plat_calls  # noqa: E402
import boot_trace  # noqa: E402

fails = 0


def ok(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': ' + str(info)[:600]))
    fails += not cond


def make_pe(path, imports):
    """a minimal PE32: one section at RVA 0x1000 holding the import directory of {dll: [name or ordinal]}"""
    base, sec_rva, sec_off = 0x00400000, 0x1000, 0x200
    data = bytearray()
    dlls = list(imports.items())
    desc_size = 20 * (len(dlls) + 1)
    data += b'\0' * desc_size
    descs = []
    for dll, names in dlls:
        name_rva = sec_rva + len(data)
        data += dll.encode() + b'\0'
        while len(data) % 4:
            data += b'\0'
        thunks = []
        for n in names:
            if isinstance(n, int):
                thunks.append(0x80000000 | n)
            else:
                thunks.append(sec_rva + len(data))
                data += struct.pack('<H', 0) + n.encode() + b'\0'
                while len(data) % 2:
                    data += b'\0'
        while len(data) % 4:
            data += b'\0'
        ilt = sec_rva + len(data)
        data += b''.join(struct.pack('<I', x) for x in thunks) + b'\0' * 4
        iat = sec_rva + len(data)
        data += b''.join(struct.pack('<I', x) for x in thunks) + b'\0' * 4
        descs.append((ilt, name_rva, iat))
    for i, (ilt, name_rva, iat) in enumerate(descs):
        struct.pack_into('<IIIII', data, 20 * i, ilt, 0, 0, name_rva, iat)
    while len(data) % 0x200:
        data += b'\0'
    hdr = bytearray(sec_off)
    hdr[0:2] = b'MZ'
    struct.pack_into('<I', hdr, 0x3C, 0x40)
    pe = 0x40
    hdr[pe:pe + 4] = b'PE\0\0'
    opt = 224
    struct.pack_into('<HHIIIHH', hdr, pe + 4, 0x14C, 1, 0, 0, 0, opt, 0x102)
    struct.pack_into('<H', hdr, pe + 24, 0x10B)
    struct.pack_into('<I', hdr, pe + 24 + 28, base)
    struct.pack_into('<II', hdr, pe + 24 + 96 + 8, sec_rva, desc_size)
    s = pe + 24 + opt
    struct.pack_into('<8sIIII', hdr, s, b'.idata', len(data), sec_rva, len(data), sec_off)
    open(path, 'wb').write(bytes(hdr) + bytes(data))
    return {dll: descs[i][2] + base for i, (dll, _n) in enumerate(dlls)}


def call(seq, tid, name, ret, s0=0x1000):
    return 'call\t%d\t%d\t0\t%s\t7b800000\t0\t0\t%#x\t%s\t0x33fe00\t%#x\n' % (seq, tid, name, s0,
                                                                         '\t'.join(['0'] * 7), ret)


def main():
    with tempfile.TemporaryDirectory() as d:
        exe = os.path.join(d, 'g.exe')
        iat = make_pe(exe, {'KERNEL32.dll': ['CreateFileA', 'Sleep', 'EnterCriticalSection'], 'WSOCK32.dll': [3],
                            'USER32.dll': ['PeekMessageA']})
        e = plat_calls.exe_imports(exe)
        ok('import directory by name and ordinal, dll lower case without .dll',
           [(dll, api) for _s, dll, api in e] == [('kernel32', 'CreateFileA'), ('kernel32', 'Sleep'),
                                                  ('kernel32', 'EnterCriticalSection'), ('wsock32', '#3'),
                                                  ('user32', 'PeekMessageA')], e)
        ok('slots are the IAT addresses', e[0][0] == '0x%08x' % iat['KERNEL32.dll'] and
           e[1][0] == '0x%08x' % (iat['KERNEL32.dll'] + 4), e)
        lst = os.path.join(d, 'list.txt')
        r = subprocess.run([sys.executable, '-I', TOOL, 'list', '--exe', exe, '--out', lst], capture_output=True, text=True)
        lines = [x for x in open(lst).read().splitlines() if not x.startswith('#')] if os.path.isfile(lst) else []
        ok('list: one line per named import, CreateFileA uncapped, no ordinals',
           r.returncode == 0 and lines == ['kernel32!CreateFileA CreateFileA 0', 'kernel32!Sleep',
                                           'kernel32!EnterCriticalSection', 'user32!PeekMessageA'], (r.stderr, lines))
        r = subprocess.run([sys.executable, '-I', TOOL, 'list'], capture_output=True, text=True)
        if not os.environ.get('FNVNX_TABLES'):
            print('skip: list without --exe needs FNVNX_TABLES (private imports.tsv)')
        else:
          ok('list without --exe: imports.tsv', r.returncode == 0 and 'kernel32!CreateFileA CreateFileA 0' in r.stdout
           and r.stdout.count('\n') > 150, r.stdout[:200])

        tsv = os.path.join(d, 'wine.tsv')
        with open(tsv, 'w') as f:
            f.write('# fnvtrace: 4 functions listed, 4 armed\n')
            f.write(call(1, 7, 'Sleep', 0x00ec1000) + 'ret\t1\t7\t0\tSleep\t0\t0\n')
            f.write(call(2, 7, 'CreateFileA', 0x00ec1234) + 'arg\t2\ts0\t%s\n' % (b'Data\\x.bsa\0'.hex()) +
                    'ret\t2\t7\t0\tCreateFileA\t0xffffffff\t0\n')
            f.write(call(3, 9, 'EnterCriticalSection', 0x00ec2000) + 'ret\t3\t9\t0\tEnterCriticalSection\t0x5\t0\n')
            f.write(call(4, 7, 'EnterCriticalSection', 0x00ec2000) + 'ret\t4\t7\t0\tEnterCriticalSection\t0x5\t0\n')
            f.write('count\tEnterCriticalSection\t128\ncount\tEnterCriticalSection\t65536\n')
        out = os.path.join(d, 'calls.json')
        md = os.path.join(d, 'calls.md')
        r = subprocess.run([sys.executable, '-I', TOOL, 'report', tsv, '--exe', exe, '--out', out, '--md', md],
                           capture_output=True, text=True)
        j = json.load(open(out)) if os.path.isfile(out) else {}
        a = {x['api']: x for x in j.get('apis', [])}
        ok('report runs', r.returncode == 0 and j.get('schema') == 'fnvnx-plat-calls/1', r.stdout + r.stderr)
        ok('3 of 5 imports called (ordinal and PeekMessageA not)', j.get('called') == 3 and j.get('imports') == 5 and
           a['user32!PeekMessageA']['calls'] == 0, j)
        ec = a.get('kernel32!EnterCriticalSection', {})
        ok('past the cap: the last count line, not exact without a detach',
           ec.get('calls') == 65536 and ec.get('logged') == 2 and ec.get('exact') is False and ec.get('threads') == 2, ec)
        cf = a.get('kernel32!CreateFileA', {})
        ok('return classes and boot order', cf.get('ret') == {'zero': 0, 'minus_one': 1, 'other': 0} and
           a['kernel32!Sleep']['order'] == 1 and cf.get('order') == 2 and ec.get('order') == 3, (cf, a['kernel32!Sleep']))
        ok('strings as hashes only', cf.get('string_hashes') == [plat_calls.fnv1a(b'Data\\x.bsa')] and
           'x.bsa' not in open(out).read() and 'x.bsa' not in open(md).read(), cf)
        ok('md table', '| 3 | kernel32!EnterCriticalSection | 65536+ | 2 |' in open(md).read(), open(md).read())
        with open(tsv, 'a') as f:
            f.write('count\tSleep\t1\ncount\tCreateFileA\t1\ncount\tEnterCriticalSection\t70000\nend\tdetach\n')
        r = subprocess.run([sys.executable, '-I', TOOL, 'report', tsv, '--exe', exe, '--out', out], capture_output=True,
                           text=True)
        j = json.load(open(out))
        a = {x['api']: x for x in j['apis']}
        ok('detach counts: exact', j['exact'] and a['kernel32!EnterCriticalSection']['calls'] == 70000 and
           a['kernel32!EnterCriticalSection']['exact'], a['kernel32!EnterCriticalSection'])

        # the lockstep leaves API entries out: game A calls the API, which calls back into game B (a WndProc)
        lk = os.path.join(d, 'lock.tsv')
        g = 'call\t%d\t7\t%d\t%s\t%s\t' + '\t'.join(['0'] * 10) + '\t0x33fe00\t0x00ec0000\n'
        open(lk, 'w').write(g % (1, 0, 'A', '00a00000') + g % (2, 1, 'PeekMessageA', '7b812340') +
                            g % (3, 2, 'B', '00a00100') + g % (4, 1, 'C', '00a00200'))
        _calls, order = boot_trace.read_log(lk, 'wine')
        mt = boot_trace.main_thread(order)
        ok('lockstep: API node dropped, its callback under the game caller',
           [c.name for c in mt] == ['A', 'B', 'C'] and [k.name for k in mt[0].kids] == ['B', 'C'],
           [(c.name, [k.name for k in c.kids]) for c in mt])
    # the guest side: list --guest, a guest trace (platform names, binkw32 undecorated), diff in the original's order
    with tempfile.TemporaryDirectory() as d:
        exe = os.path.join(d, 'g.exe')
        make_pe(exe, {'KERNEL32.dll': ['CreateFileA', 'Sleep', 'EnterCriticalSection', 'ReadFile'],
                      'binkw32.dll': ['_BinkOpen@8']})
        gl = os.path.join(d, 'glist.txt')
        subprocess.run([sys.executable, '-I', TOOL, 'list', '--guest', '--exe', exe, '--out', gl], check=True)
        names = [x.strip() for x in open(gl) if x.strip() and not x.startswith('#')]
        ok('list --guest: platform names, hot left out, bink undecorated',
           names == ['CreateFileA', 'Sleep', 'ReadFile', 'BinkOpen'], names)
        wine = os.path.join(d, 'wine.tsv')
        open(wine, 'w').write(call(1, 7, 'CreateFileA', 0x00ec1234) + 'ret\t1\t7\t0\tCreateFileA\t0x44\t0\n' +
                              call(2, 7, 'ReadFile', 0x00ec1234) + 'ret\t2\t7\t0\tReadFile\t0x1\t0\n' +
                              call(3, 7, '_BinkOpen@8', 0x00ec1234) + 'ret\t3\t7\t0\t_BinkOpen@8\t0x9000\t0\n' +
                              call(4, 7, 'EnterCriticalSection', 0x00ec1234))
        gtsv = os.path.join(d, 'guest.tsv')
        g = 'call\t%d\t1\t0\t%s\t-\t' + '\t'.join(['0x0'] * 8) + '\t0x40000\t0x30001000\t0x30001000 _Z1fv [orig 00401000]\n'
        open(gtsv, 'w').write(g % (1, 'CreateFileA') + 'ret\t1\t1\t0\tCreateFileA\t0xffffffff\n' +
                              g % (2, 'BinkOpen') + 'ret\t2\t1\t0\tBinkOpen\t0x0\n' + g % (3, 'Sleep') +
                              'ret\t3\t1\t0\tSleep\t0x0\n')
        oj, gj, dj = (os.path.join(d, x) for x in ('o.json', 'g.json', 'd.json'))
        for tr, out in ((wine, oj), (gtsv, gj)):
            subprocess.run([sys.executable, '-I', TOOL, 'report', tr, '--exe', exe, '--out', out], check=True,
                           capture_output=True)
        ok('guest report: BinkOpen counted as binkw32!_BinkOpen@8',
           {r['api']: r['calls'] for r in json.load(open(gj))['apis']}['binkw32!_BinkOpen@8'] == 1)
        r = subprocess.run([sys.executable, '-I', TOOL, 'diff', oj, gj, '--guest-list', gl, '--out', dj, '--md', dj + '.md'],
                           capture_output=True, text=True)
        j = json.load(open(dj)) if os.path.isfile(dj) else {}
        kinds = {x['api']: x['kind'] for x in j.get('rows', [])}
        ok('diff: CreateFileA other returns, ReadFile missing (first), Bink returns, Sleep guest only, the hot API '
           'not compared', r.returncode == 0 and kinds == {'kernel32!CreateFileA': 'returns', 'kernel32!ReadFile': 'missing',
                                                          'binkw32!_BinkOpen@8': 'returns'} and
           j['guest_only'] == ['kernel32!Sleep'] and j['first_missing']['api'] == 'kernel32!ReadFile' and
           j['compared'] == 3 and j['metric'] == 4, (r.stdout + r.stderr, j))
        _c, order = boot_trace.read_log(gtsv, 'guest')
        ok('lockstep: guest API nodes (no original address) dropped', boot_trace.main_thread(order) == [],
           [c.name for c in boot_trace.main_thread(order)])
    print('test_plat_calls: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
