#!/usr/bin/env python3
"""glmin.py - the OpenGL version the GL backend needs, derived from the pinned Khronos registry (fnv-nx-build tools/khronos-glxml, xml/gl.xml).
  python3 glmin.py GL_XML --declared 4.5 NAME...      NAMEs are the gl* entry points the backend references (the undefined symbols of its object, or its loader table).
The minimum core version of a command is the first `<feature api="gl">` whose `<require>` (no profile, or profile="core") lists it and no later `<remove profile="core">`
takes it out. The backend's minimum is the maximum over the commands it calls; the declared `host_gl.version` of verify/targets.json must equal it (above or
below is an error: a version above the need asks for hardware the backend does not use, below is a call that the version does not have). A command that no
core feature requires (an extension entry point) has no core version: that is an error too. Nothing is decided by a name or a list: the registry is the rule.
Exit 0 when the declaration equals the derived minimum, 1 with the reasons otherwise."""
import sys
import xml.etree.ElementTree as ET


def core_minimum(xml_text):
    """{command: (major, minor)}: the first feature of api gl that requires the command in the core profile, and does not remove it from core later."""
    root = ET.fromstring(xml_text)
    first, gone = {}, set()
    for f in root.iter("feature"):
        if f.get("api") != "gl":
            continue
        ver = tuple(int(x) for x in f.get("number").split("."))
        for blk in f:
            if blk.tag == "require" and blk.get("profile") in (None, "core"):
                for c in blk.iter("command"):
                    first.setdefault(c.get("name"), ver)
                    gone.discard(c.get("name"))
            elif blk.tag == "remove" and blk.get("profile") == "core":
                gone.update(c.get("name") for c in blk.iter("command"))
    return {k: v for k, v in first.items() if k not in gone}


def required(table, names):
    """(version, {name: version}, [names with no core version]) for the NAMEs the backend references."""
    per = {n: table[n] for n in names if n in table}
    return (max(per.values()) if per else None), per, sorted(n for n in names if n not in table)


def judge(table, declared, names):
    """Error strings: [] when DECLARED ("4.5") equals the maximum minimum core version of NAMES and every name has one."""
    ver, per, none = required(table, set(names))
    err = ["%s has no core version in the registry" % n for n in none]
    if ver is None:
        return err + ["no gl* command referenced"]
    d = tuple(int(x) for x in declared.split("."))
    if d != ver:
        who = sorted(n for n, v in per.items() if v == ver)[:3]
        err.append("declared %s, derived minimum %d.%d (first required by %s)" % (declared, ver[0], ver[1], ", ".join(who)))
    return err


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 4 or a[1] != "--declared":
        print("usage: glmin.py GL_XML --declared 4.5 NAME...", file=sys.stderr)
        sys.exit(2)
    errs = judge(core_minimum(open(a[0], encoding="utf-8").read()), a[2], a[3:])
    print("\n".join(errs) or "gl version %s equals the derived minimum" % a[2])
    sys.exit(1 if errs else 0)
