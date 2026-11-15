# l3b-w9: x87 distinct instructions of the pack universe (task 2.6 step 1)

Source: the 5,008,658 distinct instruction labels `L_xxxxxxxx:` of pack 7ef692b5 (fnv-nx-build l3a-lift/pack/7b2faae276b2, `u/*.cpp`); universe labels = 5,007,758 = these minus the 900 raise-only labels of the queue header. Each label address is mapped to its image (assigned bases from l3a-lift/facts at 163ec389, the contract_inputs pin) and decoded from the image BYTES with `proof/shape.py` (`shape_key`, `family`: x87 = opcode byte D8-DF or 9B after the prefixes). Identity of an instruction = (opcode byte, ModRM.mod==3?, ModRM.reg, ModRM.rm only where rm selects the instruction: D9 /2 /4 /5 /6 /7, DA /5, DB /4, DE /3, DF /4 at mod=3; SDM vol 2 appendix A.2.7), prefixes counted apart. The mnemonic column is for the reader (SDM opcode map); no mnemonic text is used to decide anything.

Global (commit of this note): **x87 shapes 2,055, x87 sites 392,717, distinct instruction forms 122** (prefix variants are shapes of the same instruction).
Gap to the queue header (x87 = 2,063 shapes, 392,864 addresses): 8 shapes / 147 addresses that this decode does not reproduce. 866 labels do not decode at all (28 of them start with an x87 opcode byte: undefined forms such as DA F9, DB 61); they are not the 147. Not explained yet: the universe tool (tool_sha256 2be85886...) is not on my base. Ask L3a/L1 for it or for the per-instruction x87 key list.

