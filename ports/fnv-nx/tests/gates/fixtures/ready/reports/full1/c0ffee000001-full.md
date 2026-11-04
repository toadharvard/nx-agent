# full build c0ffee000001

| stage | state |
|---|---|
| guest | ok |
| boot | ok |
| package | ok |

trial link: 4 objects, 0 undefined names, 0 multiple definitions
split-global check: 0 objects / 0 relocations; apply failures 0
x86-inconsistent 1, ABI manual 0, 12 bridges

- 0 undefined symbols bound to trap stubs
- data image: the owner's FalloutNV.exe
- WinMain entered: yes; initializers skipped after a fault: 0; trapped keys 0 (0 hits)
- stop: the boot budget ran out

## Package

exefs.nsp:
  main  offset=0x0 size=0x1000
  main.npdm  offset=0x1000 size=0x400
- guest.nso in the SD tree: yes
- exefs override for program id 0100aaaabbbb0000
- PC-relative absolute references 0

## V1 audits

| audit | failing | checked |
|---|---|---|
| c1-bind | 0 | 10 |
| c4-vtables | 0 | 3 |
