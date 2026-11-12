#!/usr/bin/env python3
"""specdefaults: the flags that make a distribution compiler behave as the reference compiler (RECIPE.md "Distribution spec defaults").

    python3 -I specdefaults.py derive --ref CC --distro CC [--lang c|c++] [--out FILE.json]

Why.  A distribution builds its GCC with a spec file that adds defaults the GNU release does not have: -fstack-protector-strong,
-fstack-clash-protection, -fcf-protection, default PIE, and, in the preprocessor spec, -D_FORTIFY_SOURCE (and -D_GLIBCXX_ASSERTIONS).
They change the code that comes out (a `__stack_chk_fail` or `__memcpy_chk` call that exists in no source).  The reference is the GNU GCC
(the msvcmodel compiler of the guest root, built from the pinned GNU tarball without a distribution spec).  The rule is not a list of
names: it is the DIFFERENCE of the two compilers' own reports.

Rule.  Run both compilers with the same arguments on the same null translation unit and read their reports:
  options  `-Q --help=common`, `=target`, `=optimizers` (one run each): every line `-name  VALUE`.  An option the two compilers report with different VALUEs, and the
           distribution's own spelling is known (the reference has it too), is set to the reference's VALUE:
             [enabled]  -> the option itself          (-fstack-clash-protection)
             [disabled] -> its off form               (-fno-stack-protector; -fno-PIE; -mno-...)
             =VALUE     -> `-name=VALUE`              (-fcf-protection=none)
           An option only the distribution has cannot differ in effect from the reference (the reference does not know it): not emitted.
  macros   `-dM -E`: a macro the distribution defines and the reference does not  -> `-UNAME`   (_FORTIFY_SOURCE, _GLIBCXX_ASSERTIONS);
           a macro both define with different values                               -> `-DNAME=VALUE` of the reference;
           a macro only the reference defines                                      -> `-DNAME=VALUE`.
Two families. Forced: hardening and semantic defaults (always neutralised; a root may not set them). Target: the options of the driver's `-Q --help=target` plus the
PIC/PIE options (specdefaults.is_target): NOT neutralised, the root must state each one whose distribution default differs from the reference, else
`implicit target choice`. A token of the cc1 line is a spec default only where `-Q --help` does not report the option's effective value.
The result is a function of the two reports only: sorted, no host state.  Applying it (recipe.py) is a stage keyed on both compilers; a root
may not set an option of the result or its negation (`overlap`).  An option whose value text is a list ("[default]", "[disabled]|…") is read
as a value only in the forms above; any other form is emitted as `-name=VALUE` taken verbatim from the reference.
Rests on the compiler being correct (item 1 of the owner's accepted list): `-Q --help` and `-dM -E` report every default of the compiler that changes
code generation or the preprocessed text (GCC manual, Developer Options "-Q --help", Preprocessor Options "-dM")."""

import json
import re
import shlex
import subprocess
import sys

FORMAT = "fnvnx-specdefaults/1"
HELP = ("-Q --help=common", "-Q --help=target", "-Q --help=optimizers")   # one run per class: a comma list is their intersection
LINE = re.compile(r"^\s+(-\S+?)\s+(\S.*?)\s*$")
MACRO = re.compile(r"^#define (\w+(?:\([^)]*\))?) ?(.*)$")
# reported values that are not a state of the option: skipped
SKIP_VALUE = re.compile(r"^(\[default\]|\[(?:[\w-]+\|)+[\w-]+\])$")


def parse_help(text):
    """-Q --help output -> {option: value}; value is "[enabled]", "[disabled]" or the compiler's own value text."""
    out = {}
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            name, val = m.groups()
            if name.endswith("=") or "[" in name or SKIP_VALUE.match(val.strip()) or not re.fullmatch(r"\[(?:enabled|disabled)\]|[\w.+:=-]+", val.strip()):
                continue   # a state of the option, or its one-token value; "<file> /tmp/..." and the like are not options of the code
            out[name] = val.strip()
    return out


