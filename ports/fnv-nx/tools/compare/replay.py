#!/usr/bin/env python3
"""replay.py - render NXD9 captures on both sides of S16-compare: the reference (nxd9play.exe under Wine, WineD3D on
llvmpipe) and the candidate (nx_render d3d9, the native renderer on its GL backend). One run directory per side:

    replay.py ref  --exe <nxd9play.exe> --prefix <wine prefix> --out <run dir> <capture.nxd9>...
    replay.py cand --bin <nx_render> --out <run dir> [--shaders <dir>] <capture.nxd9>...

<run dir>/<capture name>/f<frame %05u>.png and summary.txt per capture (the renderers' own names), plus
<run dir>/run.json: per capture the exit code, seconds, and the renderer's counters line. Extra arguments for every
capture after `--` (e.g. `-- --per-draw`). The reference needs wine (32-bit), Xvfb and Mesa's i386 llvmpipe; a
private X server is started on a free display (S16_DISPLAY overrides) and stopped at the end. The prefix is created
on first use (wineboot). Exit 0 when every capture rendered (nx_render's 3, "some draws skipped", counts as rendered).
"""
import argparse, json, os, shutil, subprocess, sys, time


def wine_path(p):
    return 'Z:' + os.path.abspath(p).replace('/', '\\')


def free_display():
    if os.environ.get('S16_DISPLAY'):
        return os.environ['S16_DISPLAY']
    for n in range(90, 200):
        if not os.path.exists(f'/tmp/.X11-unix/X{n}') and not os.path.exists(f'/tmp/.X{n}-lock'):
            return f':{n}'
    raise SystemExit('replay: no free X display')


def wine_env(prefix, display):
    e = dict(os.environ)
    e.update(WINEPREFIX=os.path.abspath(prefix), WINEARCH='win32', WINEDEBUG=e.get('WINEDEBUG', '-all'),
             WINEDLLOVERRIDES='mscoree,mshtml=;winemenubuilder.exe=d', DISPLAY=display,
             LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe')
    e.setdefault('LP_NUM_THREADS', str(min(4, os.cpu_count() or 1)))
    return e


class Xvfb:
    def __init__(self, w=1280, h=1024):
        self.display = free_display()
        self.p = subprocess.Popen(['Xvfb', self.display, '-screen', '0', f'{w}x{h}x24', '-nolisten', 'tcp', '-noreset'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if os.path.exists(f'/tmp/.X11-unix/X{self.display[1:]}'): break
            time.sleep(0.1)

    def close(self):
        self.p.terminate()
        try: self.p.wait(5)
        except subprocess.TimeoutExpired: self.p.kill()


def counters(d):
    p = os.path.join(d, 'summary.txt')
    return open(p).read().strip() if os.path.isfile(p) else ''


def run_all(caps, out, make_cmd, env=None, timeout=600):
    os.makedirs(out, exist_ok=True)
    res = {}
    for c in caps:
        name = os.path.splitext(os.path.basename(c))[0]
        d = os.path.join(out, name)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        t0 = time.time()
        try:
            r = subprocess.run(make_cmd(c, d), env=env, capture_output=True, text=True, timeout=timeout)
            rc, err = r.returncode, (r.stderr or '').strip().splitlines()[-3:]
        except subprocess.TimeoutExpired:
            rc, err = -1, ['timeout']
        res[name] = {'rc': rc, 'seconds': round(time.time() - t0, 2), 'summary': counters(d),
                     'frames': len([f for f in os.listdir(d) if f.startswith('f') and f.endswith('.png') and '_d' not in f]),
                     'stderr': err if rc not in (0, 3) else []}
    return res


def main():
    argv = sys.argv[1:]
    extra = []
    if '--' in argv:
        i = argv.index('--'); extra = argv[i + 1:]; argv = argv[:i]
    ap = argparse.ArgumentParser()
    ap.add_argument('side', choices=['ref', 'cand'])
    ap.add_argument('--exe'); ap.add_argument('--prefix'); ap.add_argument('--bin'); ap.add_argument('--shaders')
    ap.add_argument('--out', required=True); ap.add_argument('--timeout', type=int, default=600)
    ap.add_argument('captures', nargs='+')
    a = ap.parse_args(argv)
    caps = [os.path.abspath(c) for c in a.captures]
    meta = {'side': a.side, 'started': int(time.time())}
    if a.side == 'ref':
        if not (a.exe and a.prefix): ap.error('ref needs --exe and --prefix')
        x = Xvfb()
        try:
            env = wine_env(a.prefix, x.display)
            if not os.path.isfile(os.path.join(a.prefix, 'system.reg')):
                subprocess.run(['wineboot', '-i'], env=env, capture_output=True, timeout=600)
                subprocess.run(['wineserver', '-w'], env=env, timeout=600)
            exe = os.path.abspath(a.exe)
            res = run_all(caps, a.out, lambda c, d: ['wine', exe, wine_path(c), '-o', wine_path(d)] + extra, env, a.timeout)
            subprocess.run(['wineserver', '-k'], env=env, capture_output=True)
            meta['versions'] = {'wine': subprocess.run(['wine', '--version'], capture_output=True, text=True).stdout.strip(),
                                'mesa': gl_version()}
        finally:
            x.close()
    else:
        if not a.bin: ap.error('cand needs --bin')
        sh = ['--shaders', a.shaders] if a.shaders else []
        res = run_all(caps, a.out, lambda c, d: [a.bin, 'd3d9', c, '-o', d] + sh + extra, None, a.timeout)
        meta['versions'] = {'mesa': gl_version()}
    meta['captures'] = res
    json.dump(meta, open(os.path.join(a.out, 'run.json'), 'w'), indent=1)
    bad = {k: v for k, v in res.items() if v['rc'] not in (0, 3)}
    for k, v in res.items():
        print(f"{a.side} {k}: rc {v['rc']} {v['seconds']} s frames {v['frames']} {v['summary']}"
              + (f" {v['stderr']}" if v['stderr'] else ''))
    return 1 if bad else 0


def gl_version():
    try:
        r = subprocess.run(['dpkg-query', '-W', '-f=${Version}', 'libgl1-mesa-dri'], capture_output=True, text=True)
        return r.stdout.strip()
    except OSError:
        return ''


if __name__ == '__main__':
    sys.exit(main())
