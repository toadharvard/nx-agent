#!/usr/bin/env python3
"""traced.py - run a Python script and record every file it read and every directory it listed (T1).

    python3 -I tools/pipeline/traced.py <deps.json> [--exclude DIR]... -- <script.py> [args...]

The script runs in this process under an audit hook (sys.addaudithook): `open` events opened for reading (modules
the script imports included: their source is opened the same way), `os.listdir` / `os.scandir` events. After a
successful run, deps.json holds {"files": {path: sha256}, "dirs": {path: sha256 of the sorted names}, "argv",
"python" (executable sha256 and version), "env" (FNVNX_* variables)}; paths under an --exclude directory (the
script's own outputs) are left out. check(deps.json, argv) is True only when every recorded input is still the
same, so a stage may keep the outputs of the last run instead of running again (the prepare stage: tools/pipeline/
build.py), exactly as a compiler's -MD dependencies let a build skip an unchanged object. The script's exit status
is this process's.
"""
import hashlib, json, os, runpy, sys

_reads, _dirs = set(), set()
WRITE = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def _hook(event, args):
    try:
        if event == 'open':
            path, mode, flags = args
            if isinstance(path, int) or path is None:
                return
            if mode is not None and any(c in str(mode) for c in 'wax+'):
                return
            if mode is None and flags and flags & WRITE:
                return
            _reads.add(os.path.abspath(os.fsdecode(path)))
        elif event in ('os.listdir', 'os.scandir'):
            p = args[0] if args else '.'
            if isinstance(p, int):
                return
            _dirs.add(os.path.abspath(os.fsdecode(p if p is not None else '.')))
    except Exception:
        pass


def file_sha(p):
    try:
        h = hashlib.sha256()
        with open(p, 'rb') as f:
            for b in iter(lambda: f.read(1 << 20), b''):
                h.update(b)
        return h.hexdigest()
    except (OSError, IsADirectoryError):
        return None


def names_sha(d):
    try:
        return hashlib.sha256('\n'.join(sorted(os.listdir(d))).encode('utf-8', 'surrogateescape')).hexdigest()
    except OSError:
        return None


def identity():
    return {'python': [file_sha(os.path.realpath(sys.executable)), sys.version],
            'env': {k: v for k, v in sorted(os.environ.items()) if k.startswith('FNVNX_')}}


def check(deps_path, argv):
    """True when deps_path records a run with this argv whose every input is unchanged (the same files with the
    same content, the same listings, the same interpreter and FNVNX_* environment)."""
    try:
        d = json.load(open(deps_path))
    except (OSError, ValueError):
        return False
    if d.get('argv') != list(argv) or d.get('ident') != identity():
        return False
    for p, h in d.get('files', {}).items():
        if file_sha(p) != h:
            return False
    for p, h in d.get('dirs', {}).items():
        if names_sha(p) != h:
            return False
    return bool(d.get('files'))


def main():
    a = sys.argv[1:]
    if len(a) < 3 or '--' not in a:
        sys.exit(__doc__)
    out = a[0]
    excl = []
    i = 1
    while a[i] != '--':
        if a[i] == '--exclude':
            excl.append(os.path.abspath(a[i + 1]) + os.sep)
            i += 2
        else:
            sys.exit(__doc__)
    script, args = a[i + 1], a[i + 2:]
    sys.addaudithook(_hook)
    sys.argv = [script] + args   # sys.path as `python3 -I script` has it (-I: no script directory)
    rc = 0
    try:
        runpy.run_path(script, run_name='__main__')
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    if rc == 0:
        # an import reads a valid __pycache__/*.pyc and only stats the source: the module's source is the input
        for m in list(sys.modules.values()):
            f = getattr(m, '__file__', None)
            if f and f.endswith('.py') and os.path.isfile(f):
                _reads.add(os.path.abspath(f))
        for p in list(_reads):
            if p.endswith('.pyc') and os.sep + '__pycache__' + os.sep in p:
                _reads.discard(p)
                src = os.path.join(os.path.dirname(os.path.dirname(p)), os.path.basename(p).split('.')[0] + '.py')
                if os.path.isfile(src):
                    _reads.add(src)
        keep = lambda p: not any(p.startswith(x) or p + os.sep == x for x in excl)
        files = {p: file_sha(p) for p in sorted(_reads) if keep(p) and os.path.isfile(p)}
        dirs = {p: names_sha(p) for p in sorted(_dirs) if keep(p) and os.path.isdir(p)}
        tmp = out + '.tmp'
        json.dump({'argv': [script] + args, 'ident': identity(), 'files': files, 'dirs': dirs}, open(tmp, 'w'))
        os.replace(tmp, out)
    sys.exit(rc)


if __name__ == '__main__':
    main()
