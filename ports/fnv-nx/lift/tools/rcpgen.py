#!/usr/bin/env python3
"""Regenerates the table region of rt/rcptab.c from the real RCPSS/RSQRTSS of this (reference) host. Usage: tools/rcpgen.py CACHE (CACHE: the verdict's store; the generator is the host root lift/rcpgen, built by verify/recipe.py in it)"""
import os, subprocess, sys
H = os.path.dirname(os.path.abspath(__file__)); R = os.path.dirname(H)
sys.path.insert(0, H)
import cli, hostbuild
(cache,), _ = cli.parse('usage: rcpgen.py CACHE\n  CACHE  the verdict store (GATE_CACHE), passed explicitly; the generator is built in it from ports/fnv-nx/lift/rcpgen; rewrites the table region of rt/rcptab.c', sys.argv[1:], (1, 1))
gen = hostbuild.host_exe(cache, 'ports/fnv-nx/lift/rcpgen')
tabs = subprocess.check_output([gen]).decode()
p = R + '/rt/rcptab.c'; s = open(p).read()
a = s.index('/* BEGIN GENERATED */\n') + len('/* BEGIN GENERATED */\n'); b = s.index('/* END GENERATED */')
open(p, 'w').write(s[:a] + tabs + s[b:])
