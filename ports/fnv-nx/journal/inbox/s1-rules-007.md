### J-XXXX | 2026-11-08 | stream: S1-rules | status: active | parent: s1-rules-006

* **oracle-fault (712 hard on 8b233f99; 757 live records in fuzz/cache 1..8, 840 divergence files)** - replayed
  locally on link 9f865b0a4037 (its tree, msvc/pristine, the owner's exe). Mechanisms found, by commit on
  claude/fnv-s1-rules:
  - **DTORB1** (4331e5c, prepare rule, port bug): a destructor stand-in `X::~X() {}`, tagged at ??_GX, whose ??1X the
    recipe binds to another function A. MSVC's ??_G runs A; GCC's D0 ran the empty body. 21 sites convert, 37 are held
    (bases or class-type members), 6 + 1 are gated. 36 live hard records sit in these destructors (12 oracle-fault /
    unclassified, 16 call-flow, 7 vptr-store, 1 uninit-x86).
  - **d26-imp** (50ed349, harness): native import calls got their canned word normalised as an untyped bool
    (TlsGetValue 0xffffffff on x86 against 0xffffff01 natively), so `[ret + 4]` faulted on x86 only. All 5 replayed
    Havok records of this shape are gone. 144 of the 757 records have this fingerprint (an x86 read at a small
    offset that is 3 mod 4). The recheck covers every x86-only fault.
  - **d26-uniform** (e4439b4, harness): a one-sided fault that goes away with uniform garbage is uninit-x86 /
    uninit-arm (00c9e2a0).
  - Not fixed yet, seen in the sample:
    - an intercepted callee's unwritten stack out-parameter (00dc9240: x86 reads `this`'s byte where 0x00d874b0
      writes a bool; the native zero-initialises it). A stub-outparam class: the stub should write the pointee on
      both machines;
    - null-this member reads (00ca0780 `fld [ecx + 0x10]` after a canned call);
    - empty callees / bodies (EQ's test_ofault covers native-empty-body).
  - A local replay of all 840 is running (slow, about 1 / min). The first rows are in the table below; the next fuzz
    pass on the integration head gives the exact count.
* **START (farms)**: fz1..fz8 on the integration head once these are adopted. The d25-sg selections and the d26-imp
  rechecks refuzz.
