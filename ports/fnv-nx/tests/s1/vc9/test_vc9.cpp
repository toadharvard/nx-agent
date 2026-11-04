// tests/s1/vc9/test_vc9.cpp - behaviour of shim/include/fnvnx_vc9.h (STL2): VC9's layouts, growth policies,
// string algorithms and the red-black tree. Built for the host with -m32 (4-byte pointers, as MSVC x86) by
// tests/s1/test_vc9.sh. Our own test, nothing of the decomp.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include "fnvnx_vc9.h"

static int g_fail, g_invalid;
extern "C" void _invalid_parameter_noinfo(void) { ++g_invalid; }
#define CHECK(c) do { if (!(c)) { std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); ++g_fail; } } while (0)

namespace v = fnvnx_vc9;

typedef v::map<int, int> IntMap;
struct MapProbe : IntMap {   // the tree's internals, to check the red-black invariants
    int check(_Nodeptr n, _Nodeptr parent, int &count) const
    {
        if (_Isnil(n)) return 1;
        ++count;
        if (_Parent(n) != parent) return -1000;
        if (_Color(n) == _Red && (_Color(_Left(n)) == _Red || _Color(_Right(n)) == _Red)) return -1000;
        if (!_Isnil(_Left(n)) && !(_Key(_Left(n)) < _Key(n))) return -1000;
        if (!_Isnil(_Right(n)) && !(_Key(n) < _Key(_Right(n)))) return -1000;
        int l = check(_Left(n), n, count), r = check(_Right(n), n, count);
        if (l != r || l < 0) return -1000;
        return l + (_Color(n) == _Black ? 1 : 0);
    }
    bool valid() const
    {
        int count = 0;
        if (_Color(_Myhead) != _Black || !_Isnil(_Myhead)) return false;
        if (!_Isnil(_Root()) && (_Color(_Root()) != _Black || _Parent(_Root()) != _Myhead)) return false;
        if (check(_Root(), _Myhead, count) < 0 || (size_type)count != _Mysize) return false;
        if (_Mysize && (_Lmost() != _Min(_Root()) || _Rmost() != _Max(_Root()))) return false;
        return true;
    }
};

