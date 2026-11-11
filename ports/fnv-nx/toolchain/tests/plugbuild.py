"""plugbuild - the one way the plugin tests build a GCC plugin and compile a fixture with it (no build scripts).

The plugin is built from its tracked source with exactly the argv of verify/recipe.py's `plugin:<name>` stage:
    <host g++-13> -shared -nostdlib -fPIC -fno-rtti -fno-exceptions -O2 -I<target g++-13 -print-file-name=plugin>/include
        crti.o crtbeginS.o SOURCE crtendS.o crtn.o
The fixtures are compiled with the product flags (the flag set of the game's units, less the paths of its tree).
Tests run as `python3 -I FILE` from the checkout root, so a test inserts its own directory in sys.path to import this."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'link'))
import elfobj  # noqa: E402

TARGET = 'aarch64-linux-gnu-g++-13'      # the compiler the plugins are loaded into (verify/env.json)
HOST = 'x86_64-linux-gnu-g++-13'         # the compiler that builds them
TOOLCHAIN = os.path.normpath(os.path.join(HERE, '..'))
PRODUCT_FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-O2', '-fms-extensions', '-fshort-wchar', '-frounding-math', '-fsigned-char',
                 '-fno-strict-aliasing', '-fwrapv', '-fno-delete-null-pointer-checks', '-fcheck-new', '-fno-sized-deallocation',
                 '-fno-lifetime-dse', '-ffp-contract=off', '-fPIC', '-fpermissive', '-Wno-narrowing', '-march=armv8-a+crc+crypto',
                 '-mtune=cortex-a57', '-fno-stack-protector', '-fno-stack-clash-protection', '-mno-outline-atomics',
                 '-U_FORTIFY_SOURCE', '-fno-devirtualize']


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def build(name, tmp, source=None):
    """-> path of tmp/fnvnx_<name>.so (the plugin's name is the file's base name), or raises AssertionError with the diagnostics"""
    src = source or os.path.join(TOOLCHAIN, name, 'fnvnx_%s.cc' % name)
    pdir = run([TARGET, '-print-file-name=plugin']).stdout.strip()
    so = os.path.join(tmp, 'fnvnx_%s.so' % name)
    crt = [run([HOST, '-print-file-name=' + n]).stdout.strip() for n in ('crti.o', 'crtbeginS.o')]
    end = [run([HOST, '-print-file-name=' + n]).stdout.strip() for n in ('crtendS.o', 'crtn.o')]
    r = run([HOST, '-shared', '-nostdlib', '-fPIC', '-fno-rtti', '-fno-exceptions', '-O2', '-I' + pdir + '/include', '-o', so]
            + crt + [src] + end)
    assert r.returncode == 0, 'plugin %s does not build: %s' % (name, r.stderr[-1500:])
    return so


def compile(src, out, plugins, flags=None, args=(), asm=False):
    """compile src to out (an object, or assembly with asm=True) with the product flags and the plugins (paths of .so), in
    that order; -> CompletedProcess (the caller checks returncode: some tests expect an error)"""
    f = PRODUCT_FLAGS if flags is None else flags
    return run([TARGET] + f + ['-fplugin=' + p for p in plugins] + list(args) + ['-S' if asm else '-c', '-o', out, src])


def section(obj, name):
    """the text of section `name` of an object file ('' when the object has no such section), read in-process"""
    data = elfobj.read(obj).section(name)
    return '' if data is None else data.decode('utf-8')


def undefined(obj):
    """names of the undefined symbols of an object file"""
    return {s[0] for s in elfobj.read(obj).symbols() if s[3] == 0 and s[0]}


class Checks:
    """collects failures; `done()` prints the verdict line and returns the exit status"""
    def __init__(self, name):
        self.name, self.fails, self.n = name, [], 0

    def __call__(self, what, cond, detail=''):
        self.n += 1
        if not cond:
            self.fails.append(what)
            print('FAIL', what, str(detail)[:1500])
        return bool(cond)

    def done(self, extra=''):
        print('%s: %s (%d checks)%s' % (self.name, 'FAILED %d' % len(self.fails) if self.fails else 'ok', self.n, extra))
        return 1 if self.fails else 0
