"""Rule S, mechanical check (platform/win32/THREADS.md): only the delivery points touch another thread's observed context.
Run: python3 -I ports/fnv-nx/platform/test_threads_rule.py"""
import os, re, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = ('.c', '.cc', '.cpp', '.h')


def strip(src):
    src = re.sub(r'/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), src, flags=re.S)
    src = re.sub(r'//[^\n]*', '', src)
    return re.sub(r'"(\\.|[^"\\\n])*"', '""', src)


def functions(src):
    """[(name, start, end)] of the top-level definitions, by brace matching at depth 0"""
    out, depth, i, last = [], 0, 0, 0
    while i < len(src):
        ch = src[i]
        if ch == '{':
            if depth == 0:
                head = src[last:i]
                m = re.search(r'([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:__attribute__\s*\(\(.*?\)\)\s*)?$', head, re.S)
                start = i
                name = m.group(1) if m else None
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                out.append((name, start, i))
                last = i + 1
        elif ch == ';' and depth == 0:
            last = i + 1
        i += 1
    return out


def sources():
    for d, _, fs in os.walk(HERE):
        for f in fs:
            if f.endswith(EXT):
                yield os.path.join(d, f)


def allowed():
    md = open(os.path.join(HERE, 'win32', 'THREADS.md')).read()
    m = re.search(r'<!-- delivery-functions:([^>]*)-->', md)
    return set(m.group(1).split())


class ThreadsRule(unittest.TestCase):
    def test_observed_fields_only_in_delivery_points(self):
        ok = allowed()
        bad = []
        pat = re.compile(r'\bobs_\w+')
        for p in sources():
            src = strip(open(p, errors='replace').read())
            spans = functions(src)
            for m in pat.finditer(src):
                owner = next((n for n, a, b in spans if a <= m.start() <= b), None)
                if owner is None:   # the struct declaration (not inside a function body)
                    if not (p.endswith(os.path.join('win32', 'thread.cpp')) and 'struct w32_thread_obj' in src[:m.start()]):
                        bad.append((p, m.group(0), 'outside any function'))
                elif owner not in ok or not p.endswith(os.path.join('win32', 'thread.cpp')):
                    bad.append((p, m.group(0), owner))
        self.assertEqual(bad, [])

    def test_delivery_points_exist(self):
        src = strip(open(os.path.join(HERE, 'win32', 'thread.cpp')).read())
        names = {n for n, _, _ in functions(src)}
        self.assertEqual(sorted(allowed() - names), [])

    def test_no_kernel_pause_outside_world_stop(self):
        bad = []
        for p in sources():
            src = strip(open(p, errors='replace').read())
            for m in re.finditer(r'\b(os_thread_suspend|os_thread_resume|os_raw_pause|os_raw_resume)\s*\(', src):
                if not (p.endswith('os_world.c') or p.endswith('os_world.h') or p.endswith('os_guest.c') or p.endswith('os_posix.c') or p.endswith('os_horizon.c')):
                    bad.append((p, m.group(1)))
        self.assertEqual(bad, [])

    def test_no_user_pause_primitive(self):
        for p in sources():
            src = strip(open(p, errors='replace').read())
            self.assertNotIn('os_thread_suspend', src, p)
            self.assertNotIn('os_thread_resume', src, p)


if __name__ == '__main__':
    unittest.main()
