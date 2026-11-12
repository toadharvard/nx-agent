#!/usr/bin/env python3
"""check_dupdecl.py - MEASUREMENT, not the gate (the gate is L1's): counts top-level extern function and variable declarations by name over the platform, shim, loader and toolchain include trees and lists the names declared in more than one header. Regex-based (bodies skipped, extern "C" blocks transparent); run from ports/fnv-nx."""
import re,sys,glob,collections,os
dirs=['platform/include','shim/include','loader/include','toolchain/include']
def strip(s):
    s=re.sub(r'/\*.*?\*/',' ',s,flags=re.S); s=re.sub(r'//[^\n]*',' ',s)
    s=re.sub(r'^\s*#.*?(?<!\\)\n','\n',s,flags=re.M)  # preprocessor lines (single-line; continuation handled below)
    return s
def decls(path):
    t=open(path,errors='replace').read()
    t=re.sub(r'\\\n',' ',t); t=strip(t)
    out=[];depth=0;buf='';stack=[]
    i=0
    # top-level statements: split at ';' or '{...}' at depth 0 (extern "C" { is transparent)
    t=re.sub(r'extern\s*"C(\+\+)?"\s*\{','\x01',t)
    for ch in t:
        if ch=='\x01': stack.append('x'); continue
        if ch=='{':
            if depth==0: out.append(('body',buf)); buf=''
            depth+=1; continue
        if ch=='}':
            if depth==0 and stack: stack.pop(); buf=''; continue
            depth-=1
            if depth==0: buf=''
            continue
        if depth>0: continue
        if ch==';':
            out.append(('stmt',buf)); buf=''
        else: buf+=ch
    return out
fn=re.compile(r'^\s*(?:extern\s+)?(?!typedef|static|using|struct\s+\w+\s*$|enum|class)([\w\s\*&:<>,]+?)\b(\w+)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*(?:__attribute__\s*\(\(.*?\)\)\s*)?(?:noexcept\s*)?$',re.S)
var=re.compile(r'^\s*extern\s+(?!"C)([\w\s\*&:<>,]+?)\b(\w+)\s*(\[[^\]]*\])?\s*$',re.S)
res=collections.defaultdict(list)
for d in dirs:
    for p in sorted(glob.glob(d+'/**/*.h',recursive=True)+glob.glob(d+'/**/*.hpp',recursive=True)):
        for kind,s in decls(p):
            if kind!='stmt' or 'typedef' in s.split('(')[0] : continue
            s=' '.join(s.split())
            s=re.sub(r'^(extern\s+)?"C(\+\+)?"\s+(extern\s+)?',lambda m:'extern ',s)
            m=fn.match(s)
            if m and '(*' not in s.split(m.group(2))[0][-3:] :
                res[m.group(2)].append((p,'fn',s)); continue
            m=var.match(s)
            if m: res[m.group(2)].append((p,'var',s))
dup={k:v for k,v in res.items() if len({p for p,_,_ in v})>1}
tot=len(res)
print('declared names:',tot,' names declared in more than one header:',len(dup))
for k,v in sorted(dup.items()):
    print(k,' | '.join(sorted({p for p,_,_ in v})))
