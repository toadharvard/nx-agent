# x87spec.py citation check (w9, branch claude/fnv-l3b-lift-w9)

Pinned SDM: fnv-nx-build `tools/intel-sdm` 0768b0baf29a, PDF sha256 a4a62e6a...f321. "PDF index" = 1-based page of that PDF (`lift/proof/sdm_pages.tsv`).

## What was checked, and by what

1. Mechanical, all 286 `SDM <vol>:<id>` citations: every key resolves to a PDF page inside its own volume (`sdm_pages.py`, 0 unresolved; `test_page_is_in_the_keys_volume`).
2. Mechanical, every quoted fragment of the spec (48): `lift/proof/sdm_cite_check.py SDM.txt` attributes each quote to the nearest preceding key and finds it on that page or the next two. Result: 48 OK, 0 MISS, 0 NOKEY (the SDM text is not in the repo, so this is a tool run, not a unit test).
3. Semantic, by table: FSCALE Table 1-30 (49 cells), FPATAN Table 1-26 (32 concrete cells; the 4 range cells are symbolic) and FYL2X Table 1-44 (59 runs; 4 finite-by-finite cells are symbolic) are tests (`SpecialTables`, rows in `x87_sdm_rows.txt`): the spec equals the SDM in every checked cell, after the FYL2X fix below. Earlier tests cover FCOM/FUCOM/FTST/FCOMI/FUCOMI codes (Tables 8-6, 8-7), FXAM, the tag word, FINIT, the m80 and integer-indefinite encodings, FPREM and the trig special operands.
4. NOT done yet: the semantic reading of the remaining transcendental special-value tables (F2XM1 1-12, FPTAN 1-29, FSIN 1-31, FSINCOS 1-32, FPREM 1-27/1-28; the trig and FPREM special operands have earlier tests, not yet checked cell by cell against the tables), of the NaN rule against Table 4-8, and of the load/store/BCD paths against the SDM operations. Those are the next items, in the lead's order. Nothing below claims them.

## Mismatches found (rule 8: cause) and fixed

| # | spec line (before) | SDM (PDF index) | cause | fix |
| --- | --- | --- | --- | --- |
| 1 | `sdm_pages.tsv`: 11 Vol. 1 keys (8.2.2, 8.5, 8.5.2-8.5.4, 9.5, 9.5.1, T4-1, T4-3, T4-5, T4-8) pointed at Vol. 3A / Vol. 2B pages (e.g. 1:8.5 -> PDF 3431) | Vol. 1 is PDF 29-536 | `sdm_pages.py` took the last match over the whole PDF and ignored the volume | the search is limited to the pages whose footer names the key's volume; new test; all 11 now in 94-250 |
| 2 | `SDM 2A:FWAIT` | WAIT/FWAIT is Vol. 2D 6-1, PDF 2834 | wrong volume in the key (the old resolver also hid it) | key `SDM 2D:FWAIT`; quote is now the SDM sentence |
| 3 | FABS: quote `SignBit(ST(0)) := 0` | PDF 1013: `ST(0) := |ST(0)|` | the quote was my paraphrase, not the SDM operation (the SignBit form is FCHS, PDF 1022); behaviour of the spec is the same | quote corrected |
| 4 | FNSTSW AX: quote `AX := FPUStatusWord` | PDF 1096: `DEST := FPUStatusWord` | paraphrase | corrected |
| 5 | FILD/FIST/FBSTP/FLD: `ST(0) <- ...`, `DEST <- ...`, `TOP := TOP-1; ...` | `:=` and `TOP := TOP − 1` as separate statements (PDF 1046, 1051, 1020, 1056) | ASCII-arrow paraphrase inside quote marks; FILD also had a `SignExtend(...)` quote that the FILD page does not contain | corrected to the literal statements |
| 6 | FSCALE `2^RoundTowardZero`, FPTAN/FSINCOS `IF ST(0) < 2^63`, FYL2X `log2(ST(0))`, FPATAN `arctan(ST(1)/ST(0))` | text of PDF 1082, 1075, 1126, 1068 has the superscript flattened (`2RoundTowardZero`, `263`) and other spacing | quote not literal | corrected; the flattened superscript is stated in the docstring |
| 7 | FST/FSTP #IA (two places): `#IA Source operand is an SNaN`, `does not occur ... m80fp` | PDF 1090: `#IA If destination result is an SNaN value or unsupported format, except when the destination format is in double extended-precision floating-point format.` | invented wording | corrected; meaning of the spec unchanged |
| 8 | FPTAN exceptions: `... SNaN value, infinity` | PDF 1075: `... SNaN value, ∞, or unsupported format.` | paraphrase | corrected |
| 9 | FCOM note: `SDM 1:T8-6 note: 'flags not set if unmasked ...'` | the note is under 2A:FCOM Table 1-17 (and the FCOMI tables), PDF 1028, not under Vol. 1 Table 8-6 | wrong key | key and exact sentence corrected |
| 10 | `SDM 1:T4-3 'QNaN Floating-Point Indefinite'`; `SDM 1:4.8.3.5 'an SNaN is converted ...'` | the quoted title is section 4.8.3.7; the 4.8.3.5 sentence reads `When an SNaN is converted to a QNaN, the conversion is handled by setting the most-significant fraction bit of the SNaN to 1` (PDF 107) | wrong key / paraphrase | corrected |
| 11 | FPREM: `the SDM's 'implementation-dependent number ...'`; ST(i): `'ST(i) ... i-th register ...'` | PDF 1071: `An implementation-dependent number between 32 and 63`; 1:8.1.2: `to specify the ith register from TOP in the stack` | paraphrase | corrected |
| 12 | four quotes that were not SDM text at all (internal Bool names) | - | quote marks used for my own terms | quote marks removed |

