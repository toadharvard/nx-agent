# L2 w3: minimal fixture for the expand_expr_addr_expr_1 ICE (from win32/ini.cpp:124)

Toy guest root through recipe.py (msvcmodel b4057704, all 8 plugins, cxx flags `-O2 -std=gnu++17` + recipe.SEMANTICS_FLAGS, distribution cross libstdc++ headers, no other flags). Four units, same ICE
`internal compiler error: in expand_expr_addr_expr_1, at expr.cc:8710` (during RTL pass: expand, via expand_builtin/store_expr), reproduced on the first line of the function:

```cpp
#include <string>
std::string ctor(const char *s) { return std::string(s); }      // ICE
std::string cp(const std::string &s) { return std::string(s); } // ICE
std::string fold(const char *s) { std::string r(s); return r; } // ICE (the ini.cpp:124 shape)
```
Does NOT ICE: `void sink(std::string *); void f(const char *s) { std::string r(s); sink(&r); }` (the string is not returned by value).
So the trigger is returning a std::string by value (NRVO/return slot of a class with a non-trivial ctor built from a builtin), not the loop body. Plugin not isolated (not bisected, per the lead).
Toy-root generator: any root of the form in verify/tests/test_guest.py guest_repo() with these units as tracked .cc files.
