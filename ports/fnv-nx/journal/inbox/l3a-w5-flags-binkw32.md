# l3a-w5 flags census, binkw32

Code c40135c6+ (flags gate), facts fnv-nx-build bc9f12d, load base 0x01440000 (modules.tsv 0772c61). Numbers only; global for the image.

entries 988, unreached 0, instructions 61561, flag reads 4126, undecodable ranges 4
red flag reads 7, no-form rows 7992, certified 4119 of 4126

## The 7 red reads (entry, site, flag, mnemonic, origin: E entry seed, U left undefined by an instruction)

| entry | site | flag | mnemonic | origin |
|---|---|---|---|---|
| 01441230 | 01441246 | af | pushfd | U |
| 0144ddec | 0144ddef | cf | sbb | E |
| 01457d50 | 01457d55 | zf | jne | E |
| 01457d50 | 01457d57 | cf | sbb | E |
| 01457d50 | 01457d65 | sf | jns | U |
| 0145e388 | 0145e388 | zf | loopne | E |
| 0145e388 | 0145e38b | cf | sbb | E |

To L3b (form coverage): the two origin-U reads are 01441246 (pushfd, flag af, producer test) and 01457d65 (jns, flag sf, producer imul).

## No-form rows by mnemonic (instructions without a certified Form; total 7992)

| mnemonic | rows |
|---|---|
| aam | 1 |
| cpuid | 1 |
| das | 2 |
| emms | 3 |
| enter | 1 |
| fadd | 427 |
| faddp | 73 |
| fchs | 60 |
| fcomp | 2 |
| fcos | 7 |
| fdiv | 5 |
| fdivp | 1 |
| fdivr | 3 |
| fild | 21 |
| fimul | 3 |
| fistp | 10 |
| fld | 1151 |
| fldcw | 1 |
| fldlg2 | 2 |
| fmul | 357 |
| fmulp | 2 |
| fnclex | 1 |
| fnstcw | 1 |
| fnstsw | 2 |
| fsin | 3 |
| fsqrt | 2 |
| fst | 8 |
| fstp | 1012 |
| fsub | 445 |
| fsubp | 68 |
| fsubr | 2 |
| fxch | 134 |
| fyl2x | 2 |
| in | 4 |
| insb | 3 |
| insd | 1 |
| int1 | 1 |
| into | 1 |
| iretd | 1 |
| lds | 1 |
| lidt | 4 |
| ljmp | 1 |
| movd | 648 |
| movq | 531 |
| out | 6 |
| paddsw | 190 |
| paddw | 308 |
| pmulhw | 78 |
| pmullw | 40 |
| por | 414 |
| pslld | 192 |
| psllq | 166 |
| psllw | 115 |
| psrlq | 150 |
| psrlw | 52 |
| psubusw | 268 |
| punpckhbw | 12 |
| punpckhdq | 72 |
| punpckhwd | 226 |
| punpcklbw | 124 |
| punpckldq | 240 |
| punpcklwd | 228 |
| pxor | 91 |
| rdtsc | 7 |
| sal | 1 |
| salc | 2 |
| wait | 1 |