| 13 | **spec defect, not a citation:** FYL2X with ST(1) = +-inf and ST(0) finite, positive, not 1 | Table 1-44, PDF 1126: -inf row, 0<x<1 -> +inf, x>1 -> -inf; +inf row the opposite; no exception | result bits and OE/UE/PE came from `fpToReal(+-inf) * LOG2(x)` (the real of an infinity is an unspecified z3 value), so the result and flags were unconstrained | the result is an exact infinity (sign of ST(1), flipped when x < 1), no flags; every cell of Table 1-44 is now a test |

Registry scope, no change: the registry has the instructions the pack uses (`x87_keys.tsv`, 118 + 2 cleared). FYL2XP1 (D9 F9), FXTRACT, FDECSTP, FINCSTP, FLDL2T, FLDL2E-class others that the pack never uses are not in it; a new shape would be added from the SDM page by the same rules.

Also recorded, no change needed: FNSTSW/FNSTCW/FLDCW/FNCLEX undefine C0..C3 in the spec (`UNDEF_C0123`): PDF 1096 "The C0, C1, C2, and C3 are undefined." matches.

## Quote table (spec line, SDM PDF index, key, quoted SDM text), in the lead's order

The quote is on the key's PDF page or one of the next two (an instruction's Operation is usually on the second page).

### 1. Transcendentals, FPREM, FSCALE (10 quotes)

| spec line | SDM PDF index | key | quoted SDM text |
| --- | --- | --- | --- |
| 1190 | 1088 | 2A:FSQRT | `ST(0) := SquareRoot(ST(0))` |
| 1216 | 1076 | 2A:FRNDINT | `ST(0) := RoundToIntegralValue(ST(0))` |
| 1244 | 1082 | 2A:FSCALE | `ST(0) := ST(0) * 2RoundTowardZero(ST(1))` |
| 1246 | 236 | 1:8.5.4 | `massive overflow` |
| 1304 | 1070 | 2A:FPREM | `An implementation-dependent number between 32 and 63` |
| 1414 | 1074 | 2A:FPTAN | `#IA Source operand is an SNaN value, ∞, or unsupported format.` |
| 1416 | 1074 | 2A:FPTAN | `IF ST(0) < 263 THEN` |
| 1507 | 1126 | 2A:FYL2X | `PopRegisterStack` |
| 1549 | 1068 | 2A:FPATAN | `ST(1) := arctan(ST(1) / ST(0))` |
| 1549 | 1068 | 2A:FPATAN | `PopRegisterStack` |

### 2. The NaN rule (1 quotes)

| spec line | SDM PDF index | key | quoted SDM text |
| --- | --- | --- | --- |
| 309 | 107 | 1:4.8.3.5 | `When an SNaN is converted to a QNaN, the conversion is handled by setting the most-significant fraction bit of the SNaN to 1` |

### 3. Stack, tag and control/status machinery (12 quotes)

