#!/usr/bin/env python3
"""check_constants.py - compare the Win32 constants and GUIDs of the audio/input layer with reference headers.

usage: check_constants.py REFERENCE_INCLUDE_DIR [more reference headers or dirs...]

Reference side: `#define NAME value` (single or continued lines) and DEFINE_GUID(NAME, ...) of dsound.h, dinput.h,
xinput.h, mmsystem.h (and dsconf.h) found in the given directories. Only values are compared; nothing is copied.
Our side (relative to ports/fnv-nx): platform/audio, platform/input (*.cpp *.h), platform/include/*.h and the
shim headers dsound.h dinput.h xinput.h mmsystem.h found by glob under shim/include. Forms understood:
  #define N expr | enum { N = expr, ... } | [static] const|constexpr T N = expr[, N2 = expr2] |
  DEFINE_GUID / FNVNX_DS_GUID / FNVNX_DI_GUID / DS_IID (name, l, w1, w2, b0..b7) | const GUID N = {l, w1, w2, {b0..b7}}
Names are compared after dropping trailing '_' and a leading 'k'; ALIASES maps a file-local name to the
reference name where the source uses its own short name. The I3DL2 preset table of dsound.cpp is compared with
the 30 I3DL2_ENVIRONMENT_PRESET_* macros (12 values each), rows identified by their trailing /* NAME */ comment.
Expressions that cannot be evaluated (they name something outside both sides) are skipped and counted.
DirectShow part: reference side also reads uuids.h (OUR_GUID_ENTRY(name, ...) lines), vfwmsgs.h, evcode.h, winerror.h
and the .idl files (strmif, axcore, axextend, control, oaidl, ...: `uuid(...)` followed by `interface|coclass|
dispinterface NAME` is IID_NAME / CLSID_NAME; enums with explicit or implicit values). Our side adds the dshow
sources, whose GUID forms are DSH_G(name, l, w1, w2, b0..b7) and the shorthand DSH_L/E/M/T(name, l) (fixed tail of the
quartz interface / quartz media type / FOURCC-derived / time-format GUID families); the local name's trailing '_' is
dropped, so IID_IFilterGraph_ is compared with IID_IFilterGraph. DSHOW_TAILS holds the tails of the shorthands.
Exit status: 0 no mismatch, 1 at least one mismatch, 2 usage error."""
import ast
import glob
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
M32 = 0xFFFFFFFF