| opcode | form | mod/reg/rm | instruction (SDM) | shapes | sites | prefixes seen |
|---|---|---|---|---|---|---|
| 9b | - | -/- | fwait | 2 | 206 | fs |
| d8 | mem | 0/- | fadd m32fp | 111 | 8149 | es/es, fs |
| d8 | mem | 1/- | fmul m32fp | 114 | 19004 | - |
| d8 | mem | 2/- | fcom m32fp | 41 | 764 | - |
| d8 | mem | 3/- | fcomp m32fp | 41 | 879 | - |
| d8 | mem | 4/- | fsub m32fp | 117 | 4848 | - |
| d8 | mem | 5/- | fsubr m32fp | 35 | 568 | - |
| d8 | mem | 6/- | fdiv m32fp | 31 | 881 | - |
| d8 | mem | 7/- | fdivr m32fp | 26 | 732 | - |
| d8 | reg | 0/* | fadd st,st(i) | 6 | 615 | - |
| d8 | reg | 1/* | fmul st,st(i) | 7 | 4391 | - |
| d8 | reg | 2/* | fcom st(i) | 7 | 2248 | - |
| d8 | reg | 3/* | fcomp st(i) | 7 | 1647 | - |
| d8 | reg | 4/* | fsub st,st(i) | 7 | 434 | 66 |
| d8 | reg | 5/* | fsubr st,st(i) | 6 | 96 | - |
| d8 | reg | 6/* | fdiv st,st(i) | 8 | 202 | - |
| d8 | reg | 7/* | fdivr st,st(i) | 8 | 74 | - |
| d9 | mem | 0/- | fld m32fp | 263 | 101175 | 66, 67 |
| d9 | mem | 2/- | fst m32fp | 103 | 7032 | cs |
| d9 | mem | 3/- | fstp m32fp | 213 | 93734 | - |
| d9 | mem | 4/- | fldenv | 5 | 6 | - |
| d9 | mem | 5/- | fldcw | 5 | 975 | - |
| d9 | mem | 6/- | fnstenv | 2 | 2 | - |
| d9 | mem | 7/- | fnstcw | 7 | 535 | - |
| d9 | reg | 0/* | fld st(i) | 7 | 8472 | - |
| d9 | reg | 1/* | fxch st(i) | 7 | 8311 | - |
| d9 | reg | 3/* | (undef fstp1) | 1 | 1 | - |
| d9 | reg | 4/0 | fchs | 1 | 2110 | - |
| d9 | reg | 4/1 | fabs | 1 | 962 | - |
| d9 | reg | 4/4 | ftst | 1 | 7 | - |
| d9 | reg | 4/5 | fxam | 1 | 4 | - |
| d9 | reg | 5/0 | fld1 | 1 | 7605 | - |
| d9 | reg | 5/2 | fldl2e | 1 | 2 | - |
| d9 | reg | 5/3 | fldpi | 1 | 6 | - |
| d9 | reg | 5/4 | fldlg2 | 1 | 3 | - |
| d9 | reg | 5/5 | fldln2 | 1 | 2 | - |
| d9 | reg | 5/6 | fldz | 1 | 11424 | - |
| d9 | reg | 6/0 | f2xm1 | 1 | 4 | - |
| d9 | reg | 6/1 | fyl2x | 1 | 7 | - |
| d9 | reg | 6/2 | fptan | 1 | 2 | - |
| d9 | reg | 6/3 | fpatan | 1 | 5 | - |
| d9 | reg | 6/5 | fprem1 | 1 | 5 | - |
| d9 | reg | 7/0 | fprem | 1 | 5 | - |
| d9 | reg | 7/2 | fsqrt | 1 | 6 | - |
| d9 | reg | 7/3 | fsincos | 1 | 50 | - |
| d9 | reg | 7/4 | frndint | 1 | 11 | - |
| d9 | reg | 7/5 | fscale | 1 | 12 | - |
| d9 | reg | 7/6 | fsin | 1 | 7 | - |
| d9 | reg | 7/7 | fcos | 1 | 11 | - |
| da | mem | 0/- | fiadd m32int | 13 | 124 | ss |
| da | mem | 1/- | fimul m32int | 13 | 106 | - |
| da | mem | 2/- | ficom m32int | 5 | 5 | - |
| da | mem | 3/- | ficomp m32int | 2 | 2 | - |
| da | mem | 4/- | fisub m32int | 16 | 200 | - |
| da | mem | 5/- | fisubr m32int | 2 | 2 | - |
| da | mem | 6/- | fidiv m32int | 13 | 112 | - |
| da | mem | 7/- | fidivr m32int | 2 | 4 | - |
| da | reg | 3/* | fcmovu | 1 | 1 | - |
| da | reg | 5/1 | fucompp | 1 | 1123 | - |
| db | mem | 0/- | fild m32int | 58 | 4363 | - |
| db | mem | 1/- | fisttp m32int | 4 | 11 | - |
| db | mem | 2/- | fist m32int | 4 | 4 | - |
| db | mem | 3/- | fistp m32int | 7 | 530 | - |
| db | mem | 5/- | fld m80fp | 9 | 133 | - |
| db | mem | 7/- | fstp m80fp | 7 | 221 | - |
| db | reg | 0/* | fcmovnb | 1 | 2 | - |
| db | reg | 3/* | fcmovnu | 2 | 3 | - |
| db | reg | 4/2 | fnclex | 1 | 7 | - |
| db | reg | 5/* | fucomi st,st(i) | 1 | 1 | - |
| db | reg | 6/* | fcomi st,st(i) | 3 | 3 | f2 |
| dc | mem | 0/- | fadd m64fp | 35 | 2361 | ds |
| dc | mem | 1/- | fmul m64fp | 43 | 8523 | - |
| dc | mem | 2/- | fcom m64fp | 32 | 404 | - |
| dc | mem | 3/- | fcomp m64fp | 45 | 4952 | - |
| dc | mem | 4/- | fsub m64fp | 29 | 769 | - |
| dc | mem | 5/- | fsubr m64fp | 30 | 1013 | - |
| dc | mem | 6/- | fdiv m64fp | 17 | 3679 | - |
| dc | mem | 7/- | fdivr m64fp | 10 | 1350 | - |
| dc | reg | 0/* | fadd st(i),st | 7 | 478 | - |
| dc | reg | 1/* | fmul st(i),st | 8 | 1390 | - |
| dc | reg | 4/* | fsubr st(i),st | 3 | 5 | - |
| dc | reg | 5/* | fsub st(i),st | 4 | 54 | - |
| dc | reg | 7/* | fdiv st(i),st | 5 | 54 | - |
| dd | mem | 0/- | fld m64fp | 116 | 6841 | ds, es |
| dd | mem | 1/- | fisttp m64int | 3 | 5 | - |
| dd | mem | 2/- | fst m64fp | 42 | 627 | - |
| dd | mem | 3/- | fstp m64fp | 96 | 6921 | - |
| dd | mem | 4/- | frstor | 4 | 5 | - |
| dd | mem | 6/- | fnsave | 2 | 3 | - |
| dd | mem | 7/- | fnstsw m16 | 8 | 26 | fs |
| dd | reg | 0/* | ffree st(i) | 4 | 12 | - |
| dd | reg | 2/* | fst st(i) | 1 | 1 | - |
| dd | reg | 3/* | fstp st(i) | 7 | 10124 | - |
| dd | reg | 4/* | fucom st(i) | 3 | 203 | - |
| dd | reg | 5/* | fucomp st(i) | 5 | 314 | - |
| de | mem | 0/- | fiadd m16int | 9 | 37 | f3, ss |
| de | mem | 1/- | fimul m16int | 4 | 4 | - |
| de | mem | 2/- | ficom m16int | 2 | 3 | - |
| de | mem | 3/- | ficomp m16int | 5 | 6 | - |
| de | mem | 4/- | fisub m16int | 4 | 5 | - |
| de | mem | 5/- | fisubr m16int | 2 | 2 | - |
| de | mem | 6/- | fidiv m16int | 2 | 2 | - |
| de | mem | 7/- | fidivr m16int | 3 | 4 | ds |
| de | reg | 0/* | faddp st(i),st | 7 | 10584 | - |
| de | reg | 1/* | fmulp st(i),st | 7 | 7378 | - |
| de | reg | 3/1 | fcompp | 1 | 4201 | - |
| de | reg | 4/* | fsubrp st(i),st | 7 | 2807 | - |
| de | reg | 5/* | fsubp st(i),st | 7 | 2380 | - |
| de | reg | 6/* | fdivrp st(i),st | 6 | 1567 | - |
| de | reg | 7/* | fdivp st(i),st | 6 | 559 | - |
| df | mem | 0/- | fild m16int | 7 | 23 | - |
| df | mem | 1/- | fisttp m16int | 2 | 11 | - |
| df | mem | 2/- | fist m16int | 5 | 14 | - |
| df | mem | 3/- | fistp m16int | 4 | 5 | - |
| df | mem | 4/- | fbld m80bcd | 2 | 4 | - |
| df | mem | 5/- | fild m64int | 9 | 878 | - |
| df | mem | 6/- | fbstp m80bcd | 3 | 4 | - |
| df | mem | 7/- | fistp m64int | 12 | 256 | fs, ss |
| df | reg | 0/* | ffreep st(i) | 1 | 1 | - |
| df | reg | 4/0 | fnstsw ax | 1 | 16676 | - |
| df | reg | 5/* | fucomip st,st(i) | 3 | 3 | - |
| df | reg | 6/* | fcomip st,st(i) | 1 | 1 | - |
