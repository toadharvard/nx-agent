extern "C" char scr[];
extern "C" int entry() { return *(volatile int*)(scr + 4); }
int g_val = 5;
int use_g() { return g_val; }