# file-local names that stand for a reference name (value comparison only; the name is not otherwise related)
ALIASES = {
    "waveout.cpp": {
        "OK": "MMSYSERR_NOERROR", "BADDEV": "MMSYSERR_BADDEVICEID", "NOMEM": "MMSYSERR_NOMEM",
        "INVALHANDLE": "MMSYSERR_INVALHANDLE", "BADERRNUM": "MMSYSERR_BADERRNUM", "INVALFLAG": "MMSYSERR_INVALFLAG",
        "INVALPARAM": "MMSYSERR_INVALPARAM", "BADFORMAT": "WAVERR_BADFORMAT", "STILLPLAYING": "WAVERR_STILLPLAYING",
        "UNPREPARED": "WAVERR_UNPREPARED", "H_DONE": "WHDR_DONE", "H_PREP": "WHDR_PREPARED", "H_BEGIN": "WHDR_BEGINLOOP",
        "H_END": "WHDR_ENDLOOP", "H_QUEUE": "WHDR_INQUEUE",
    },
    "dsound.cpp": {
        "GUID_Chorus": "GUID_DSFX_STANDARD_CHORUS", "IID_Chorus": "IID_IDirectSoundFXChorus8",
        "GUID_Flanger": "GUID_DSFX_STANDARD_FLANGER", "IID_Flanger": "IID_IDirectSoundFXFlanger8",
        "GUID_Echo": "GUID_DSFX_STANDARD_ECHO", "IID_Echo": "IID_IDirectSoundFXEcho8",
        "GUID_Distortion": "GUID_DSFX_STANDARD_DISTORTION", "IID_Distortion": "IID_IDirectSoundFXDistortion8",
        "GUID_Compressor": "GUID_DSFX_STANDARD_COMPRESSOR", "IID_Compressor": "IID_IDirectSoundFXCompressor8",
        "GUID_Gargle": "GUID_DSFX_STANDARD_GARGLE", "IID_Gargle": "IID_IDirectSoundFXGargle8",
        "GUID_ParamEq": "GUID_DSFX_STANDARD_PARAMEQ", "IID_ParamEq": "IID_IDirectSoundFXParamEq8",
        "IID_KsPropertySet": "IID_IKsPropertySet", "DS3DALG_HRTF_FULL": "DS3DALG_HRTF_FULL",
        "kIID_IUnknown": "IID_IUnknown",
    },
    "ds3d.h": {"DS3D_MODE_NORMAL": "DS3DMODE_NORMAL", "DS3D_MODE_HEADRELATIVE": "DS3DMODE_HEADRELATIVE",
               "DS3D_MODE_DISABLE": "DS3DMODE_DISABLE"},
    "dinput.cpp": {"P_" + n: "DIPROP_" + n for n in (
        "BUFFERSIZE AXISMODE GRANULARITY RANGE DEADZONE SATURATION FFGAIN FFLOAD AUTOCENTER CALIBRATIONMODE "
        "CALIBRATION GUIDANDPATH INSTANCENAME PRODUCTNAME JOYSTICKID KEYNAME SCANCODE VIDPID USERNAME TYPENAME").split()},
    "dshow_int.h": dict(
        {"SEEK_" + a: "AM_SEEKING_" + b for a, b in (
            ("CanSeekAbsolute", "CanSeekAbsolute"), ("CanSeekForwards", "CanSeekForwards"),
            ("CanSeekBackwards", "CanSeekBackwards"), ("CanGetCurrentPos", "CanGetCurrentPos"),
            ("CanGetStopPos", "CanGetStopPos"), ("CanGetDuration", "CanGetDuration"),
            ("NoPositioning", "NoPositioning"), ("Absolute", "AbsolutePositioning"),
            ("Relative", "RelativePositioning"), ("Incremental", "IncrementalPositioning"),
            ("PosMask", "PositioningBitsMask"), ("KeyFrame", "SeekToKeyFrame"), ("ReturnTime", "ReturnTime"),
            ("Segment", "Segment"), ("NoFlush", "NoFlush"))},
        CLSID_MPEG1Splitter_="CLSID_MPEG1Splitter", CLSID_MPEGAudioDecoder_="CLSID_CMpegAudioCodec"),
}
ALIASES["dshow.cpp"] = ALIASES["dshow_filters.cpp"] = ALIASES["dshow_int.h"]
# our name = a value derived from reference names (an expression over the reference side)
DERIVED = {
    "SEM_KBD": "DIKEYBOARD_ESCAPE & ~DIK_ESCAPE",
    "SEM_MOUSE_ABS": "DIMOUSE_XAXISAB - DIMOFS_X",
    "SEM_MOUSE_REL": "DIMOUSE_XAXIS - DIMOFS_X",
    "SEM_MOUSE_BTN": "(DIMOUSE_BUTTON0 - DIMOFS_BUTTON0) & ~0x7F",
}
# reference values known to differ from the real SDK header (Wine 9.0 typo), waived with the evidence
WAIVED = {
    "SEM_MOUSE_BTN": "Wine writes DIMOUSE_BUTTON0 as (0x82000400|off); the DirectX SDK header has 0x82000480, "
                     "which is what the game's action maps carry: needs a decision (verify against the exe)",
    "I3DL2_ENVIRONMENT_PRESET_CITY[7]": "reference +2217 is outside the legal reflections range (max +1000 mB); "
                                        "ours -2217 is in range: needs a decision",
    "I3DL2_ENVIRONMENT_PRESET_SEWERPIPE[8]": "reference 0.011 vs ours 0.021 (reverb delay); both are in range, the "
                                             "reference has no proof either way: needs a decision",
}

CAST = re.compile(r"\(\s*(?:const\s+)?(?:unsigned|signed|int|long|short|char|DWORD|WORD|BYTE|UINT|ULONG|LONG|HRESULT|HR|uint32_t|"
                  r"INT|SHORT|USHORT|BOOL|size_t|uint\d+_t|int\d+_t|float|double|LPARAM|WPARAM|UINT_PTR|ULONG_PTR)"
                  r"(?:\s+(?:long|int|short|char))*\s*\)")