def parse_macros(text):
    """-dM -E output -> {name: value}"""
    out = {}
    for line in text.splitlines():
        m = MACRO.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def off_form(name):
    """-fX -> -fno-X; -mX -> -mno-X; -fno-X -> -fX (the reference has the option on while the name is already negative)."""
    for p in ("-f", "-m"):
        if name.startswith(p + "no-"):
            return p + name[len(p) + 3:]
        if name.startswith(p):
            return p + "no-" + name[len(p):]
    return None


def candidates(name, ref_value):
    """The flags that may give option NAME the value the reference reports, most specific first (the compiler decides which one exists)."""
    if ref_value == "[enabled]":
        return [name]
    if ref_value == "[disabled]":
        o = off_form(name)
        if o is None:
            return []
        # -fstack-protector-strong has no -fno- form of its own: its family's off form (-fno-stack-protector) switches it off
        parts = o.split("-")   # ["", "fno", "stack", "protector", "strong"]
        pre, rest = "-" + parts[1], parts[2:]
        return [pre + "-" + "-".join(rest[:n]) for n in range(len(rest), 0, -1)]
    if ref_value.startswith("-"):   # the report spells the whole option (-fcf-protection -> -fcf-protection=full)
        return [ref_value.split()[0]]
    return ["%s=%s" % (name, ref_value)] if re.fullmatch(r"[\w.+:-]+", ref_value) else []


def differing(ref_help, dist_help):
    """-> {option: reference value} of the options both compilers report with different values"""
    return {n: rv for n, rv in ref_help.items() if dist_help.get(n) is not None and dist_help[n] != rv}


def derive_from(ref_help, dist_help, ref_macros, dist_macros, accepts=lambda f: True):
    """-> sorted list of flags.  Pure function of the four reports (parse_help / parse_macros results) and of ACCEPTS(flag) (does the
    distribution compiler know this flag)."""
    out = set()
    for name, rv in differing(ref_help, dist_help).items():
        f = next((c for c in candidates(name, rv) if accepts(c)), None)
        if f:
            out.add(f)
    for name, dv in dist_macros.items():
        if name not in ref_macros and "(" not in name:
            out.add("-U" + name)
    for name, rv in ref_macros.items():
        if "(" not in name and dist_macros.get(name) != rv:
            out.add("-D%s=%s" % (name, rv) if rv else "-D" + name)
    return sorted(out)


def names_of(flags):
    """The option names a flag set speaks of, with their negations: what a root may not set."""
    out = set()
    for f in flags:
        if f.startswith(("-U", "-D")):
            out.add("-U" + re.split(r"[=]", f[2:], 1)[0])
            out.add("-D" + re.split(r"[=]", f[2:], 1)[0])
            continue
        base = f.split("=", 1)[0]
        out.add(base)
        o = off_form(base)
        if o:
            out.add(o)
        # an option with a value form (-fcf-protection=none): every spelling of the same option
        if "=" in f:
            out.add(base + "=")
    return out


def overlap(root_flags, derived):
    """-> the tokens of ROOT_FLAGS that set an option the derived set already fixes: the same option under any value or sign, and the options of
    its family (-fno-stack-protector fixes -fstack-protector, -fstack-protector-strong, -fstack-protector-all, ...)."""
    names = names_of(derived)
    fam = {n for n in names if n.startswith(("-f", "-m")) and "=" not in n}
    r = []
    for t in root_flags:
        if t.startswith(("-U", "-D")):
            key = t[:2] + re.split(r"[=]", t[2:], 1)[0]
            hit = key in names
        else:
            key = t.split("=", 1)[0]
            hit = key in names or (key + "=") in names or any(key == f or key.startswith(f + "-") for f in fam)
        if hit:
            r.append(t)
    return r


