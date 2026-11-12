# clpod: the cl reference table for the MSVC C++03 result-return rule

`gen.py --cl CL_EXE --prefix WINEPREFIX --out DIR --toolchain COMMIT --wine-debs wine-debs.txt` regenerates `table.tsv`, `cases.cpp` and `cases.asm`
deterministically (header of `gen.py` states the matrix and the one rule). MEASUREMENT: cl 15.00.30729.01 (fnv-nx-build `toolchain/msvc9` f26d2624,
sp1/VC/bin/cl.exe) under Wine 9.0 (`wine-debs.txt`). It becomes a test reference when it is a recipe stage keyed on a pinned wine deb profile and on cl's sha256.
