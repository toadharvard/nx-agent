# CODEMAP: the byte classes of codemap.tsv, and what they mean under rule DIC

The classes are an ACCOUNTING breakdown of the executable sections (one row per run of bytes, `start length class owner`). They are not a partition of the byte properties. Two independent
over-approximations are what the lift and the gates use:

- MAY-EXECUTE: the byte lies in a range of ranges.tsv (an instruction byte of an entry's flow). Sound because every indirect transfer lands in an entry (R3, closure complete) or is a violation.
- MAY-BE-READ: the byte lies in a data_in_code.tsv range (a proven consumer reads it), or is a relocation slot (class 3), or is in a PE data directory (class 8). A byte can be both (a table whose bytes a
  candidate entry also decodes; a constant inside a run that a proven load reads). The class column records the accounting precedence only.

Facts about a byte that need "not code" or "not data" must be stated from the two sets above, never from the class name.

# entry-decomp
Instruction bytes of a decomp function row (funcs.tsv size). May-execute. May-be-read: only through a data_in_code range overlapping it (none in a proven run: a relocated word in a proven run is an operand, else red).

# entry-nondecomp
Instruction bytes of a non-decomp entry (call, tail, ptr, export, tls, eh-*, seh-*, inner). May-execute. A `candidate` entry (module universe.tsv column 4) is an R3 pointer value with no proven flow: may-execute
until the points-to proves its value reaches no control transfer; a candidate with a relocation misfit is refused (refused.tsv) and its bytes are no entry.

# data-reloc
Bytes of a relocated dword slot outside every reached instruction (class 3, "named by a relocation"): a jump table, vtable or pointer table in .text. May-be-read. Proven consumer: the DIC kind of the range that holds
it (jtab: the slots of a resolved `jmp [T + r*4]`; ptrtable: a load of a data-reloc word bounded by the idxrange object). A slot with no consumer fact stays class 3 and is counted open per image; the bytes
are still may-execute if an entry range covers them (a candidate decoding over a table is refused by the misfit rule).

# data-pe-header
Bytes named by a PE data directory (import, export, resource, TLS, load config...). May-be-read through the loader's documented use. Never may-execute (no entry range covers them; an entry over them is a bad-decode red).

# padding
`nop`/`int3`/`lea`/`mov r,r` fill at the ends of residue runs between entries. May-execute is not claimed; they are reached only as fall-through filler and the lift takes no body from them.

# data-unreached
Residue: valid executable bytes that no entry reaches, no PE directory names and no relocation names, under a COMPLETE closure (every relocated in-span value is an entry, pcsrc s4 green; ASM-225 premise F). Neither
may-execute (no flow reaches them) nor claimed as data beyond that: not may-be-read unless a data_in_code range covers them. With an incomplete closure the same bytes are UNEXPLAINED.

# hole
Bytes between executable sections (alignment of the section table). Not part of the universe. Neither set.

# UNEXPLAINED
A build error: residue bytes while the closure is not complete (reason named: `unent`, `s4-unchecked`, `s4-violation`), or a failed decode. Red in the gate; never read as data or code.

## DIC kinds (data_in_code.tsv, tools/dic.py)
jtab (produced: the slots of a resolved indirect jump table, ehtables.json jtab_tables), ptrtable, string, struct, const, eh (rule defined by L1; produced when the consumer facts exist). A range exists iff a
proven consumer reads it; kind and extent come from the consumer, never from the look of the bytes. Open ranges are counted, not guessed.