# Family (2), target and code-model choices: the options the driver's own `-Q --help=target` lists, plus the PIC/PIE code-generation options.  They are
# NOT neutralised: where the distribution's default differs from the reference, the root must STATE the option (any value) in its flags, so no distribution
# default applies silently and the root's choice is in its key.  Family (1), everything else (hardening, unwind tables, FORTIFY ...), is always neutralised and
# a root may not set it.  The classification comes from the driver (target_names), never from a list kept by hand; only the PIC/PIE group is named here
# because the driver does not list the generic code-generation options under `target`.
PIC = ("-fPIC", "-fpic", "-fPIE", "-fpie")


def norm(flag):
    """-fno-X -> -fX, -mno-X -> -mX, a value dropped: the name of the option a flag sets"""
    base = flag.split("=", 1)[0]
    for p in ("-fno-", "-mno-"):
        if base.startswith(p):
            return "-" + base[1] + base[len(p):]
    return base


def option_names(text):
    """names of the options a `-Q --help=CLASS` report lists (normalised, `=VALUE` and `[..]` dropped)"""
    out = set()
    for line in text.splitlines():
        m = re.match(r"^\s+(-[\w+-]+)", line)
        if m:
            out.add(norm(m.group(1)))
    return out


def is_target(flag, tnames):
    return flag[:2] not in ("-U", "-D") and (norm(flag) in tnames or norm(flag) in PIC)


def group(flag):
    return set(PIC) if norm(flag) in PIC else {norm(flag)}


def for_root(root_flags, derived):
    """DERIVED {"forced": [...], "target": [...]} -> (flags that apply to the root, [reasons the root is red]).  Forced flags apply and the root may not set
    them; for every target flag the root must state its option (any member of the PIC group for PIC/PIE) or it is an implicit target choice."""
    stated = {norm(t) for t in root_flags}
    err = ["implicit target choice: the distribution default of %s differs from the reference; the root states none of %s in its flags" % (
        t, " ".join(sorted(group(t)))) for t in derived["target"] if not (stated & group(t))]
    tn = {norm(t) for t in derived["target"]} | set(PIC)
    err += ["%s sets an option the distribution compiler's spec default fixes" % t for t in overlap([t for t in root_flags if norm(t) not in tn], derived["forced"])]
    return list(derived["forced"]), err


LEVELS = ("-O0", "-O2")   # a spec default may depend on the optimisation level (_FORTIFY_SOURCE needs -O1 or more)


class SpecError(Exception):
    """A difference between the compilers that no flag removes (reported, never skipped)."""


def run(cc, argv):
    return subprocess.run([cc] + list(argv), capture_output=True, text=True)


def help_report(cc, level, extra=()):
    # no input file: with one, -Q --help prints nothing
    return "".join(run(cc, [level] + c.split() + list(extra)).stdout for c in HELP)


def macro_report(cc, lang, level, extra=()):
    r = run(cc, ["-dM", "-E", "-x", lang, level] + list(extra) + ["/dev/null"])
    if r.returncode:
        raise SpecError("%s -dM -E failed: %s" % (cc, r.stderr.strip()[-200:]))
    return r.stdout


TOKEN = re.compile(r"^-(?:[fm][\w+-]*(?:=[\w.+:-]+)?|D\w+(?:=.*)?|U\w+)$")


def cc1_tokens(cc, lang, level, extra=()):
    """The option tokens (-f -m -D -U) of the compiler proper's own command line for a null translation unit (`-###`: the driver's spec
    defaults are in it, whether or not `-Q --help` reports them)."""
    r = run(cc, ["-###", "-c", "-x", lang, level] + list(extra) + ["/dev/null", "-o", "/dev/null"])
    line = next((l for l in r.stderr.splitlines() if "/cc1" in l), None)
    if line is None:
        raise SpecError("%s -### shows no compiler proper: %s" % (cc, r.stderr.strip()[-200:]))
    return {t for t in shlex.split(line)[1:] if TOKEN.match(t)}


def neutral(tok):
    """Candidate flags that undo the token TOK of a distribution compiler's command line (not in the reference's)."""
    if tok.startswith("-D"):
        return ["-U" + re.split("=", tok[2:], 1)[0]]
    name, _, val = tok.partition("=")
    if val:
        return [name + "=none", name + "=off", name + "=0"] + [c for c in candidates(name, "[disabled]")]
    return candidates(name, "[disabled]") + [name + "=none"]   # a bare token of a valued option (-fcf-protection): its value "none"


