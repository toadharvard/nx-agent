#!/usr/bin/env python3
"""The FPCR row of the declared console CPU (verify/targets.json console_cpu.fpcr): its fields, the one integer derived from it and the judgement of its shape.
Pure (no file, no environment): verify/targets.py (the record's checker) and gen_header.py (the header generator, a recipe stage) both read this one definition."""

FPCR_FIELDS = {"AHP": (26, 1), "DN": (25, 1), "FZ": (24, 1), "RMode": (22, 2)}   # name -> (low bit, width): DDI0488H 14.4.1, Table 14-3; the other bits are RES0 (trap enables included)


def fpcr_value(t):
    """The one integer of the console CPU's FPCR for guest code: each field of the record at its bit position (FPCR_FIELDS). Everything that writes FPCR (`msr fpcr`) reads this value, generated into
    fnvnx_target.h by gen_header.py. The record's shape is judged in `check`."""
    f = t["console_cpu"]["fpcr"]
    return sum(f[n] << lo for n, (lo, _) in FPCR_FIELDS.items())


def fpcr_errors(t):
    """Shape of the row `console_cpu.fpcr`: exactly the fields of FPCR_FIELDS, each one integer that fits its width."""
    f = t["console_cpu"].get("fpcr")
    if not isinstance(f, dict) or set(f) != set(FPCR_FIELDS):
        return ["console_cpu.fpcr has exactly the fields %s" % sorted(FPCR_FIELDS)]
    return ["console_cpu.fpcr.%s is one integer of %d bit(s)" % (n, w) for n, (lo, w) in sorted(FPCR_FIELDS.items()) if type(f[n]) is not int or not 0 <= f[n] < (1 << w)]
