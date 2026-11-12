#!/usr/bin/env python3
"""Census shard (scratch, never merged): packshard.py PACK OUT LO HI  copies PACK/u/<i>.cpp for LO <= i < HI (and disp.S, imp.cpp when LO == 0)."""
import os, shutil, sys


def main(pack, out, lo, hi):
    os.makedirs(os.path.join(out, 'u'), exist_ok=True)
    ns = ['u/%03x.cpp' % i for i in range(lo, hi)] + (['disp.S', 'imp.cpp'] if lo == 0 else [])
    for n in ns:
        shutil.copyfile(os.path.join(pack, n), os.path.join(out, n))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