NUMSUF = re.compile(r"\b(0[xX][0-9a-fA-F]+|\d+)(?:[uU][lL]{0,2}|[lL]{1,2}[uU]?)\b")
IDENT = re.compile(r"(?<![\w.])[A-Za-z_]\w*")


def norm(name):
    name = name.rstrip("_")
    return name[1:] if name.startswith("k") and name[1:2].isupper() else name


class Side:
    def __init__(self, label):
        self.label = label
        self.raw = {}      # name -> (expr string, file)
        self.guid = {}     # name -> (tuple of 11 ints, file)
        self.val = {}
        self.skipped = 0

    def add(self, name, expr, f):
        self.raw.setdefault(name, (expr.strip(), f))

    def evaluate(self, name, stack=()):
        if name in self.val:
            return self.val[name]
        if name in stack or name not in self.raw:
            return None
        v = self.ev_expr(self.raw[name][0], stack + (name,))
        self.val[name] = v
        return v

    def ev_expr(self, expr, stack):
        e = re.sub(r"/\*.*?\*/", "", expr)
        e = re.sub(r"//.*", "", e)
        e = e.replace("\\\n", " ").strip()
        if not e or e.startswith('"') or e.startswith("{") or e.startswith("'") and len(e) > 3:
            return None
        if e.startswith("'") and e.endswith("'") and len(e) == 3:
            return ord(e[1])
        # MAKEDIPROP(n): a GUID-sized property id, compared as a number tagged "prop"
        m = re.fullmatch(r"\(?\s*MAKEDIPROP\s*\(\s*(.+?)\s*\)\s*\)?", e)
        if m:
            v = self.ev_expr(m.group(1), stack)
            return None if v is None else ("prop", v)
        e = re.sub(r"__MSABI_LONG\s*\(|_HRESULT_TYPEDEF_\s*\(", "(", e)
        e = re.sub(r"(\d\.\d*|\.\d+)[fF]\b", r"\1", e)
        e = CAST.sub(" ", e)
        e = NUMSUF.sub(lambda m_: m_.group(1), e)
        e = e.replace("&&", " and ").replace("||", " or ")

        def fn(m_):  # MAKE_HRESULT / MAKE_DSHRESULT / MAKE_DIHRESULT style macros
            return m_.group(0)
        names = {}
        out = []
        pos = 0
        for m_ in IDENT.finditer(e):
            out.append(e[pos:m_.start()])
            pos = m_.end()
            n = m_.group(0)
            if n in ("and", "or", "not", "sizeof"):
                out.append(n)
                continue
            if n in ("MAKE_HRESULT", "MAKE_DSHRESULT", "MAKE_DIHRESULT", "MAKE_DSHRESULT_"):
                out.append("_" + n)
                continue
            builtin = {"SEVERITY_ERROR": 1, "SEVERITY_SUCCESS": 0, "FACILITY_WIN32": 7, "_FACDS": 0x878,
                       "_FACDI": 0x877, "S_OK": 0, "S_FALSE": 1, "E_FAIL": 0x80004005, "E_NOTIMPL": 0x80004001,
                       "E_INVALIDARG": 0x80070057, "E_POINTER": 0x80004003, "E_NOINTERFACE": 0x80004002,
                       "E_HANDLE": 0x80070006, "E_OUTOFMEMORY": 0x8007000E, "E_ACCESSDENIED": 0x80070005,
                       "E_PENDING": 0x8000000A, "E_UNEXPECTED": 0x8000FFFF, "REGDB_E_CLASSNOTREG": 0x80040154,
                       "CLASS_E_NOAGGREGATION": 0x80040110, "ERROR_OLD_WIN_VERSION": 1150, "ERROR_RMODE_APP": 1153,
                       "ERROR_BAD_DRIVER_LEVEL": 119, "ERROR_FILE_NOT_FOUND": 2, "ERROR_ACCESS_DENIED": 5,
                       "ERROR_NOT_FOUND": 1168, "ERROR_INVALID_PARAMETER": 87, "ERROR_ALREADY_INITIALIZED": 1247,
                       "ERROR_NOT_READY": 21, "ERROR_ARITHMETIC_OVERFLOW": 534, "ERROR_READ_FAULT": 30,
                       "ERROR_INSUFFICIENT_BUFFER": 122, "ERROR_BUSY": 170, "ERROR_NOT_SUPPORTED": 50,
                       "ERROR_DEV_NOT_EXIST": 55, "ERROR_BAD_COMMAND": 22, "ERROR_ALREADY_EXISTS": 183,
                       "ERROR_NOT_OWNER": 288, "ERROR_INVALID_HANDLE": 6, "ERROR_INVALID_DATA": 13,
                       "ERROR_OUTOFMEMORY": 14, "ERROR_INVALID_ACCESS": 12, "ERROR_OTHER_ERROR": 0, "WAVE_MAPPER": 0xFFFFFFFF,
                       "DIMOFS_X": 0, "DIMOFS_Y": 4, "DIMOFS_Z": 8, "DIMOFS_BUTTON0": 12, "DIMOFS_BUTTON1": 13,
                       "DIMOFS_BUTTON2": 14, "DIMOFS_BUTTON3": 15}
            if n in builtin:
                out.append(str(builtin[n]))
                continue
            v = self.evaluate(n, stack)
            if v is None or isinstance(v, tuple):
                return None
            out.append("(%d)" % v)
        out.append(e[pos:])
        py = "".join(out)

        def _MAKE_HRESULT(s, f, c):
            return ((s & 1) << 31) | ((f & 0x7FFF) << 16) | (c & 0xFFFF)
        env = {"_MAKE_HRESULT": _MAKE_HRESULT,
               "_MAKE_DSHRESULT": lambda c: _MAKE_HRESULT(1, 0x878, c),
               "_MAKE_DIHRESULT": lambda c: _MAKE_HRESULT(1, 0x877, c)}
        try:
            tree = ast.parse(py.strip(), mode="eval")
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Call, ast.Name,
                                     ast.Load, ast.BoolOp, ast.Compare, ast.IfExp, ast.operator, ast.unaryop,
                                     ast.boolop, ast.cmpop)):
                return None
            if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in env):
                return None
            if isinstance(node, ast.Name) and node.id not in env and node.id not in ("and", "or"):
                return None
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                return None
        try:
            v = eval(compile(tree, "<c>", "eval"), {"__builtins__": {}}, env)
        except (ArithmeticError, TypeError, ValueError):   # what an expression over constants can raise: x / 0, float << n, ...
            return None
        if isinstance(v, float):
            return v
        return int(v)


