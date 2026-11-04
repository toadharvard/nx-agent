#!/usr/bin/env python3
"""scenes.py - the fixed scene set (scenes.json) as console scripts, and console output parsed into scene state.

    scenes.py list   <scenes.json>                          scene ids
    scenes.py get    <scenes.json> <id> <key>               one field (0 when missing)
    scenes.py chunks <scenes.json> <id> <dir> <prefix>      writes <dir>/<prefix>_<k>.txt (one `bat` file per run
                                                            of commands between wait:<s> markers) and prints
                                                            "<k> <seconds to wait after it>" per chunk
    scenes.py state  <scenes.json> <id> <dir> <prefix>      writes <dir>/<prefix>_state.txt
    scenes.py parse  <scenes.json> <id> <console.txt> <state.json> [--esm-state <esm cell state>]
                                                            console output (scof) -> fnvnx-scenestate/1
"""
import json, re, sys

def load(p, sid):
    d = json.load(open(p))
    for s in d['scenes']:
        if s['id'] == sid: return d, s
    raise SystemExit(f'no scene {sid!r}')

def chunks(d, s):
    """[(lines, wait seconds)]: common_pre + cmds + common_post, cut at wait:<s>"""
    if s.get('menu'): return []
    out, cur = [], list(d.get('common_pre', []))
    for c in s['cmds']:
        m = re.fullmatch(r'wait:(\d+)', c)
        if m: out.append((cur, int(m.group(1)))); cur = []
        else: cur.append(c)
    cur += d.get('common_post', [])
    out.append((cur, 0))
    return out

def state_lines(d, s):
    return [l.format(cell=s.get('cell', '')) for l in d.get('state', []) if s.get('cell') or '{cell}' not in l]

NUM = r'(-?\d+(?:\.\d+)?)'
def parse_console(text):
    """the values the state script prints, from the console log (FNV prints 'GetPos: X >> 1.23' and
    'GameHour = 12.0000' style lines)"""
    v = {}
    for k, rx in (('pos_x', r'GetPos:\s*X\s*>>\s*' + NUM), ('pos_y', r'GetPos:\s*Y\s*>>\s*' + NUM),
                  ('pos_z', r'GetPos:\s*Z\s*>>\s*' + NUM), ('rot_x', r'GetAngle:\s*X\s*>>\s*' + NUM),
                  ('rot_z', r'GetAngle:\s*Z\s*>>\s*' + NUM), ('gamehour', r'GameHour\s*=\s*' + NUM),
                  ('timescale', r'TimeScale\s*=\s*' + NUM), ('incell', r'GetInCell\s*>>\s*' + NUM)):
        m = re.findall(rx, text, re.I)
        if m: v[k] = float(m[-1])
    return v

def main():
    a = sys.argv[1:]
    if not a: print(__doc__); return 2
    cmd, p = a[0], a[1]
    if cmd == 'list':
        print('\n'.join(s['id'] for s in json.load(open(p))['scenes'])); return 0
    d, s = load(p, a[2])
    if cmd == 'get':
        print(s.get(a[3], 0)); return 0
    if cmd == 'chunks':
        for k, (lines, w) in enumerate(chunks(d, s)):
            open(f'{a[3]}/{a[4]}_{k}.txt', 'w', newline='').write('\r\n'.join(lines) + '\r\n')
            print(k, w)
        return 0
    if cmd == 'state':
        open(f'{a[3]}/{a[4]}_state.txt', 'w', newline='').write('\r\n'.join(state_lines(d, s)) + '\r\n'); return 0
    if cmd == 'parse':
        v = parse_console(open(a[3], encoding='latin-1', errors='replace').read())
        st = {'format': 'fnvnx-scenestate/1', 'source': 'wine-ref-console', 'scene': s['id'], 'refs': None}
        if 'esm-state' in ' '.join(a) and '--esm-state' in a:
            e = json.load(open(a[a.index('--esm-state') + 1])); st['cell'] = e['cell']; st['plugins'] = e['plugins']
        if 'pos_x' in v:
            st['camera'] = {'pos': [v['pos_x'], v.get('pos_y'), v.get('pos_z')],
                            'rot_deg': [v.get('rot_x'), 0.0, v.get('rot_z')], 'fov': 75.0, 'frame': 'player'}
        if 'gamehour' in v: st['time'] = {'gamehour': v['gamehour'], 'timescale': v.get('timescale')}
        if 'incell' in v: st['in_cell'] = bool(v['incell'])
        json.dump(st, open(a[4], 'w'), indent=1); return 0
    print(__doc__); return 2

if __name__ == '__main__':
    sys.exit(main())
