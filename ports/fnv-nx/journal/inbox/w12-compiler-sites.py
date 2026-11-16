import ast,glob,re,os,collections,sys
pat=re.compile(r"(^|/)(aarch64-[\w-]+-|x86_64-[\w-]+-)?(gcc|g\+\+|cc|c\+\+|clang|clang\+\+|cpp)(-\d+)?$")
CALLS={'run','Popen','call','check_call','check_output'}
def is_exec(f):
    if isinstance(f,ast.Attribute):
        b=f.value.id if isinstance(f.value,ast.Name) else ''
        return (b=='subprocess' and f.attr in CALLS) or (b=='os' and re.match(r'(exec|spawn|posix_spawn)',f.attr))
    return isinstance(f,ast.Name) and f.id in CALLS|{'check_output'}
def lit(n): return isinstance(n,ast.Constant) and isinstance(n.value,str) and pat.search(n.value.strip())
files=sorted(f for f in glob.glob('**/*.py',recursive=True) if '/tests/' in '/'+f or os.path.basename(f).startswith('test_'))
out=collections.OrderedDict()
for f in files:
    try: t=ast.parse(open(f).read())
    except Exception: continue
    # names tainted: assigned from an expression containing a driver literal or a tainted name (fixpoint, whole file)
    taint=set(); ch=True
    asg=[n for n in ast.walk(t) if isinstance(n,(ast.Assign,ast.AnnAssign,ast.AugAssign)) ]
    while ch:
        ch=False
        for a in asg:
            v=a.value
            if v is None: continue
            hit=any(lit(x) or (isinstance(x,ast.Name) and x.id in taint) for x in ast.walk(v))
            if hit:
                tg=a.targets if isinstance(a,ast.Assign) else [a.target]
                for g in tg:
                    for x in ast.walk(g):
                        if isinstance(x,ast.Name) and x.id not in taint: taint.add(x.id); ch=True
    # function params named after compilers count too: a function whose default or call passes literal handled by taint
    sites=[]
    for n in ast.walk(t):
        if isinstance(n,ast.Call) and is_exec(n.func) and n.args:
            a0=n.args[0]
            if any(lit(x) or (isinstance(x,ast.Name) and x.id in taint) for x in ast.walk(a0)): sites.append(n.lineno)
    if sites: out[f]=sites
print('commit',os.popen('git rev-parse --short HEAD').read().strip(),'; test files',len(files),'; files with sites',len(out),'; sites',sum(len(v) for v in out.values()))
for f,v in out.items(): print(len(v),f,v)
