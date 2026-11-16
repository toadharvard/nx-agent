"""The directory that holds the package `fnvlift` is an argument of a tool: `--lib DIR` (a recipe stage passes the mount of its declared input, /in/ports/fnv-nx/lift).
take() removes the option from sys.argv, puts DIR on sys.path and returns it; a missing option is an error. No other path (the tool's own parent included) is a place
the package is found, so the declared input is the only source and a stage's key holds its content."""
import os, sys


def take():
    if '--lib' not in sys.argv or sys.argv.index('--lib') + 1 >= len(sys.argv):
        sys.exit('%s: missing --lib DIR (the directory that holds the package fnvlift)' % os.path.basename(sys.argv[0]))
    i = sys.argv.index('--lib'); d = sys.argv[i + 1]; del sys.argv[i:i + 2]
    sys.path.insert(0, d)
    return d
