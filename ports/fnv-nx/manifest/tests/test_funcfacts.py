#!/usr/bin/env python3
"""Runs the self-test of check_funcfacts.py (synthetic PE images built in memory; no game bytes)."""
import os, runpy, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.argv = ["check_funcfacts.py"]
runpy.run_path(os.path.join(HERE, "..", "check_funcfacts.py"), run_name="__main__")
