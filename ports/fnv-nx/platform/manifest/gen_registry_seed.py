#!/usr/bin/env python3
"""gen_registry_seed.py - the registry's first-boot seed from the installer's script (ENV_TRUTH F3).

    gen_registry_seed.py <installer-script.iss> <registry_seed.tsv>
    gen_registry_seed.py --selftest

Input: the [Registry] section of the Inno Setup script the GOG installer of this game version was built from
(innoextract-style dump of the setup header's script, ISPP-expanded). Output: platform/win32/registry_seed.cpp's format
(documented there): FNVREGSEED 1, then root TAB key TAB name TAB type TAB data per row, with the installer constant
{app} kept symbolic (the platform expands it to the guest game directory). The output is installer-derived data: it goes
to the private artifacts repository (fnv-nx-build, l5-platform/manifest, manifest/registry_seed.tsv), never into this
repository.

Rules (a whole class, no list of names): every entry of [Registry] that installs data is converted; an entry the platform
cannot represent faithfully stops the generator with the line and the reason, never skipped.
  * Root: HKLM HKCU HKCR HKU (HKCC / HKA are errors).
  * ValueType: none -> KEY row, string -> REG_SZ, expandsz -> REG_EXPAND_SZ, dword -> REG_DWORD, qword -> REG_QWORD,
    binary -> REG_BINARY (hex pairs). Anything else (multisz included): error.
  * Constants: {app} stays; "{{" stays; every other {const} (e.g. {pf}, {userdocs}) is an error, as in the platform.
  * Flags: deletekey/deletevalue entries remove data at install time, which on a fresh hive removes nothing: no row.
    uninsdelete* act at uninstall only, 32bit, createvalueifdoesntexist, dontcreatekey, preservestringtype and noerror
    do not change a fresh hive's content: the row is made. 64bit is an error (a 32-bit process cannot see that view);
    any other flag is an error.
  * Check:/Components:/Tasks: conditions: an entry with one is an error (the installer's choice is not known here)."""
import re
import sys

ROOTS = {"HKLM": "HKLM", "HKCU": "HKCU", "HKCR": "HKCR", "HKU": "HKU"}
TYPES = {"none": "KEY", "string": "REG_SZ", "expandsz": "REG_EXPAND_SZ", "dword": "REG_DWORD", "qword": "REG_QWORD",
         "binary": "REG_BINARY"}
NOROW = {"deletekey", "deletevalue"}
OK = {"uninsdeletekey", "uninsdeletekeyifempty", "uninsdeletevalue", "32bit", "createvalueifdoesntexist", "dontcreatekey", "preservestringtype", "noerror"}


def fields(line):
    """split 'Key: value; Key: "va;lue"' at the semicolons outside double quotes"""
    out, cur, q = [], "", False
    for c in line:
        if c == '"':
            q = not q
        if c == ";" and not q:
            out.append(cur)
            cur = ""
        else:
            cur += c
    out.append(cur)
    d = {}
    for f in out:
        f = f.strip()
        if not f:
            continue
        k, sep, v = f.partition(":")
        if not sep:
            raise ValueError("no 'Name: value' in %r" % f)
        v = v.strip()
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1].replace('""', '"')
        d[k.strip().lower()] = v
    return d


def check_constants(s, what):
    for m in re.finditer(r"\{[^{}]*\}?", s.replace("{{", "")):
        if m.group(0) != "{app}":
            raise ValueError("installer constant %s in %s is not representable (only {app})" % (m.group(0), what))