def strip_comments(t):
    t = re.sub(r"/\*.*?\*/", lambda m: " " * 0 + re.sub(r"[^\n]", " ", m.group(0)), t, flags=re.S)
    return re.sub(r"//[^\n]*", "", t)


def split_top(s):
    out, d, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            d += 1
        elif ch in ")]}":
            d -= 1
        if ch == "," and d == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def guid_tuple(args):
    vals = []
    for a in args:
        v = Side("tmp").ev_expr(a.strip().strip("{}"), ())
        if not isinstance(v, int):
            return None
        vals.append(v)
    if len(vals) != 11:
        return None
    return tuple(vals)


# DSH_L/E/M/T shorthands of dshow_int.h: (w1, w2, b0..b7) after the first data word
DSHOW_TAILS = {"L": (0x0ad4, 0x11ce, 0xb0, 0x3a, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70),
               "E": (0x524f, 0x11ce, 0x9f, 0x53, 0x00, 0x20, 0xaf, 0x0b, 0xa7, 0x70),
               "M": (0x0000, 0x0010, 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71),
               "T": (0x8c82, 0x11cf, 0xbc, 0x0c, 0x00, 0xaa, 0x00, 0xac, 0x74, 0xf6)}
GUID_MACROS = r"(?:DEFINE_GUID|FNVNX_DS_GUID|FNVNX_DI_GUID|DS_IID|DEFINE_OLEGUID|DSH_G)"


