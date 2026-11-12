#!/usr/bin/env python3
"""The programs a set of shell scripts invokes, read from their text (stdlib only).
    python3 -I script_programs.py FILE.sh... [--json]
Prints one command word per line: every word in command position of a simple command (after `;` `&&` `||` `|` `(` `)` `{` `}` `$(` a
backtick, a newline, `then` `do` `else`; leading VAR=value assignments, `!`, `exec`, `command`, `env`, `time`, `sudo` skipped), minus shell
builtins and keywords, `[` `[[` `:` and the functions the scripts define. A word that is not a literal (it starts with $ or a quote)
is printed with a leading `?`: such a command is dynamic and its value is a build input to be declared. Here-documents are not shell
(their opener's command is still read) and comments are dropped. A word with a `/` is a path to a file of the build, not a program of the
host, and is left out; a program the scripts reach through PATH is what the profile has to provide."""
import re
import sys

KEYWORDS = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "in", "function", "select", "!",
            "{", "}", "time", "exec", "command", "env", "sudo", "nohup", "builtin"}
BUILTINS = {":", "[", "[[", "true", "false", "cd", "echo", "printf", "read", "set", "export", "unset", "shift", "exit", "return", "trap",
            "source", ".", "eval", "test", "local", "declare", "readonly", "wait", "umask", "type", "hash", "ulimit", "pwd", "alias",
            "getopts", "let", "break", "continue", "mapfile", "readarray", "typeset", "kill", "jobs", "fg", "bg", "pushd", "popd"}


def strip_quotes(seg):
    """Same-length copy of SEG with the contents of single- and double-quoted strings blanked (so that separators inside them do not
    split); a `$(` inside double quotes is kept (as `; `) with its text up to the matching `)` (closed as `;`), so the command inside
    is still read."""
    out, i, q, depth = list(seg), 0, None, 0
    while i < len(seg):
        c = seg[i]
        if q is None:
            if c in "'\"":
                q = c
            elif c == "\\":
                out[i] = " "
                if i + 1 < len(seg):
                    out[i + 1] = " "
                i += 1
        elif depth:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    out[i] = ";"
            elif c in "\"'":
                out[i] = " "
        elif c == q:
            q = None
        elif q == '"' and c == "$" and seg[i + 1:i + 2] == "(":
            out[i], out[i + 1] = " ", ";"
            depth = 1
            i += 1
        else:
            out[i] = " "
        i += 1
    return "".join(out)


def commands(text):
    funcs = set(re.findall(r"^\s*(?:function\s+)?([A-Za-z_][\w.-]*)\s*\(\)", text, re.M))
    lines, hd = [], None
    for ln in text.split("\n"):
        if hd:
            if ln.strip() == hd:
                hd = None
            continue
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1", ln)
        lines.append(ln)
        if m:
            hd = m.group(2)
    found = []
    joined = []
    for ln in lines:   # join backslash continuations
        if joined and joined[-1].endswith("\\"):
            joined[-1] = joined[-1][:-1] + " " + ln.strip()
        else:
            joined.append(ln)
    arr = 0
    for ln in joined:
        ln = re.sub(r"(^|\s)#.*$", "", ln)
        if arr:   # inside NAME=( ... ): words, not commands
            arr += ln.count("(") - ln.count(")")
            continue
        if re.match(r"^\s*[A-Za-z_]\w*=\([^)]*$", ln):
            arr = 1
            continue
        s = strip_quotes(ln)
        # keep the raw words: split the stripped line to get the positions, read words from the original
        parts = re.split(r"(;|&&|\|\||\||\(|\)|\{|\}|`|\$\(|&(?!>)|\bthen\b|\bdo\b|\belse\b)", s)
        pos, prev = 0, ";"
        for p in parts:
            raw = ln[pos:pos + len(p)]
            pos += len(p)
            if p in (";", "&&", "||", "|", "(", ")", "{", "}", "`", "$(", "&", "then", "do", "else"):
                prev = p
                continue
            if prev in (")", "}"):   # what follows a closing bracket is not a command position (the rest of a test, a case pattern)
                prev = ";"
                continue
            words = [w for w in raw.split() if not re.match(r"^[\"']+$", w)]   # a lone quote is the rest of a string, not a word
            while words:
                w = words[0]
                if re.match(r"^[A-Za-z_]\w*=", w) or w in KEYWORDS:
                    words.pop(0)
                    continue
                if re.match(r"^\d*[<>]", w):   # a redirection before the command
                    words.pop(0)
                    continue
                break
            if not words:
                continue
            w = words[0]
            if w[0] in "$\"'" or w.startswith("${"):
                found.append("?" + w)
            elif "/" in w or w in BUILTINS or w in funcs or not re.match(r"^[A-Za-z][A-Za-z0-9_.+-]*$", w):
                continue
            else:
                found.append(w)
    return found


def main(a):
    files = [x for x in a if not x.startswith("--")]
    names = sorted({c for f in files for c in commands(open(f, encoding="utf-8").read())})
    if "--json" in a:
        import json
        print(json.dumps(names))
    else:
        print("\n".join(names))


if __name__ == "__main__":
    main(sys.argv[1:])
