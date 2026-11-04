#!/usr/bin/env python3
"""test_ini_port_defaults.py - V1 (D14) against S15-threads 4364484: the Switch ini defaults of platform/win32/ini.cpp
(port_default) keep GetPrivateProfile*'s Win32 semantics (tests/v1/ini/port_defaults.cpp, on tests/s4's harness).

    python3 -I tests/v1/test_ini_port_defaults.py [--work <dir>]

I1 a key the file has wins (any case, blanks, empty value), a commented or second-section key is absent;
I2 truncation and return value of a table value (size 0, 1, 2), no [General] section at all;
I3 [fnvnx] bPortDefaults in any case / 0x0 turns it off for that file only; FALLOUT_DEFAULT.INI is the engine's,
   MyFallout.ini and Fallout.ini.bak are not; key and section listings stay the file's.
Builds platform/ (os_posix) with tests/s4/Makefile's flags into a temporary directory; exit 1 on a failed check.
"""
import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--work')
    a = ap.parse_args(argv)
    work = a.work or tempfile.mkdtemp(prefix='v1-ini-')
    tests = '%s %s' % (os.path.join(PORT, 'tests', 's4', 'main.cpp'), os.path.join(HERE, 'ini', 'port_defaults.cpp'))
    r = subprocess.run(['make', '-s', '-j8', '-C', os.path.join(PORT, 'tests', 's4'), 'OUT=' + work,
                        'TEST_CXX=' + tests, work + '/s4_tests'], capture_output=True, text=True)
    if r.returncode:
        print('build failed:\n' + (r.stdout + r.stderr)[-1500:])
        return 2
    root = os.path.join(work, 'root')
    r = subprocess.run([os.path.join(work, 's4_tests')], capture_output=True, text=True,
                       env=dict(os.environ, FNVNX_ROOT=root))
    print((r.stdout + r.stderr).strip())
    return 1 if r.returncode else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