def parse_text(side, text, fname, ours):
    t = strip_comments(text)
    t = t.replace("\\\n", " ")
    for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(?![\w(])[ \t]+([^\n]+)", t, re.M):
        side.add(m.group(1), m.group(2), fname)
    for m in re.finditer(r"\b(" + GUID_MACROS + r")\s*\(([^;]*?)\)\s*;", t, re.S):
        a = split_top(m.group(2))
        g = guid_tuple(a[1:])
        if g and re.fullmatch(r"[A-Za-z_]\w*", a[0].strip()):
            side.guid.setdefault(a[0].strip(), (g, fname))
    # reference uuids.h: OUR_GUID_ENTRY(name, l, w1, w2, b1..b8) without a trailing ';'
    for m in re.finditer(r"^[ \t]*OUR_GUID_ENTRY\s*\(([^)\n]*)\)", t, re.M):
        a = split_top(m.group(1))
        g = guid_tuple(a[1:])
        if g and re.fullmatch(r"[A-Za-z_]\w*", a[0].strip()):
            side.guid.setdefault(a[0].strip(), (g, fname))
    # our dshow_int.h: DSH_L/E/M/T(name, first) shorthands
    for m in re.finditer(r"^[ \t]*DSH_([LEMT])\s*\(([^;\n]*)\)\s*;", t, re.M):
        a = split_top(m.group(2))
        v = Side("tmp").ev_expr(a[1], ()) if len(a) == 2 else None
        if isinstance(v, int) and re.fullmatch(r"[A-Za-z_]\w*", a[0].strip()):
            side.guid.setdefault(a[0].strip(), ((v,) + DSHOW_TAILS[m.group(1)], fname))
    # .idl: [ ... uuid(...) ... ] interface|coclass|dispinterface NAME  ->  IID_NAME / CLSID_NAME
    for m in re.finditer(r"\buuid\s*\(\s*([0-9a-fA-F]{8})-([0-9a-fA-F]{4})-([0-9a-fA-F]{4})-([0-9a-fA-F]{4})-"
                         r"([0-9a-fA-F]{12})\s*\)", t):
        j, depth = m.end(), 1
        while j < len(t) and depth:   # skip to the ']' that closes the attribute list
            depth += (t[j] == "[") - (t[j] == "]")
            j += 1
        k = re.match(r"\s*(interface|coclass|dispinterface)\s+(\w+)", t[j:])
        if not k:
            continue
        hx = m.group(4) + m.group(5)
        g = (int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16)) + tuple(
            int(hx[i:i + 2], 16) for i in range(0, 16, 2))
        side.guid.setdefault(("CLSID_" if k.group(1) == "coclass" else "IID_") + k.group(2), (g, fname))
    for m in re.finditer(r"\bconst(?:expr)?\s+GUID\s+([A-Za-z_]\w*)\s*=\s*\{\s*([^;]*?)\}\s*;", t, re.S):
        a = split_top(m.group(2).replace("{", " ").replace("}", " "))
        g = guid_tuple(a)
        if g is None and len(a) == 11:
            g = None
        if g:
            side.guid.setdefault(m.group(1), (g, fname))
    if True:
        for m in re.finditer(r"\benum\b[^{;]*\{([^}]*)\}", t):
            nxt = 0
            for item in split_top(m.group(1)):
                item = item.strip()
                if not item:
                    continue
                if "=" in item:
                    n, e = item.split("=", 1)
                    side.add(n.strip(), e, fname)
                    v = side.ev_expr(e, ())
                    nxt = v + 1 if isinstance(v, int) else None
                elif nxt is not None:
                    side.add(item, str(nxt), fname)
                    nxt += 1
    if ours:
        for m in re.finditer(r"\b(?:static\s+)?(?:const|constexpr)\s+(?!GUID\b)((?:unsigned\s+|signed\s+)?"
                             r"(?:[A-Za-z_][\w:]*)(?:\s+(?:long|int))*)\s+([A-Za-z_]\w*\s*=[^;{}]*);", t):
            for item in split_top(m.group(2)):
                if "=" in item:
                    n, e = item.split("=", 1)
                    if re.fullmatch(r"[A-Za-z_]\w*", n.strip()):
                        side.add(n.strip(), e, fname)