def convert(script):
    rows, in_reg = [], False
    for n, raw in enumerate(script.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        m = re.match(r"\[(\w+)\]$", line)
        if m:
            in_reg = m.group(1).lower() == "registry"
            continue
        if not in_reg:
            continue
        try:
            d = fields(line)
            unknown = set(d) - {"root", "subkey", "valuetype", "valuename", "valuedata", "flags", "permissions"}
            if unknown or "root" not in d or "subkey" not in d:
                raise ValueError("unknown or missing parameter %s" % sorted(unknown or {"Root/Subkey"}))
            for cond in ("check", "components", "tasks", "languages", "minversion", "onlybelowversion"):
                if cond in d:
                    raise ValueError("conditional entry (%s)" % cond)
            flags = set(d.get("flags", "").lower().split())
            if flags - NOROW - OK:
                raise ValueError("flag %s" % sorted(flags - NOROW - OK))
            if "64bit" in flags:
                raise ValueError("64bit view")
            if flags & NOROW:
                continue
            if d["root"] not in ROOTS:
                raise ValueError("root %s" % d["root"])
            vt = d.get("valuetype", "none").lower()
            if vt not in TYPES:
                raise ValueError("ValueType %s" % vt)
            key, name, data = d["subkey"], d.get("valuename", ""), d.get("valuedata", "")
            for what, s in (("Subkey", key), ("ValueName", name), ("ValueData", data)):
                check_constants(s, what)
                if "\t" in s or "\n" in s:
                    raise ValueError("tab or newline in %s" % what)
            if vt == "dword" or vt == "qword":
                data = str(int(data, 0))
            elif vt == "binary":
                if not re.fullmatch(r"([0-9a-fA-F]{2}( |$))*", data):
                    raise ValueError("binary data %r" % data)
                data = data.replace(" ", "")
            if vt == "none":
                name = data = ""
            rows.append((ROOTS[d["root"]], key, name, TYPES[vt], data))
        except ValueError as e:
            raise SystemExit("gen_registry_seed: script line %d: %s: %s" % (n, e, raw))
    if not rows:
        raise SystemExit("gen_registry_seed: the script has no [Registry] rows to install")
    return rows


def emit(rows):
    return "FNVREGSEED 1\n" + "".join("\t".join(r) + "\n" for r in rows)


def selftest():
    s = ('[Files]\nSource: x; DestDir: "{app}"\n[Registry]\n'
         'Root: HKLM; Subkey: "SOFTWARE\\Synth\\Game"; ValueType: string; ValueName: "Installed Path"; ValueData: "{app}"; Flags: uninsdeletekeyifempty\n'
         'Root: HKLM; Subkey: "SOFTWARE\\Synth\\Game"; ValueType: dword; ValueName: "V"; ValueData: "16"\n'
         'Root: HKCU; Subkey: "SOFTWARE\\Synth\\K"; Flags: uninsdeletekey\n'
         'Root: HKCU; Subkey: "SOFTWARE\\Synth\\Old"; Flags: deletekey\n')
    want = ("FNVREGSEED 1\nHKLM\tSOFTWARE\\Synth\\Game\tInstalled Path\tREG_SZ\t{app}\n"
            "HKLM\tSOFTWARE\\Synth\\Game\tV\tREG_DWORD\t16\nHKCU\tSOFTWARE\\Synth\\K\t\tKEY\t\n")
    assert emit(convert(s)) == want, emit(convert(s))
    for bad in ('[Registry]\nRoot: HKLM; Subkey: "S\\{pf}"; ValueType: string; ValueName: "a"; ValueData: "b"\n',
                '[Registry]\nRoot: HKLM; Subkey: "S"; ValueType: string; ValueData: "b"; Flags: 64bit\n',
                '[Registry]\nRoot: HKLM; Subkey: "S"; ValueType: string; ValueData: "b"; Check: X\n',
                '[Registry]\nRoot: HKCC; Subkey: "S"\n', '[Files]\n'):
        try:
            convert(bad)
        except SystemExit:
            continue
        raise AssertionError("accepted: " + bad)
    print("gen_registry_seed selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    elif len(sys.argv) == 3:
        with open(sys.argv[1], encoding="utf-8-sig", errors="strict") as f:
            out = emit(convert(f.read()))
        with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as f:
            f.write(out)
        print("gen_registry_seed: %d rows -> %s" % (out.count("\n") - 1, sys.argv[2]))
    else:
        sys.exit(__doc__)
