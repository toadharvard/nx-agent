#!/usr/bin/env python3
"""unitcache.py - content-keyed MSVC object cache for the equivalence gate (T1-fastbench).

equiv.py's compile_unit asks this cache first when FNVNX_EQUIV_CACHE=<dir> is set (build, attribute, show; every
variant: EH off, lean, layouts). A compile's key is

    sha256(CACHE_V, the compiler's binaries (cl.exe, c1.dll, c1xx.dll, c2.dll), the command line with the tree, decomp
           and port paths replaced by tokens, the names of every file under the /I directories, and the content of
           every file the compile read: the source and each header cl.exe reported with /showIncludes)

so a unit is compiled again only when its prepared source, a header it includes, its flags, the rules' shim headers
or the compiler change; a header added anywhere on the include path changes every key (shadowing is then possible).
The fingerprint rows are always recomputed from the object (equiv.py's Fingerprinter), so changes to the
fingerprinting itself need no key. Layout:
    m/<sha1(source, command line, salt)>.json   the last compile of that unit and variant: {"key", "deps"}
    o/<key>.obj, o/<key>.txt                    the object and the compiler's text (showIncludes lines removed)
On a farm the directory is a checkout of fnv-nx-build's equiv/cache/<k>of<N> (tools/farm/equiv_job.sh).

    unitcache.py stat <dir>
    unitcache.py prune <dir>       drop objects no manifest names (each manifest keeps only its unit's last compile)
"""
import hashlib, json, os, re, shutil, sys
from pathlib import Path

CACHE_V = 'unitcache-1'
INC = re.compile(r'^Note: including file:\s*(.*?)\s*$')
_fh = {}            # (path, size, mtime_ns) -> sha256: one read per file per process
_names = {}         # directory -> sha256 of its file names


def file_hash(p):
    try:
        st = os.stat(p)
    except OSError:
        return None
    k = (p, st.st_size, st.st_mtime_ns)
    h = _fh.get(k)
    if h is None:
        with open(p, 'rb') as f:
            h = hashlib.sha256(f.read()).hexdigest()
        _fh[k] = h
    return h


def names_hash(d):
    k = (d, os.stat(d).st_mtime_ns)    # memo per directory state (a file added at its top changes its mtime; one
                                       # added in a subdirectory is seen by the next process)
    h = _names.get(k)
    if h is None:
        out = []
        for r, ds, fs in os.walk(d):
            ds.sort()
            out += [os.path.relpath(os.path.join(r, f), d) for f in sorted(fs)]
        h = _names[k] = hashlib.sha256('\n'.join(out).encode()).hexdigest()
    return h


def win_to_posix(p):
    if len(p) > 2 and p[1] == ':' and p[0] in 'Zz':
        p = p[2:]
    return os.path.normpath(p.replace('\\', '/'))


class Cache:
    def __init__(self, root, anchors, compiler_files):
        """anchors: [(token, absolute dir)], longest first; compiler_files: the binaries to put in the salt"""
        self.root = Path(root)
        self.anchors = sorted(((t, os.path.abspath(d)) for t, d in anchors), key=lambda x: -len(x[1]))
        self.salt = hashlib.sha256(('%s|%s' % (CACHE_V, '|'.join(file_hash(f) or '-' for f in compiler_files)))
                                   .encode()).hexdigest()
        self.hits = self.misses = 0

    def norm(self, s):
        for t, d in self.anchors:
            s = s.replace(d, t)
        return s

    def denorm(self, s):
        for t, d in self.anchors:
            if s.startswith(t):
                return d + s[len(t):]
        return s

    def _ident(self, args, inc_dirs):
        cmd = '\x1f'.join(self.norm(a) for a in args)
        names = '|'.join(names_hash(d) for d in inc_dirs if os.path.isdir(d))
        return cmd, names

    def _key(self, cmd, names, deps):
        hs = []
        for d in deps:
            h = file_hash(self.denorm(d))
            if h is None:
                return None
            hs.append('%s=%s' % (d, h))
        return hashlib.sha256('\n'.join([self.salt, cmd, names] + hs).encode()).hexdigest()[:32]

    def _manifest(self, source, cmd):
        return self.root / 'm' / (hashlib.sha1(('%s|%s|%s' % (source, cmd, self.salt)).encode()).hexdigest() + '.json')

    def lookup(self, source, args, inc_dirs, obj):
        """-> the compiler's text when <obj> was written from the cache, else None"""
        cmd, names = self._ident(args, inc_dirs)
        m = self._manifest(source, cmd)
        try:
            j = json.loads(m.read_text())
        except (OSError, ValueError):
            self.misses += 1
            return None
        k = self._key(cmd, names, j['deps'])
        o = self.root / 'o' / ('%s.obj' % k) if k else None
        if not k or k != j['key'] or not o.is_file():
            self.misses += 1
            return None
        shutil.copyfile(o, obj)
        self.hits += 1
        t = self.root / 'o' / ('%s.txt' % k)
        return t.read_text(errors='replace') if t.is_file() else ''

    def store(self, source, args, inc_dirs, src_path, text, obj):
        """record a successful compile; returns the text without the showIncludes lines"""
        deps, rest = [self.norm(os.path.abspath(src_path))], []
        for line in text.splitlines():
            mm = INC.match(line.strip())
            if mm:
                deps.append(self.norm(win_to_posix(mm.group(1))))
            else:
                rest.append(line)
        text = '\n'.join(rest) + ('\n' if rest else '')
        deps = sorted(set(deps))
        cmd, names = self._ident(args, inc_dirs)
        k = self._key(cmd, names, deps)
        if k is None:
            return text
        od = self.root / 'o'; od.mkdir(parents=True, exist_ok=True)
        tmp = od / ('%s.obj.tmp%d' % (k, os.getpid()))
        shutil.copyfile(obj, tmp); os.replace(tmp, od / ('%s.obj' % k))
        (od / ('%s.txt' % k)).write_text(text)
        m = self._manifest(source, cmd); m.parent.mkdir(parents=True, exist_ok=True)
        tmp = m.with_suffix('.tmp%d' % os.getpid())
        tmp.write_text(json.dumps({'key': k, 'source': source, 'deps': deps}) + '\n'); os.replace(tmp, m)
        return text


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('stat', 'prune'):
        print(__doc__); return 2
    r = Path(sys.argv[2])
    if sys.argv[1] == 'prune':
        keep = set()
        for m in (r / 'm').glob('*.json') if (r / 'm').is_dir() else []:
            try:
                keep.add(json.loads(m.read_text())['key'])
            except (OSError, ValueError, KeyError):
                m.unlink()
        n = 0
        for f in list((r / 'o').iterdir()) if (r / 'o').is_dir() else []:
            if f.name.split('.')[0] not in keep:
                f.unlink(); n += 1
        print('unitcache: pruned %d files, %d objects kept' % (n, len(keep)))
        return 0
    m = len(list((r / 'm').glob('*.json'))) if (r / 'm').is_dir() else 0
    o = list((r / 'o').glob('*.obj')) if (r / 'o').is_dir() else []
    print('manifests %d, objects %d, %d MB' % (m, len(o), sum(p.stat().st_size for p in o) >> 20))
    return 0


if __name__ == '__main__':
    sys.exit(main())
