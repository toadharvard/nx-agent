#!/usr/bin/env python3
"""Regenerates the table region of rt/rcptab.c from the real RCPSS/RSQRTSS of this (reference) host. Usage: tools/rcpgen.py"""
import os, subprocess, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__)); R = os.path.dirname(H)
with tempfile.TemporaryDirectory() as d:
    subprocess.check_call(['gcc', '-O2', '-msse2', H + '/rcpgen.c', '-o', d + '/g'])
    tabs = subprocess.check_output([d + '/g']).decode()
p = R + '/rt/rcptab.c'; s = open(p).read()
a = s.index('/* BEGIN GENERATED */\n') + len('/* BEGIN GENERATED */\n'); b = s.index('/* END GENERATED */')
open(p, 'w').write(s[:a] + tabs + s[b:])
