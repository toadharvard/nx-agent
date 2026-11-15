#!/usr/bin/env python3
"""Regenerates the table region of rt/rcptab.c from the real RCPSS/RSQRTSS of this (reference) host. Usage: tools/rcpgen.py WORKDIR (WORKDIR: the directory the generator binary is built in; a declared input)"""
import os, subprocess, sys
H = os.path.dirname(os.path.abspath(__file__)); R = os.path.dirname(H)
sys.path.insert(0, H)
import cli
(d,), _ = cli.parse('usage: rcpgen.py WORKDIR\n  WORKDIR  directory the generator binary is built in; rewrites the table region of rt/rcptab.c', sys.argv[1:], (1, 1))
os.makedirs(d, exist_ok=True)
subprocess.check_call(['gcc', '-O2', '-msse2', H + '/rcpgen.c', '-o', d + '/g'])
tabs = subprocess.check_output([d + '/g']).decode()
p = R + '/rt/rcptab.c'; s = open(p).read()
a = s.index('/* BEGIN GENERATED */\n') + len('/* BEGIN GENERATED */\n'); b = s.index('/* END GENERATED */')
open(p, 'w').write(s[:a] + tabs + s[b:])
