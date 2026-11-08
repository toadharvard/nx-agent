### J-XXXX | 2026-11-08 | stream: R4 + S4-platform (claude/fnv-recon4) -> S2 s2d-010 items, ABI classes taken from S2 | status: wip | parent: recon4-018

Measured on fnv-nx-build link/97deacd38aec (out/refs.tsv, abi.tsv, addrmap.tsv); counts only.

* **1, import declared without extern "C"** (d2e44ab1).
  - Rows: refs.tsv has 2 import rows with no target and a spelling other than the slot's api: d3dx9shader.cpp's
    _Z37D3DXCreateCubeTextureFromFileInMemory... (IAT 0x00fdf400), and renderer.cpp's IMP_fdf2ec (0x00fdf2ec).
  - Fix: resolve binds such a reference to the api's C name (imports.tsv) when the slot has no export-table
    symbol; matching spellings are unchanged. test_bindings fails without the change.
  - 2 -> 0 expected.
* **2b, bool read from a char/int result** (f7b16a61).
  - abi.bool_from_int: the reference returns bool, the definition another integer type. abi and abi2 now give the
    pair a bridge with (value != 0) after the call, at the definition's width (class ret-bool-from-int).
  - test_retbool runs the emitted bridges under qemu: char 2 -> 1; unbridged, the bool holds 2.
  - S2's ~1,343 references -> 0 unbridged expected. The real count shows at the next link as abi.tsv rows of class
    ret-bool-from-int. This link can't count them offline: these pairs were "same", so abi.tsv has no row for them,
    and Itanium names carry no return type.
* **2a, name-addr code references with no ABI check.**
  - 265 on this link. S2's 533 / ~160 came from an older link.
  - The check: resolve compares both Itanium names (name_addr_sigs). The convention rule is the same for both sides:
    a class scope is a thiscall member, a `fnv_<hex>` scope is a namespace.
  - Equal native argument shapes are "same", whatever x86 says (the conventions are guessed). abi's bridge verdict
    is taken. Any other difference is flagged code-manual, with an abi.tsv row marked `(itanium)`. Moves are never
    guessed.
  - Every row, by status:
    - same: 216 (197 equal native shape, 19 same by abi);
    - bridge: 0;
    - flagged code-manual: 27 (aggregate parameters 13; member/free with a register shift or pop mismatch 7;
      arity 7);
    - not compared: 22 (a C name or no native symbol on one side).
  - The 27 flagged rows need a decision per pair, from the source or recon: is the definition really the function
    the reference means, and with which convention? They are listed in abi.tsv at the next link.
* **tests** - tools/link: all 20 pass. New: test_nameaddr_abi, test_retbool, test_tls_block. test_bindings has the
  import case. test_vacall skips (no gcc plugin headers).

* **The 27 flagged rows, decided** (orchestrator 08:22; ledger per pair in the private scratchpad, from the exe's
  entry and ret bytes plus the decomp source).
  - 14 distinct pairs.
    - same 5: an enum parameter the Itanium synth cannot see; free functions in a namespace the heuristic took for a
      class.
    - bridge 2 (0x00b5ade0): the definition is a __fastcall with an unused edx. resolve now prefers the definition's
      own MSVC declaration, so abi's edx-dummy bridge applies. test_nameaddr_abi covers it.
    - R2 20, recon-shaped:
      - 7 rows: reference declarations that drop the arguments or the `this` the exe's ret shows;
      - 13 rows: definitions that model by-value iterators and structs differently from their references.
  - 5 + 2 + 20 = 27.
