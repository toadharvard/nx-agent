"""check_test_threads.py - no test starts a thread except through the harness helpers (tests/test_threads.h): a test file under
tests/s4, tests/s4/m32 or tests/s7 may not name a thread start API (code only: comments and string literals are skipped); the harness
files s4_test.h, s7_test.h, test_threads.h and the runners' main.cpp are the only places that may. Exit 1 and the list on a violation."""
import re,glob,os,sys
R=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','..'))
API=re.compile(r'(?<![A-Za-z0-9_])(CreateThread|_beginthreadex|_beginthread|pthread_create|os_thread_create|QueueUserWorkItem|std::thread|std::jthread|std::async)(?![A-Za-z0-9_])')
HARNESS={'s4_test.h','s7_test.h','test_threads.h','main.cpp'}
def code_only(s):
    out=[];i=0;n=len(s)
    while i<n:
        c=s[i]
        if s.startswith('//',i):
            j=s.find('\n',i); j=n if j<0 else j; out.append(' '*(j-i)); i=j
        elif s.startswith('/*',i):
            j=s.find('*/',i+2); j=n if j<0 else j+2; out.append(re.sub(r'[^\n]',' ',s[i:j])); i=j
        elif c in '"\'':
            j=i+1
            while j<n and s[j]!=c:
                j+=2 if s[j]=='\\' else 1
            out.append(re.sub(r'[^\n]',' ',s[i:j+1])); i=j+1
        else: out.append(c); i+=1
    return ''.join(out)
def scan(files):
    bad=[]
    for f in files:
        if os.path.basename(f) in HARNESS: continue
        s=code_only(open(f,errors='replace').read())
        for m in API.finditer(s):
            bad.append((os.path.relpath(f,R),s[:m.start()].count('\n')+1,m.group(1)))
    return bad
if len(sys.argv)>1 and sys.argv[1]=='--selftest':
    import tempfile
    d=tempfile.mkdtemp(); p=d+'/x.cpp'
    open(p,'w').write('// CreateThread(\nconst char*s="pthread_create(";\nvoid f(){ std::thread t; CreateThread(0,0,0,0,0,0); }\n')
    b=scan([p]); assert [x[2] for x in b]==['std::thread','CreateThread'],b
    print('check_test_threads selftest: ok'); sys.exit(0)
files=sorted(glob.glob(R+'/tests/s4/*.cpp')+glob.glob(R+'/tests/s4/*.h')+glob.glob(R+'/tests/s4/m32/*.cpp')+glob.glob(R+'/tests/s4/m32/*.h')+glob.glob(R+'/tests/s7/*.cpp')+glob.glob(R+'/tests/s7/*.h'))
bad=scan(files)
for f,l,n in bad: print('%s:%d: %s outside the harness (use test_CreateThread / test_pthread_create / test_thread, tests/test_threads.h)'%(f,l,n))
print('check_test_threads: %d test files, %d thread start calls outside the harness (must be 0)'%(len(files),len(bad)))
sys.exit(1 if bad else 0)
