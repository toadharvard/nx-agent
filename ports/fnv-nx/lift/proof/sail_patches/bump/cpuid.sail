// PATCH (vii): x86_cpuid, TRANSCRIPTION of ACL2 books/projects/x86isa/machine/instructions/cpuid.lisp at acl2 master (sail_pins.txt row acl2) (ASM-009): def-inst x86-cpuid, lines 75-112
// (dispatch: inst-listing.lisp:9985-9987 `(INST "CPUID" (OP :OP #xFA2) NIL '(X86-CPUID) '((:UD (UD-LOCK-USED))))`, see cpuid_dispatch.py), with the model's constant tables
// *cpuid-basic-info* (cpuid.lisp:28-43: one entry, eax 0, ebx 0, ecx 0, edx = (logior (ash 1 3) (ash 1 5) (ash 1 6) (ash 1 17)) = 0x00020068) and *cpuid-extended-info* (:44: nil) unfolded through get-cpu-info
// (cpuid.lisp:54-70): for the one-element list every index >= 1 returns that element ((car relevant-info): idx 1 directly, idx > 1 by (cdr relevant-info) = nil -> (not cpu-info) -> (car relevant-info));
// index 0 or an empty list returns eax = (len relevant-info) (+ #x80000000 for the extended list), ebx = ecx = edx = 0 (:96-106).  The ACL2 8.5 translation has no x86-cpuid (opcode 0F A2 was
// `Opcode Unimplemented`).  The values the model returns in eax/ebx/ecx/edx are the model's constants, not the console's: the certificate of this encoding excludes exactly those four outputs and takes
// them from the declared CPU model (rt/cpumodel.c); everything else it certifies is the model's frame.
val x86_cpuid : (proc_mode, sbits(48), sbits(48), prefixes, bits(8), bits(8), modr_m, sib) -> unit

function x86_cpuid (proc_mode, start_rip, temp_rip, prefixes, rex_byte, opcode, modr_m, sib) = {
    let ctx : string = "X86-CPUID";
    let requested_info : bits(32) = rr32(0x0);
    let basic_info? : bool = unsigned(requested_info) < 2147483648;
    let info_idx : int = unsigned(requested_info[30 .. 0]);
    let (info_eax, info_ebx, info_ecx, info_edx) : (bits(32), bits(32), bits(32), bits(32)) = {
        if basic_info? then {
            if info_idx == 0 then {
                (0x00000001, 0x00000000, 0x00000000, 0x00000000)
            } else {
                (0x00000000, 0x00000000, 0x00000000, 0x00020068)
            }
        } else {
            (0x80000000, 0x00000000, 0x00000000, 0x00000000)
        }
    };
    wr32(0x0, info_eax);
    wr32(0x3, info_ebx);
    wr32(0x1, info_ecx);
    wr32(0x2, info_edx);
    write_iptr(proc_mode, temp_rip)
}