| spec line | SDM PDF index | key | quoted SDM text |
| --- | --- | --- | --- |
| 206 | 209 | 1:8.1.2 | `to specify the ith register from TOP in the stack` |
| 231 | 234 | 1:8.5.1.1 | `An empty register has a tag value of 11` |
| 250 | 234 | 1:8.5.1.1 | `An instruction attempts to load a non-empty x87 FPU register` |
| 415 | 236 | 1:8.5.4 | `Destination is a memory location` |
| 441 | 1028 | 2A:FCOM | `Flags not set if unmasked invalid-arithmetic-operand (#IA) exception is generated.` |
| 726 | 1015 | 2A:FADD | `IF Instruction = FADDP THEN PopRegisterStack` |
| 1583 | 2834 | 2D:FWAIT | `Causes the processor to check for and handle pending, unmasked, floating-point exceptions before proceeding.` |
| 1589 | 1024 | 2A:FNCLEX | `FPUStatusWord[0:7] := 0; FPUStatusWord[15] := 0` |
| 1598 | 1060 | 2A:FLDCW | `FPUControlWord := SRC` |
| 1608 | 1092 | 2A:FNSTCW | `DEST := FPUControlWord` |
| 1615 | 1096 | 2A:FNSTSW | `DEST := FPUStatusWord` |
| 1622 | 1096 | 2A:FNSTSW | `DEST := FPUStatusWord` |

### 4. Compare and FCOMI EFLAGS, FCMOV (2 quotes)

| spec line | SDM PDF index | key | quoted SDM text |
| --- | --- | --- | --- |
| 1069 | 1026 | 2A:FCMOVcc | `IF condition TRUE THEN ST(0) := ST(i)` |
| 1073 | 1026 | 2A:FCMOVcc | `Stack underflow occurred` |

### 5. Loads, stores, conversions, other (23 quotes)

| spec line | SDM PDF index | key | quoted SDM text |
| --- | --- | --- | --- |
| 8 | 1015 | 2A:FADD | `FADD/FADDP/FIADD` |
| 9 | 1015 | 2A:FADD | `Floating-Point Exceptions` |
| 10 | 1015 | 2A:FADD | `FPU Flags Affected` |
| 135 | 213 | 1:8.1.3.3 | `The B-bit (bit 15) ... reflects the contents of the ES flag` |
| 305 | 108 | 1:4.8.3.7 | `QNaN Floating-Point Indefinite` |
| 449 | 234 | 1:8.5.1.1 | `sets the IE flag (bit 0) and the SF flag (bit 6)` |
| 532 | 1090 | 2A:FST | `#IA If destination result is an SNaN value or unsupported format, except when the destination format is in double extended-precision floating-point format.` |
| 630 | 1046 | 2A:FILD | `ST(0) := ConvertToDoubleExtendedPrecisionFP(SRC)` |
| 637 | 1051 | 2A:FIST | `DEST := Integer(ST(0))` |
| 676 | 102 | 1:T4-5 | `FFFFC000000000000000H` |
| 680 | 1020 | 2A:FBSTP | `DEST := BCD(ST(0))` |
| 858 | 1046 | 2A:FILD | `ST(0) := ConvertToDoubleExtendedPrecisionFP(SRC)` |
| 863 | 1018 | 2A:FBLD | `ST(0) := ConvertToDoubleExtendedPrecisionFP(SRC)` |
| 898 | 1090 | 2A:FST | `#IA If destination result is an SNaN value or unsupported format, except when the destination format is in double extended-precision floating-point format.` |
| 916 | 1051 | 2A:FIST | `DEST := Integer(ST(0))` |
| 923 | 1020 | 2A:FBSTP | `DEST := BCD(ST(0)); PopRegisterStack` |
| 930 | 1111 | 2A:FXCH | `temp := ST(0); ST(0) := SRC; SRC := temp` |
| 956 | 1022 | 2A:FCHS | `SignBit(ST(0)) := NOT (SignBit(ST(0)))` |
| 1362 | 1084 | 2A:FSIN | `approximation of the mathematical sin function` |
| 1902 | 250 | 1:9.5 | `The MMX state is aliased to the x87 FPU state` |
| 1906 | 251 | 1:9.6.2 | `writes ... to the exponent part ... (bits 64 through 79)` |
| 1930 | 250 | 1:9.4.8 | `setting the tags in x87 FPU tag word to 11B` |
| 1931 | 250 | 1:9.5.1 | `The EMMS instruction ... sets the entire x87 FPU tag word to empty (11B).` |
