// fixture (fpexact / undecodable): bytes in an executable section that decode to no instruction
void fx_undec(void) { __asm__ volatile(".inst 0x0f0f0f0f"); }