def accepts_by(cc, lang):
    def accepts(flag):
        return run(cc, [flag, "-fsyntax-only", "-x", lang, "/dev/null"]).returncode == 0
    return accepts


def derive(ref_cc, dist_cc, lang="c"):
    """-> {"forced": [...], "target": [...]} for the distribution compiler DIST_CC against the reference REF_CC (driver paths); both lists sorted.
    Step 1: the option flags: help differences over every level of LEVELS (the flag is the first spelling the distribution compiler accepts), and the
    tokens of the distribution's own compiler command line that the reference's does not have, for options the help does not report (-fcf-protection).
    Step 2: closure: with those flags the help of the distribution compiler has no difference left (else SpecError: no rule closes it).
    Step 3: the macros that still differ with the option flags applied (a macro that follows from an option, like __PIE__, is a consequence).
    A flag is `target` when its option is one the driver lists under `-Q --help=target` or a PIC/PIE option (see TARGET), else `forced`."""
    acc = accepts_by(dist_cc, lang)
    rh = {lv: parse_help(help_report(ref_cc, lv)) for lv in LEVELS}
    dh = {lv: parse_help(help_report(dist_cc, lv)) for lv in LEVELS}
    rt = {lv: cc1_tokens(ref_cc, lang, lv) for lv in LEVELS}
    tnames = option_names(run(dist_cc, ["-Q", "--help=target"]).stdout)
    out = {f for lv in LEVELS for f in derive_from(rh[lv], dh[lv], {}, {}, acc)}

    def reported(t, lv):   # the help states this option's effective value: the help difference is authoritative (a default the target gives is no spec default)
        return norm(t) in {norm(n) for h in (rh[lv], dh[lv]) for n, v in h.items() if v in ("[enabled]", "[disabled]")}
    for lv in LEVELS:
        for t in sorted(cc1_tokens(dist_cc, lang, lv) - rt[lv]):
            if not t.startswith(("-D", "-U")) and not reported(t, lv):
                f = next((c for c in neutral(t) if acc(c)), None)
                if f is None:
                    raise SpecError("%s: the spec default %s has no flag that undoes it" % (dist_cc, t))
                out.add(f)
    opts = sorted(out)
    for lv in LEVELS:
        left = differing(rh[lv], parse_help(help_report(dist_cc, lv, opts)))
        left_t = {t for t in cc1_tokens(dist_cc, lang, lv, opts) - rt[lv] - set(opts)
                  if not t.startswith(("-D", "-U")) and not reported(t, lv) and not any(c in opts for c in neutral(t))}
        if left or left_t:
            raise SpecError("%s differs from %s after %s: %s" % (dist_cc, ref_cc, " ".join(opts), ", ".join(sorted(set(left) | left_t))))
    macros = set()
    for lv in LEVELS:
        rm = parse_macros(macro_report(ref_cc, lang, lv))
        dm = parse_macros(macro_report(dist_cc, lang, lv, opts))
        macros |= set(derive_from({}, {}, rm, dm))
    return {"forced": sorted(macros | {f for f in opts if not is_target(f, tnames)}), "target": sorted(f for f in opts if is_target(f, tnames))}


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="specdefaults")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("derive")
    d.add_argument("--ref", required=True)
    d.add_argument("--distro", required=True)
    d.add_argument("--lang", default="c", choices=("c", "c++"))
    d.add_argument("--out")
    a = ap.parse_args(argv)
    flags = derive(a.ref, a.distro, a.lang)
    doc = {"format": FORMAT, "lang": a.lang, "forced": flags["forced"], "target": flags["target"]}
    if a.out:
        with open(a.out, "w") as f:
            json.dump(doc, f, sort_keys=True, indent=1)
            f.write("\n")
    print(json.dumps(doc, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
