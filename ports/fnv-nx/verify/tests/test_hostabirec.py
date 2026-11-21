#!/usr/bin/env python3
"""hostabirec.py string macros and the two headers added to the record (sdlayout.h, hostvm.h): the real headers, and copies of the include
directory with one header changed (test inputs, not stand-ins of any product file). Exit 0 on pass."""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import hostabirec  # noqa: E402

FAILED = []
N = [0]


def case(name, cond, extra=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


def variant(tmp, header, edit):
    d = tempfile.mkdtemp(dir=tmp)
    inc = os.path.join(d, "fnvnx")
    shutil.copytree(hostabirec.INCLUDE, inc)
    p = os.path.join(inc, header)
    text = edit(open(p).read())
    with open(p, "w") as f:
        f.write(text)
    return inc


def main():
    R = hostabirec.Record()
    sd = open(os.path.join(hostabirec.INCLUDE, "sdlayout.h")).read()
    dir_lit = re.search(r'^#define FNVNX_SD_DIR\s+"([^"]*)"', sd, re.M).group(1)
    fonts_tail = re.search(r'^#define FNVNX_SD_FONTS\s+FNVNX_SD_DIR\s+"([^"]*)"', sd, re.M).group(1)
    case("a string macro is its literal", R.string("FNVNX_SD_DIR") == dir_lit, R.string("FNVNX_SD_DIR"))
    case("a named string macro and an adjacent literal concatenate (FNVNX_SD_FONTS)", R.string("FNVNX_SD_FONTS") == dir_lit + fonts_tail,
         R.string("FNVNX_SD_FONTS"))
    allsd = re.findall(r"^#define (FNVNX_SD_\w+)\s+(?:FNVNX_SD_DIR\s+)?\"", sd, re.M)
    case("every string macro of sdlayout.h is in the record", all(n in R.strings for n in allsd) and len(allsd) >= 10, allsd)
    case("hostvm.h numbers are constants of the record", R.define("FNVNX_VM_ALIGN") == 0x10000 and R.define("FNVNX_SEC_VIEWS") == 512
         and R.define("FNVNX_MAP_FIXED_MAX") == 512, None)
    try:
        R.string("FNVNX_VM_ALIGN")
        ok = False
    except KeyError:
        ok = True
    case("an integer define is no string macro (KeyError)", ok)
    try:
        R.define("FNVNX_SD_FONTS")
        ok = False
    except KeyError:
        ok = True
    case("a string macro is no integer define (KeyError)", ok)
    case("module_text carries the strings", "STRINGS = " in hostabirec.module_text(R) and "'FNVNX_SD_FONTS'" in hostabirec.module_text(R))
    tmp = tempfile.mkdtemp(prefix="hbr-")
    try:
        for name, edit, frag in (
                ("an unknown name in a string macro is a RecordError, not skipped",
                 lambda t: t.replace('FNVNX_SD_DIR "/fonts"', 'FNVNX_SD_NOPE "/fonts"'), "FNVNX_SD_NOPE"),
                ("an escape in a literal is a RecordError", lambda t: t.replace('"/fonts"', '"/f\\\\n"'), "FNVNX_SD_FONTS"),
                ("a stray token after a literal is a RecordError", lambda t: t.replace('"/fonts"', '"/fonts" + 1'), "FNVNX_SD_FONTS"),
                ("a name that is only an integer define cannot be concatenated", lambda t: t.replace('FNVNX_SD_DIR "/fonts"', 'FNVNX_SD_COUNT "/fonts"')
                 + "\n#define FNVNX_SD_COUNT 3\n", "FNVNX_SD_COUNT")):
            inc = variant(tmp, "sdlayout.h", edit)
            try:
                hostabirec.Record(inc)
                case(name, False, "no error")
            except hostabirec.RecordError as e:
                case(name, frag in str(e), e)
        inc = variant(tmp, "sdlayout.h", lambda t: t.replace('FNVNX_SD_DIR "/fonts"', '"/fonts" FNVNX_SD_DIR'))
        case("literal and macro in either order concatenate", hostabirec.Record(inc).string("FNVNX_SD_FONTS") == "/fonts" + dir_lit)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d cases, %d failed" % (N[0], len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
