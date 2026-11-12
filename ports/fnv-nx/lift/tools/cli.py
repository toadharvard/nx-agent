"""The one command-line rule of the lifter entry points: every entry point declares its usage and its arguments, and
cli.parse() is its first statement. -h/--help prints the usage and exits 0; an option that is not declared, a missing
option value and a wrong positional count print the usage to stderr and exit 2. Nothing is ever taken as a path, an
output name or a file unless it was declared as that argument, so an unknown word can never reach the file system.
    pos, opts = cli.parse(USAGE, argv, npos=(min, max), opts={'--name': True (takes a value) | False (flag)})"""
import sys


def parse(usage, argv, npos=(0, 0), opts=None):
    opts = opts or {}; pos = []; got = {}; k = 0
    def bad(msg):
        sys.stderr.write('%s\n\n%s\n' % (msg, usage)); sys.exit(2)
    if any(a in ('-h', '--help') for a in argv):
        sys.stdout.write(usage + '\n'); sys.exit(0)
    while k < len(argv):
        a = argv[k]
        if a.startswith('-') and a != '-':
            if a not in opts: bad('unknown option %s' % a)
            if opts[a]:
                if k + 1 >= len(argv): bad('option %s needs a value' % a)
                got[a] = argv[k + 1]; k += 2
            else:
                got[a] = True; k += 1
        else:
            pos.append(a); k += 1
    lo, hi = npos
    if not lo <= len(pos) <= hi: bad('expected %s argument(s), got %d' % (lo if lo == hi else '%d..%d' % (lo, hi), len(pos)))
    return pos, got
