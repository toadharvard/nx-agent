# D9S1 comparator and G-REPLAY replayer (RD1)

`d9cmp.py A.d9s B.d9s [--profile env.tsv] [--max N] [--json out.json] [--fold-threads] [--spec d3d9_spec.json]` (python3 stdlib; reader `d9s1.py`).
Exit 0: no det mismatch (env diffs, masked pix items, collapsed polls, other interleavings do not count). 1: det mismatch. 2: usage, unreadable stream,
different spec hashes, or a hash that is not the spec file's. Output is counts; `--json` writes the same numbers plus the details; `--max N` lists up to N mismatches (default 1).

## Comparison rules (all derived from the spec / STREAM.md; nothing per method)
1. **Threads.** Records are compared per thread: thread ordinal i of A is matched with ordinal i of B (ordinals = order of first appearance, so an ordinal
   that appears in a different order in the two files is a mismatch). Each A record is compared with the next unmatched record of the same thread in B; A records
   left without partner are `missing record in B`, B records left over are `extra record in B`. The thread word itself is not compared.
   `--fold-threads` puts all threads of both streams on one thread (needed for G-REPLAY: the replayer is one thread).
2. **Interleaving.** `interleave_swaps` = number of neighbouring matched pairs (in A file order) whose B order is inverted. > 0 with equal per-thread
   sequences is equal (exit 0) and printed. Object ids and token ids are numbered by first appearance in the whole file, so another interleaving can renumber
   them: ids are compared up to one consistent one-to-one renaming per kind (object, token; 0 only equals 0), `renamed_ids` counts renamed ids (0 when the
   interleaving is equal). Ids inside canon buffers (SetPrivateData IUNKNOWN, CheckResourceResidency) are compared as raw bytes (a renumbering there is a mismatch, never a false equal).
3. **Equality** of a record: iface, slot, `this`, depth, new ids (+iface), retired ids and every item (inputs, present/when flags, ret, outs, lock tok/pitch/bytes, close tok/bytes) in order;
   a rec flag, a length or a label that differs is a mismatch. Items carry the class of their HDR. Both `pix`: masked (counted, never compared). Classes differ: mismatch.
   `env` in both: equal -> ok, else `env_diff` (method, item, both values; not a mismatch). The first env diff is reported by A seq, with whether the first det mismatch
   comes `before` / `after` it (later records may be consequences of the env diff). `--profile`: rows `Method<TAB>item<TAB>hex[<TAB>hex of the call's input items]`; every env item whose
   (method, item[, inputs]) has a row is checked in A and B (`env_profile_diff_a/b`).
4. **Polling collapse.** A record is a poll iff: its ret HDR is `pix`; no other item with class det/env has a payload; the spec method has no `effects`, no `closes` and no locked param;
   it creates and retires no ids. Within one thread a maximal run of polls with equal (iface, slot, this, depth, input items) is replaced by its first record; all others are
   dropped and counted in `collapsed`. So k and m repeats compare equal; a repeat of any other call is a mismatch.
5. First mismatch: seq (A/B), thread, method, item label, why, file offsets, 32-byte hex windows of both sides starting at the differing word.

## G-REPLAY
`python3 gen/gen_replay.py OUT` -> `OUT/d9replay.gen.cpp` (tables + invokers from the spec; exit 2 naming the item if the spec has something without emitter). Build:
`g++ -m32 -std=c++17 -I shim/include -I gen -I cmp -I OUT d9wrap.gen.cpp d9stream.cpp d9replay.gen.cpp cmp/d9replay_rt.cpp <implementation providing d9impl_entries[]>`;
`d9replay IN.d9s OUT.d9s` re-issues every depth-0 record **through the generated wrapper** (so OUT is a D9S1 stream) and `d9cmp.py IN OUT --fold-threads` decides. Design reason: the
comparator is then the only place that knows what equal means (same item encoding, classes, masks), and nested records (depth > 0) come out of the implementation by themselves.
- Calls are re-issued on ONE thread in file order (all threads folded). Object ids map to the pointers the implementation returned (per interface, derived interfaces accepted).
- Inputs are rebuilt from the recorded bytes: structs by spec layout, buffers at `off`, canon words to live pointers, ohandle tokens to `d9impl_ohandle(token)` if the integrator defines it
  (e.g. a real HWND), else stable fake values (IUnknown tokens: replay-owned dummy objects). At a recorded Unlock/ReleaseDC the recorded unlock bytes are written into the live locked memory.
- `d9impl_before_call(ordinal, seq)` / `d9impl_bind(id, ptr)` are optional hooks (the echo test uses them).
Limits: no real multi-threaded replay; `dc` (GetDC) surfaces cannot be written back (GDI), so a program that draws on a DC shows a mismatch at ReleaseDC; out buffers get a 64 KiB capacity
(the application's real size is not in the stream); exports without a spec signature (5) are not recorded, so not replayed; env answers of the implementation are whatever it gives (reported, not failed).
