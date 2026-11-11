#!/usr/bin/env python3
"""check_worldstop.py - assumption A1 of the world-stop window rules (platform/os/os_world.c, "THE STOP WINDOW").

Rule W2 needs: no registered thread blocks the pause signal except under the world lock. Statically, in platform/ and
toolchain/guest/ (comments and string literals removed):
  1. no call of a function that changes a thread's signal mask or leaves the process image: sigprocmask, pthread_sigmask,
     sigsuspend, sigwait*, signalfd, fork, vfork, system, popen, posix_spawn*, exec*;
  2. pthread_create occurs only in platform/os/os_posix.c, inside os_thread_start, and between an os_world_lock() and the
     next os_world_unlock() (both in that function, in that order, with the call between them).
Usage: check_worldstop.py [--root ports/fnv-nx] [--selftest]   exit 1 with one line per violation."""
import os, re, sys

BAD = re.compile(r'\b(sigprocmask|pthread_sigmask|sigsuspend|sigwait\w*|signalfd|fork|vfork|system|popen|posix_spawn\w*|'
                 r'execl\w*|execv\w*|execle|execlp)\s*\(')
CREATE = re.compile(r'\bpthread_create\s*\(')


def strip(src):
    out = re.sub(r'/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), src, flags=re.S)
    out = re.sub(r'//[^\n]*', '', out)
    out = re.sub(r'"(\\.|[^"\\\n])*"', '""', out)
    return out


def func_body(src, name):
    m = re.search(r'(?:^|\n)[A-Za-z_][\w \*]*\b' + name + r'\s*\([^;{]*\)\s*\{', src)
    if not m:
        return None
    i, depth = m.end(), 1
    while depth and i < len(src):
        depth += (src[i] == '{') - (src[i] == '}')
        i += 1
    return m.end(), i


def check_text(path, src, is_posix_backend):
    errs = []
    s = strip(src)
    for m in BAD.finditer(s):
        errs.append('%s:%d: %s() changes a thread signal mask or leaves the process image (A1)' %
                    (path, s.count('\n', 0, m.start()) + 1, m.group(1)))
    for m in CREATE.finditer(s):
        line = s.count('\n', 0, m.start()) + 1
        if not is_posix_backend:
            errs.append('%s:%d: pthread_create outside platform/os/os_posix.c (A1)' % (path, line))
            continue
        b = func_body(s, 'os_thread_start')
        if not b or not (b[0] <= m.start() < b[1]):
            errs.append('%s:%d: pthread_create outside os_thread_start (A1)' % (path, line))
            continue
        body = s[b[0]:b[1]]
        k = m.start() - b[0]
        lock = [x.start() for x in re.finditer(r'\bos_world_lock\s*\(\s*\)', body) if x.start() < k]
        unlock = [x.start() for x in re.finditer(r'\bos_world_unlock\s*\(\s*\)', body) if x.start() > k]
        between = body[lock[-1]:k] if lock else ''
        if not lock or not unlock or re.search(r'\bos_world_unlock\s*\(', between):
            errs.append('%s:%d: pthread_create is not between os_world_lock() and os_world_unlock() (W2)' % (path, line))
    return errs


def scan(root):
    errs = []
    for top in ('platform', 'toolchain/guest'):
        for d, _, fs in os.walk(os.path.join(root, top)):
            for f in fs:
                if f.endswith(('.c', '.cc', '.cpp', '.h', '.hpp', '.inc')):
                    p = os.path.join(d, f)
                    rel = os.path.relpath(p, root)
                    with open(p, errors='replace') as fh:
                        errs += check_text(rel, fh.read(), rel == 'platform/os/os_posix.c')
    return errs


def selftest():
    good = 'int os_thread_start(os_thread *t)\n{\n  os_world_lock();\n  int r = pthread_create(&t->th, 0, f, t);\n  os_world_unlock();\n  return r;\n}\n'
    assert not check_text('platform/os/os_posix.c', good, True)
    assert check_text('platform/os/os_posix.c', good.replace('os_world_lock();', ''), True)
    assert check_text('platform/os/os_posix.c', good.replace('os_world_unlock();', ''), True)
    assert check_text('platform/os/os_posix.c', good.replace('int r =', 'os_world_unlock(); int r ='), True)
    assert check_text('platform/win32/x.cpp', good, False)
    assert check_text('platform/os/os_posix.c', 'void g(void){ pthread_sigmask(0,0,0); }', True)
    assert not check_text('platform/os/os_posix.c', '/* system( */ const char *s = "fork(";', True)


if __name__ == '__main__':
    root = 'ports/fnv-nx'
    if '--root' in sys.argv:
        root = sys.argv[sys.argv.index('--root') + 1]
    if '--selftest' in sys.argv:
        selftest()
    errs = scan(root)
    print('\n'.join(errs) if errs else 'check_worldstop: ok')
    sys.exit(1 if errs else 0)
