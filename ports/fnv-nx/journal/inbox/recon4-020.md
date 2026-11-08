### J-XXXX | 2026-11-08 | stream: R4 + S4-platform (claude/fnv-recon4) -> S2-bodies' 26 runtime traps, taken over | status: done (farm confirmation pending) | parent: recon4-018

Source: R2's s2d/list/97deacd38aec, the `runtime` rows. These are template instances the units never emit, plus the
decomp's std look-alikes. Counts only: the per-name ledger stays in the R4 scratchpad.

* **Ledger: 26 rows, plus 1 extra found on the way. Every row has a status.**

  | status | rows |
  |---|---|
  | fixed by S4 rule STL1, measured 0 undefined (4fc72082) | 15 |
  | STL1 body present, not instantiated in the local prepare | 2 |
  | fixed by S4 rule STL2, measured 0 undefined (2588d577) | 6, +1 extra |
  | recon, handed to R2 | 3 |

  - The 2 unconfirmed rows are std::swap<_Aux_cont*> and std::swap<_Aux_cont>, both from _Swap_aux. That template
    is instantiated only with the unit's vector::swap. It has a body now, so any instantiation is defined.
  - The extra row is CsvCtype<char>::widen. CsvUseFacet's facet type declares it with no body.
* **STL1 (tools/rules/s4_stl.py).** It covers SpeedTree indexed geometry's VC9 helper templates: _Allocate, _Destroy,
  fill, swap and vector::_Xlen.
  - They are declared in the unit's top-level `namespace std` and defined nowhere. STL1 adds VC9's bodies, written
    here.
  - _Xlen throws through fnvnx_xlength_error.
* **STL2.** It covers tesobject's CSV ifstream model and modelloader's vector<float> specialization:
  - ios_base::setstate: (state | s) & 0x17. The copy has no exceptions() setter, so it never throws.
  - basic_ios::init goes to a new platform function, fnvnx_basic_ios_init, factored out of fnvnx_ostream_ctor. It
    leaves the caller's vptr alone. tests/s4 cpplib has a test for it.
  - CsvUseFacet: a static facet with an identity widen.
  - basic_ifstream::open: VC9's basic_filebuf::open, through fnvnx_fiopen and the unit's filebuf _Init.
  - vector<float> ctor/dtor: every field 0; the dtor frees first.
* **Measured.** I prepared and compiled locally, with the guest flags, then ran nm -u on each unit:
  - indexedgeometry: 15 undefined -> 0;
  - tesobject + modelloader: 9 -> 0.
  - tesobject still has 2 undefineds in the nested fnv_00ac9790::std copy: ParserUseFacet and
    basic_ios::setstate(int, bool). They are not on the list; I asked R2 about them.
* **Tests.** tests/s2d/test_stl1.py checks both rules on hand-written units, compiling before and after. tests/s4
  passes: 108 tests, 0 failed.
* **Handed to R2 (recon).**
  - FUN_00ad5770, the CSV getline in tesobject;
  - StandardLibrarySingletonObject ctor and dtor in bsawardssystemutility_console.
* **Pending.** The next farm link should show these names gone from the trap list.