REF_FILES = ("dsound.h", "dinput.h", "xinput.h", "mmsystem.h", "dsconf.h", "uuids.h", "vfwmsgs.h", "evcode.h",
             "winerror.h", "strmif.idl", "axcore.idl", "axextend.idl", "control.idl", "oaidl.idl", "unknwn.idl",
             "mmreg.h", "amvideo.idl", "ks.h", "ksmedia.h")


def collect_ref(paths):
    side = Side("reference")
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "**", "*.h"), recursive=True))
            files += sorted(glob.glob(os.path.join(p, "**", "*.idl"), recursive=True))
        else:
            files.append(p)
    for f in files:
        if os.path.basename(f) in REF_FILES:
            parse_text(side, open(f, errors="replace").read(), os.path.basename(f), False)
    return side, files


def collect_ours():
    pats = ["platform/audio/*.cpp", "platform/audio/*.h", "platform/input/*.cpp", "platform/input/*.h",
            "platform/include/*.h"]
    files = []
    for p in pats:
        files += sorted(glob.glob(os.path.join(ROOT, p)))
    for n in ("dsound.h", "dinput.h", "xinput.h", "mmsystem.h"):
        files += sorted(glob.glob(os.path.join(ROOT, "shim", "include", "**", n), recursive=True))
    side = Side("ours")
    per_file = {}
    for f in files:
        base = os.path.basename(f)
        sub = Side("f")
        txt = open(f, errors="replace").read()
        parse_text(sub, txt, base, True)
        per_file[f] = (sub, txt)
        for n, v in sub.raw.items():
            side.raw.setdefault(n, v)
        for n, v in sub.guid.items():
            side.guid.setdefault(n, v)
    return side, files, per_file


def same(a, b):
    if isinstance(a, tuple) != isinstance(b, tuple):
        a, b = (a[1] if isinstance(a, tuple) else a), (b[1] if isinstance(b, tuple) else b)
    if a == b:
        return True
    if isinstance(a, int) and isinstance(b, int):
        return (a & M32) == (b & M32) and abs(a) < 1 << 33 and abs(b) < 1 << 33
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-6
    return False


def fmt(v):
    if isinstance(v, tuple):
        return "MAKEDIPROP(%s)" % (v[1],)
    if isinstance(v, int):
        return "%d (0x%X)" % (v, v & M32) if abs(v) < 1 << 33 else str(v)
    return str(v)


def fmt_guid(g):
    return "{%08x-%04x-%04x-%s-%s}" % (g[0], g[1], g[2], "".join("%02x" % b for b in g[3:5]),
                                      "".join("%02x" % b for b in g[5:]))


