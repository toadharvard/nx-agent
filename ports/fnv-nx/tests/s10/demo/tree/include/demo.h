// Synthetic stand-ins for the decomp's generated initializer units (tests/s10, no game content).
#pragma once

extern "C" void demo_log(const char *what, int v);

struct Logger {
    int v;
    explicit Logger(int x) : v(x) { demo_log("ctor", x); }
    ~Logger() { demo_log("dtor", v); }
};

struct Plain { // trivially destructible: its initializer registers no ??__F
    int a, b;
    Plain() : a(1), b(2) { demo_log("plain", a + b); }
};

struct Dep { // reads an object of the other unit: needs that unit's initializer to have run first
    int v;
    explicit Dep(const Logger &l) : v(l.v * 10) { demo_log("dep", v); }
    ~Dep() { demo_log("~dep", v); }
};

extern Logger g_b1;
extern Plain g_a2;
extern int g_runtime_value;
