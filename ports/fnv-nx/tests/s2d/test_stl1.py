#!/usr/bin/env python3
"""tests/s2d - STL1 (tools/rules/s4_stl.py) on a hand-written unit: a `namespace std` copy declaring VC9's helper
templates (_Allocate, _Destroy, fill, swap) and a vector with `static void _Xlen();`, none defined. After the rule
the unit compiles (system aarch64-linux-gnu-g++ -mabi=ilp32, -O0 so nothing is inlined away) with none of them
undefined; a unit that defines them itself is left as it is. STL2 on a hand-written iostream / vector<float> model:
setstate, basic_ios::init, a UseFacet with its widen, basic_ifstream::open and vector<float>'s ctor/dtor get bodies,
calling only fnvnx_basic_ios_init and fnvnx_fiopen. Our own code only."""
import collections
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(P, 'tools', 'rules'))
import s4_stl  # noqa: E402

UNIT = r'''
typedef unsigned long size_t;
void *operator new(size_t);
namespace std {
template <class T> void swap(T&, T&);
template <class T> T* _Allocate(size_t, T*);
template <class T> void _Destroy(T*);
template <class I, class T> void fill(I, I, const T&);
struct Aux { int *p; };
template <class _Ty, class _Ax> class vector : public Aux
{
public:
    static void _Xlen();
    _Ty* grow(size_t n) { if (n > 100) _Xlen(); return _Allocate(n, (_Ty*)0); }
};
}
struct A {};
int use(unsigned *a, unsigned *b, std::Aux &x, std::Aux &y)
{
    std::fill(a, b, 7u);
    std::swap(x, y);
    std::vector<unsigned short, A> v;
    unsigned short *q = v.grow(4);
    std::_Destroy(q);
    return (int)q[0];
}
'''
UNIT2 = r'''
typedef unsigned long size_t;
void *operator new(size_t);
void operator delete(void *);
struct _iobuf { char *_ptr; int _cnt; char *_base; };
struct Loc { void *p; };
struct SB { virtual ~SB() {} };
namespace std {
template <class T> class allocator {};
class ios_base
{
  public:
    virtual ~ios_base() {}
    unsigned int standardStream;
    int state, exceptions, format, precision, width;
    void* array;
    void* callbacks;
    Loc* locale;
    Loc getloc() const { return *locale; }
    void setstate(int);
};
template <class E> struct XCtype
{
    E widen(char) const;
};
template <class E> const XCtype<E>& XUseFacet(const Loc&);
template <class E, class Tr> class basic_ios : public ios_base
{
  public:
    E wid(char c) const { return XUseFacet<E>(getloc()).widen(c); }
    void init(SB*, bool);
    SB* streambuf;
    void* tied;
    E fill;
};
class FB : public SB
{
  public:
    enum _Initfl { _Newfl, _Openfl, _Closefl };
    void _Init(struct _iobuf* f, _Initfl w) { _Myfile = f; _Closef = w == _Openfl; }
    bool _Closef;
    struct _iobuf* _Myfile;
};
template <class E, class Tr> class basic_istream : virtual public basic_ios<E, Tr>
{
  public:
    explicit basic_istream(SB* sb) { this->init(sb, false); }
};
template <class E, class Tr> class basic_ifstream : public basic_istream<E, Tr>
{
  public:
    basic_ifstream(const char* n) : basic_istream<E, Tr>(&filebuf)
    {
        filebuf._Init(0, FB::_Newfl);
        if (open(n, 1, 0x40) == 0)
            this->setstate(2);
    }
    FB* open(const char*, int, int);
    FB filebuf;
};
template <class T, class A = allocator<T> > class vector;
template <> class vector<float, allocator<float> >
{
  public:
    void* auxiliary;
    unsigned int allocatorAuxiliary;
    float* first;
    float* last;
    float* end;
    vector();
    ~vector();
};
}
struct Tr {};
char use(const char *n)
{
    std::basic_ifstream<char, Tr> f(n);
    std::vector<float> v;
    return f.wid('x') + (char)(v.first == 0);
}
'''
fails = 0


def ok(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': ' + str(info)[:600]))
    fails += not cond


def main():
    st = collections.Counter()
    out = s4_stl.r_stl_helper_bodies('src/t.cpp', UNIT, st)
    ok('five helpers get bodies', st['STL1'] == 5 and out.startswith(UNIT), st)
    st2 = collections.Counter()
    ok('a second run adds nothing (defined now)', s4_stl.r_stl_helper_bodies('src/t.cpp', out, st2) == out and
       not st2['STL1'], st2)
    ok('no std copy: unchanged', s4_stl.r_stl_helper_bodies('src/u.cpp', 'int f() { return 1; }\n',
                                                            collections.Counter()) == 'int f() { return 1; }\n')
    cxx = shutil.which('aarch64-linux-gnu-g++')
    if not cxx:
        print('SKIP compile: aarch64-linux-gnu-g++ missing')
    else:
        with tempfile.TemporaryDirectory() as t:
            for name, text in (('before', UNIT), ('after', out)):
                src, obj = os.path.join(t, name + '.cpp'), os.path.join(t, name + '.o')
                open(src, 'w').write(text)
                r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O0', '-fpermissive', '-c', src, '-o', obj],
                                   capture_output=True, text=True)
                ok('%s compiles' % name, r.returncode == 0, r.stderr)
                und = subprocess.run(['aarch64-linux-gnu-nm', '-u', obj], capture_output=True, text=True).stdout
                hits = [l.split()[-1] for l in und.splitlines() if any(k in l for k in
                                                                      ('_Allocate', '_Destroy', '4fill', '4swap', '_Xlen'))]
                if name == 'before':
                    ok('before: the helpers are undefined (the trap stubs)', len(hits) == 5, hits)
                else:
                    ok('after: none undefined', not hits, hits)
    st = collections.Counter()
    out2 = s4_stl.r_stl_stream_bodies('src/t2.cpp', UNIT2, st)
    ok('STL2: five bodies (the facet with its widen, the vector ctor with its dtor)', st['STL2'] == 5, (st, out2[len(UNIT2):]))
    ok('STL2: vector fields all zeroed', 'auxiliary(0), allocatorAuxiliary(0), first(0), last(0), end(0)' in out2,
       out2[len(UNIT2):])
    ok('STL2: a second run adds nothing', s4_stl.r_stl_stream_bodies('src/t2.cpp', out2, collections.Counter()) == out2)
    if cxx:
        with tempfile.TemporaryDirectory() as t:
            res = {}
            for name, text in (('before', UNIT2), ('after', out2)):
                src, obj = os.path.join(t, name + '.cpp'), os.path.join(t, name + '.o')
                open(src, 'w').write(text)
                r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O0', '-fpermissive', '-c', src, '-o', obj],
                                   capture_output=True, text=True)
                ok('STL2 %s compiles' % name, r.returncode == 0, r.stderr)
                res[name] = subprocess.run(['aarch64-linux-gnu-nm', '-u', obj], capture_output=True,
                                           text=True).stdout.split()
            want = ('setstate', '4init', 'XUseFacet', '5widen', '4open', '6vectorIfSaIfEEC', '6vectorIfSaIfEED')
            ok('STL2 before: the seven members undefined',
               all(any(k in s for s in res['before']) for k in want), res['before'])
            ok('STL2 after: none, only the two platform calls',
               not any(k in s for s in res['after'] for k in want) and 'fnvnx_basic_ios_init' in res['after'] and
               'fnvnx_fiopen' in res['after'], res['after'])
    print('test_stl1: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