int main()
{
    // ---- string: SSO, VC9 growth (15, 31, 47, 70, 105, 157), algorithms against libstdc++
    v::string s;
    CHECK(s.capacity() == 15 && s.size() == 0 && s.c_str()[0] == 0);
    unsigned caps[8], ncap = 0, last = s.capacity();
    for (int i = 0; i < 150; ++i)
    {
        s += (char)('a' + i % 26);
        if (s.capacity() != last && ncap < 8) caps[ncap++] = last = s.capacity();
    }
    CHECK(ncap >= 5 && caps[0] == 31 && caps[1] == 47 && caps[2] == 70 && caps[3] == 105 && caps[4] == 157);
    std::string ref;
    for (int i = 0; i < 150; ++i) ref += (char)('a' + i % 26);
    CHECK(std::string(s.c_str()) == ref && s.size() == 150);
    CHECK(s.find("xyz") == ref.find("xyz") && s.find("xyz", 30) == ref.find("xyz", 30));
    CHECK(s.rfind("abc") == ref.rfind("abc") && s.rfind('q', 100) == ref.rfind('q', 100));
    CHECK(s.find_first_of("zy", 5) == ref.find_first_of("zy", 5) && s.find_last_of("ab") == ref.find_last_of("ab"));
    CHECK(s.find_first_not_of("abc") == ref.find_first_not_of("abc"));
    CHECK(s.find_last_not_of("tsr") == ref.find_last_not_of("tsr"));
    CHECK(s.find("nope") == v::string::npos);
    v::string t = s.substr(10, 5);
    CHECK(t == "klmno" && t.size() == 5 && t.capacity() == 15);
    t.insert(2, "XY");
    CHECK(t == "klXYmno");
    t.erase(1, 3);
    CHECK(t == "kmno");
    t.replace(1, 2, "123456");
    CHECK(t == "k123456o");
    t.append(t, 1, 3);             // from itself
    CHECK(t == "k123456o123");
    t.assign(t.c_str() + 3, 4);    // _Inside
    CHECK(t == "3456");
    v::string u = "abc" + t + 'z' + "!";
    CHECK(u == "abc3456z!" && u.compare("abc") > 0 && v::string("ab") < v::string("abc"));
    u.resize(2);
    CHECK(u == "ab" && u.capacity() == 15);
    u.reserve(100);
    CHECK(u.capacity() == 111 && u == "ab");   // _Grow(100, true): 100 | 15
    u.swap(t);
    CHECK(u == "3456" && t == "ab");
    std::string conv = u;          // to the port's std::string
    CHECK(conv == "3456");
    v::string w(u.begin(), u.end());
    CHECK(w == "3456");
    try { u.at(10); CHECK(false); } catch (std::out_of_range &) {}
    CHECK(g_invalid == 0);

    // ---- vector: VC9 growth (+ half), insert / erase, the swap move of elements
    v::vector<int> vi;
    unsigned vcaps[12], nv = 0;
    for (int i = 0; i < 30; ++i)
    {
        unsigned c = vi.capacity();
        vi.push_back(i);
        if (vi.capacity() != c && nv < 12) vcaps[nv++] = vi.capacity();
    }
    CHECK(nv >= 9 && vcaps[0] == 1 && vcaps[1] == 2 && vcaps[2] == 3 && vcaps[3] == 4 && vcaps[4] == 6 &&
          vcaps[5] == 9 && vcaps[6] == 13 && vcaps[7] == 19 && vcaps[8] == 28);
    vi.insert(vi.begin() + 3, 2, -1);
    CHECK(vi.size() == 32 && vi[2] == 2 && vi[3] == -1 && vi[4] == -1 && vi[5] == 3);
    vi.erase(vi.begin(), vi.begin() + 5);
    CHECK(vi.size() == 27 && vi[0] == 3 && vi.back() == 29);
    vi.resize(3);
    CHECK(vi.size() == 3 && vi.capacity() == 42);
    v::vector<int> vj(vi);
    CHECK(vj.size() == 3 && vj.capacity() == 3 && vj == vi);
    vj.insert(vj.end(), vi.begin(), vi.end());
    CHECK(vj.size() == 6 && vj[5] == 5);
    v::vector<v::string> vs;
    for (int i = 0; i < 20; ++i) vs.push_back(v::string(40, (char)('a' + i)));
    vs.erase(vs.begin() + 1);
    CHECK(vs.size() == 19 && vs[0][0] == 'a' && vs[1][0] == 'c' && vs[18].size() == 40);
    vs.insert(vs.begin(), v::string("x"));
    CHECK(vs[0] == "x" && vs[1][0] == 'a');
    // checked iterators: comparing iterators of different vectors is reported, as _SECURE_SCL does
    (void)(vi.begin() == vj.begin());
    CHECK(g_invalid == 1);
    g_invalid = 0;

    // ---- map: ordering, red-black invariants under random insert / erase, copy, operator[]
    MapProbe m;
    std::map<int, int> rm;
    std::srand(12345);
    for (int round = 0; round < 4000; ++round)
    {
        int k = std::rand() % 500;
        if (std::rand() % 3)
        {
            bool a = m.insert(IntMap::value_type(k, round)).second, b = rm.insert(std::make_pair(k, round)).second;
            CHECK(a == b);
        }
        else
            CHECK(m.erase(k) == rm.erase(k));
        if (round % 97 == 0) CHECK(m.valid());
    }
    CHECK(m.valid() && m.size() == rm.size());
    std::map<int, int>::iterator ri = rm.begin();
    bool same = true;
    for (IntMap::iterator it = m.begin(); it != m.end(); ++it, ++ri) same = same && it->first == ri->first && it->second == ri->second;
    CHECK(same);
    IntMap::iterator back = m.end();
    --back;
    CHECK(back->first == rm.rbegin()->first);
    MapProbe m2;
    static_cast<IntMap &>(m2) = m;
    CHECK(m2.valid() && m2.size() == m.size());
    v::map<v::string, v::string> ms;
    ms["b"] = "2";
    ms["a"] = "1";
    ms["c"] = "3";
    CHECK(ms.size() == 3 && ms.begin()->first == "a" && ms.find("c")->second == "3" && ms.find("d") == ms.end());
    CHECK(ms.lower_bound("bb")->first == "c" && ms.upper_bound("b")->first == "c" && ms.count("a") == 1);
    ms.erase(ms.find("b"));
    CHECK(ms.size() == 2 && ms.find("b") == ms.end());
    ms.clear();
    CHECK(ms.empty() && ms.begin() == ms.end());
    CHECK(g_invalid == 0);

    std::printf("%s (%d failed)\n", g_fail ? "FAIL" : "ok", g_fail);
    return g_fail ? 1 : 0;
}
