// fixture (fpexact / f2i-asm): an inline asm with a float operand and an integer result
int fx_asm(float f) { int r; __asm__("fcvtzs %w0, %s1" : "=r"(r) : "w"(f)); return r; }