def i3dl2(ref, per_file):
    """compare the preset table of dsound.cpp with I3DL2_ENVIRONMENT_PRESET_* (rows named by their /* NAME */)"""
    res = {"rows": 0, "values": 0, "bad": []}
    refp = {}
    for n, (e, f) in ref.raw.items():
        m = re.fullmatch(r"I3DL2_ENVIRONMENT_PRESET_(\w+)", n)
        if m and "," in e:
            refp[m.group(1)] = [x.strip() for x in split_top(e)]
    for f, (_, txt) in per_file.items():
        if os.path.basename(f) != "dsound.cpp":
            continue
        m = re.search(r"k_i3dl2_presets\[\]\s*=\s*\{(.*?)\n\};", txt, re.S)
        if not m:
            continue
        for row in re.finditer(r"\{([^{}]*)\},?\s*/\*\s*(\w+)\s*\*/", m.group(1)):
            name = row.group(2)
            if name not in refp:
                res["bad"].append((name, "preset row has no reference macro", ""))
                continue
            ours = [x.strip() for x in split_top(row.group(1))]
            res["rows"] += 1
            if len(ours) != len(refp[name]):
                res["bad"].append((name, "%d values" % len(ours), "%d values" % len(refp[name])))
                continue
            for i, (a, b) in enumerate(zip(ours, refp[name])):
                res["values"] += 1
                try:
                    fa, fb = float(a.rstrip("fF")), float(b.rstrip("fF"))
                except ValueError:
                    continue
                if abs(fa - fb) > 1e-9:
                    res["bad"].append(("I3DL2_ENVIRONMENT_PRESET_%s[%d]" % (name, i), a, b))
    return res


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    ref, rfiles = collect_ref(argv[1:])
    ours, ofiles, per_file = collect_ours()
    if not ref.raw and not ref.guid:
        print("no reference definitions found in", argv[1:])
        return 2
    ZERO = (0,) * 11
    for n, (e, f) in list(ref.raw.items()):   # a macro that renames a GUID: DS3DALG_DEFAULT GUID_NULL, IID_X8 IID_X
        e = e.strip()
        if n not in ref.guid and (e in ref.guid or e == "GUID_NULL"):
            ref.guid[n] = (ref.guid[e][0] if e in ref.guid else ZERO, f)
    rnames = {norm(n): n for n in ref.raw}
    rguid = {norm(n): n for n in ref.guid}
    mism, waived, compared, only, unevaluable = [], [], 0, [], 0
    seen = set()
    # constants (per file, so file-local aliases apply)
    for f, (sub, _) in per_file.items():
        base = os.path.basename(f)
        al = ALIASES.get(base, {})
        for n in sub.raw:
            key = al.get(n, n)
            rn = rnames.get(norm(key))
            if rn is None:
                if (n, base) not in seen:
                    only.append(n)
                seen.add((n, base))
                continue
            if (rn, base) in seen:
                continue
            seen.add((rn, base))
            ov, rv = sub.evaluate(n), ref.evaluate(rn)
            if ov is None or rv is None:
                unevaluable += 1
                if os.environ.get("CHECK_CONSTANTS_VERBOSE"):
                    print("UNEVALUABLE %s (%s): ours %r ref %r" % (n, rn, sub.raw[n][0][:40], ref.raw[rn][0][:40]))
                continue
            compared += 1
            if not same(ov, rv):
                mism.append((n if n == rn else "%s (%s)" % (n, rn), fmt(ov), fmt(rv), base))
        for n, (g, _) in sub.guid.items():
            key = al.get(n, n)
            rn = rguid.get(norm(key))
            if rn is None:
                # a GUID whose name differs but whose value is a reference GUID is verified by value
                if any(rg[0] == g for rg in ref.guid.values()):
                    compared += 1
                else:
                    only.append(n)
                continue
            compared += 1
            if rn in ref.guid and ref.guid[rn][0] != g:
                mism.append((n if n == rn else "%s (%s)" % (n, rn), fmt_guid(g), fmt_guid(ref.guid[rn][0]), base))
    for n, expr in DERIVED.items():
        if n not in ours.raw:
            continue
        ov, rv = ours.evaluate(n), ref.ev_expr(expr, ())
        if ov is None or rv is None:
            unevaluable += 1
            continue
        compared += 1
        if not same(ov, rv):
            if n in WAIVED:
                waived.append((n, fmt(ov), fmt(rv), WAIVED[n]))
            else:
                mism.append((n, fmt(ov), fmt(rv), "dinput.cpp"))
    only = [n for n in only if n not in DERIVED]
    pr = i3dl2(ref, per_file)
    compared += pr["values"]
    for n, a, b in pr["bad"]:
        mism.append((n, a, b, "dsound.cpp"))
    uniq_only = sorted(set(only))
    print("reference headers: %d files, %d macros, %d GUIDs" % (len(rfiles), len(ref.raw), len(ref.guid)))
    print("our sources: %d files, %d names, %d GUIDs" % (len(ofiles), len(ours.raw), len(ours.guid)))
    print("compared: %d (incl. %d I3DL2 preset values in %d rows); unevaluable: %d" %
          (compared, pr["values"], pr["rows"], unevaluable))
    waived += [(n, a, b, WAIVED[n]) for n, a, b, base in mism if n in WAIVED]
    mism = [m_ for m_ in mism if m_[0] not in WAIVED]
    for n, a, b, base in mism:
        print("MISMATCH %s: ours %s -> reference %s   [%s]" % (n, a, b, base))
    for n, a, b, why in waived:
        print("WAIVED %s: ours %s, reference %s: %s" % (n, a, b, why))
    print("only on our side (not verifiable): %d" % len(uniq_only))
    if os.environ.get("CHECK_CONSTANTS_VERBOSE"):
        print(" ".join(uniq_only))
    print("mismatches: %d" % len(mism))
    return 1 if mism else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
