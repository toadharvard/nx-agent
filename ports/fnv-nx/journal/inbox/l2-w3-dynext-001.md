# l2-w3 dynext: the two open LoadLibrary sites stay open (measured)

Pins: exe 0948da1e, msvc/pristine/87eb9373a0bb @64d64a8b, facts a0f3d8ea, manifest e15d385f, n1 over inputs e1ca6b02d60d (68,076 N1 functions, 322,663 pinned symbols).
Open: GetProcAddress 0 of 135; LoadLibrary 2 of 36 (exe 0xda284a global 0x11b76d0; d3dx9_38 0x56b717 global 0x793b24).

Rule (b), compare-bounded indexed stores: of the 127 indexed stores in the exe section of 0x11b76d0, 7 are bounded and 120 are not; none of the 7 reaches the target. The 9,635 escape reasons (5,076 distinct addresses) are not closed by bounds.

Rule (A), unit data-section extents from pristine objects + N1 pins: 17,733 sections pinned, 17,399 agree, 334 disagree. Target has no extent (its pin is an undefined extern in unit 2836). 641 extents in its 977,564-byte section cover 8,554 bytes; 0 of 127 store bases and 132 of 5,076 escapes fall in an extent. 10,108 of 17,399 extents overlap another unit's: they are decomp declarations, not exe objects, so they are not a fact.
d3dx9_38 has no pristine objects: no extents exist (2 indexed stores, 111 escapes).

Closing needs real object boundaries (/Z7 or map rebuild). Nothing pushed as a tool; no private data here.
