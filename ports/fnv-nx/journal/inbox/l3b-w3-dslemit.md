# w3: dslemit root, numbers at shapes commit 77524556 (build of 76763619; branch w3t on w2g)

Root lift/test/dslemit (guest, lifted_by lift/gen/gen_dslemit.py, pack = shapes external). Two cold builds equal, warm 0 stages run (257 hit).
Totals over the whole universe: shapes 26500 (union of 7 images); emitted 18939 shapes (19082 functions); bad 7561; units compiled 128/128, failed 0.

## bad by family (family = capstone group of the decoded instruction: fpu = x87; mmx/sse*/avx/3dnow = simd; int/iret/privilege/vm and arpl, bound, les/lds/lss/lfs/lgs, in/out, hlt, cli/sti, cpuid, rdtsc, wait, int3, into, sysenter = sys; the rest = int)
| class | int | x87 | simd | sys | total |
|---|---|---|---|---|---|
| uncovered (no DSL form) | 262 | 2145 | 4344 | 385 | 7136 |
| emit error | 425 | 0 | 0 | 0 | 425 |
| total | 687 | 2145 | 4344 | 385 | 7561 |

## emit errors (425), by error class
- 232 Unsupported('segment')   (the emitter's own error text; the cause is not analysed here)
- 92 Unsupported('form variable bx'), 63 bp, 25 di, 10 si  = 190 (emitter error text 'form variable <reg>')
- 3 AssertionError (shapes 6659, 665c, 665d: bytes 66 59, 66 5c, 66 5d: the decoder reads them as 16-bit pop)
Sum 232+190+3 = 425.

## undef.tsv (17 rows)
These are NOT link symbols. Each row is (mnemonic, flag) where the proven DSL form declares the flag undefined (x86 SDM) while the hand handler assigns a value:
and af; imul af pf sf zf; mul af pf sf zf; or af; sar af; shl af; shld af; shr af; shrd af; test af; xor af.
Owner: emitter (L3a/forms). Not rt, platform or libc. The DSL leaves the flag untouched, the hand handler writes a value; they differ only for code that reads an architecturally undefined flag.

## verdict findings for L1 (EMLINK)
The unit view hardlinks one #error blob per other unit into every unit: links = units^2. 4096 units gave EMLINK (ext4 limit 65000); the generator uses 128 buckets. L3a's 4096-unit pack hits it.
