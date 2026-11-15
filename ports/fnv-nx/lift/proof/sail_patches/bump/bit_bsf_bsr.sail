// PATCH (viii): x86_bsr and x86_bsf, TRANSCRIPTION of ACL2 books/projects/x86isa/machine/instructions/bit.lisp at acl2 master 3ccb342c88bd3b815856f4bbc336382287934405 (ASM-009), with the dispatch
// of inst-listing.lisp:10314-10317 (BSF 0F BC, no prefix: X86-BSF, (:UD (UD-LOCK-USED))) and :10322-10325 (BSR 0F BD, no prefix: X86-BSR, same UD rule), see bsf_bsr_dispatch.py.  The pinned model (the translation
// of ACL2 8.5) has no x86_bsf / x86_bsr: opcode 0F BC is `Translation error: ... not translated` and 0F BD is `Opcode Unimplemented`, i.e. the isla poison path.
//   bit.lisp:755-824 def-inst x86-bsr: operand-size (select-operand-size ... nil ...), seg-reg, source = x86-operand-from-modr/m-and-sib-bytes (gpr access, operand-size, inst-ac? t, no immediate), rip increment,
//     check-instruction-length (:#GP(0) on a bad length), then (:810-823) the destination register (reg-index reg rex *r*) is written with (bsr source) at operand-size ONLY IF source /= 0 (comment :760-763 "AMD ...
//     unchanged ... I use the latter behavior"), ZF = (source = 0), CF PF AF SF OF undefined (!flgi-undefined), write-*ip.
//   bit.lisp:870-954 def-inst x86-bsf: the same body; ZF = (reg/mem = 0) (:936-937), CF OF SF AF PF undefined (:939-943), the destination is written with (bsf 0 reg/mem) only if reg/mem /= 0 (:945-953, "AMD ... unchanged").
// The ACL2 helpers are recursive on the value: (bsr n) = 0 for n = 1, else 1 + (bsr (logcdr n)) (bit.lisp:715-723): the index of the most significant set bit; (bsf index x) = index for the lowest set bit of x, else
// (bsf (1+ index) (logtail 1 x)) (:831-843): the index of the least significant set bit.  isla executes concrete control flow only, so the recursion cannot run on a symbolic source.  The closed forms below, on bits(64)
// (the source is zero-extended from its operand size), are branch-free and equal to the recursions for every nonzero 64-bit value: bsr = popcount(x | x>>1 | .. | x>>32) - 1 (the smear sets every bit at or below
// the most significant set bit, there are index + 1 of them) and bsf = popcount(~x & (x - 1)) (the bits below the lowest set bit: x - 1 flips the lowest set bit and every bit under it, ~x keeps the zeros
// among them, the ones above are cleared by ~x there; index many).  The equivalence of both with the recursions unrolled over 64 bits is a z3 check: lift/proof/tests/test_bsfbsr_closed_form.py.
val bsx_popcount64 : bits(64) -> bits(64)

function bsx_popcount64 (x) = {
    let x1 : bits(64) = (x & 0x5555555555555555) + ((x >> 1) & 0x5555555555555555);
    let x2 : bits(64) = (x1 & 0x3333333333333333) + ((x1 >> 2) & 0x3333333333333333);
    let x3 : bits(64) = (x2 & 0x0f0f0f0f0f0f0f0f) + ((x2 >> 4) & 0x0f0f0f0f0f0f0f0f);
    let x4 : bits(64) = (x3 & 0x00ff00ff00ff00ff) + ((x3 >> 8) & 0x00ff00ff00ff00ff);
    let x5 : bits(64) = (x4 & 0x0000ffff0000ffff) + ((x4 >> 16) & 0x0000ffff0000ffff);
    (x5 & 0x00000000ffffffff) + ((x5 >> 32) & 0x00000000ffffffff)
}

val bsx_bsr64 : bits(64) -> bits(64)

function bsx_bsr64 (x) = {
    let s1 : bits(64) = x | (x >> 1);
    let s2 : bits(64) = s1 | (s1 >> 2);
    let s3 : bits(64) = s2 | (s2 >> 4);
    let s4 : bits(64) = s3 | (s3 >> 8);
    let s5 : bits(64) = s4 | (s4 >> 16);
    let s6 : bits(64) = s5 | (s5 >> 32);
    sub_bits(bsx_popcount64(s6), 0x0000000000000001)
}

val bsx_bsf64 : bits(64) -> bits(64)

function bsx_bsf64 (x) = {
    bsx_popcount64(not_vec(x) & sub_bits(x, 0x0000000000000001))
}

val x86_bsx : (proc_mode, sbits(48), sbits(48), prefixes, bits(8), bits(8), modr_m, sib, bool) -> unit

function x86_bsx (proc_mode, start_rip, temp_rip, prefixes, rex_byte, opcode, modr_m, sib, is_bsr) = {
    let r_m : bits(3) = modr_m[r_m];
    let mod_var : bits(2) = modr_m[mod];
    let reg : bits(3) = modr_m[reg];
    let p2 : bits(8) = prefixes[seg];
    let p4? : bool = 0x67 == prefixes[adr];
    let seg_reg : seg_reg_idx = select_segment_register(proc_mode, prefixes, mod_var, r_m, sib);
    let operand_size : {|1, 2, 4, 8|} = select_operand_size(proc_mode, false, rex_byte, false, prefixes, false, false, false);
    let inst_ac? : bool = true;
    let (source, increment_rip_by, addr) : (bits(128), {|0, 1, 2, 4|}, sbits(64)) = x86_operand_from_modr_m_and_sib_bytes(proc_mode, 0b0, operand_size, inst_ac?, false, seg_reg, prefixes, temp_rip, rex_byte, r_m, mod_var, sib, 0b000);
    let increment_rip_by : bits(3) = bits_of_int(increment_rip_by, 3);
    let temp_rip : sbits(48) = add_to_iptr(proc_mode, temp_rip, sail_zero_extend(increment_rip_by, 48));
    let badlength? : option(int) = check_instruction_length(start_rip, temp_rip, 0b000);
    if is_some(badlength?) then x86_fault("(!!FAULT-FRESH :GP 0 :INSTRUCTION-LENGTH BADLENGTH?)");
    let src : bits(64) = truncate(source, 64);
    let zero? : bool = src == 0x0000000000000000;
    if not_bool(zero?) then {
        let res : bits(64) = if is_bsr then bsx_bsr64(src) else bsx_bsf64(src);
        write_rgfi_size(bits_of_int(operand_size, 4), reg_index(reg, rex_byte, 0b10), res, rex_byte)
    };
    let rflags_var : rflagsbits = rflags in
    let new_rflags : rflagsbits = [rflags_var with zf = if zero? then 0b1 else 0b0] in rflags = new_rflags;
    let rflags_var : rflagsbits = rflags in
    let sailval : bits(1) = undef_flg() in
    let new_rflags : rflagsbits = [rflags_var with cf = sailval] in rflags = new_rflags;
    let rflags_var : rflagsbits = rflags in
    let sailval : bits(1) = undef_flg() in
    let new_rflags : rflagsbits = [rflags_var with pf = sailval] in rflags = new_rflags;
    let rflags_var : rflagsbits = rflags in
    let sailval : bits(1) = undef_flg() in
    let new_rflags : rflagsbits = [rflags_var with af = sailval] in rflags = new_rflags;
    let rflags_var : rflagsbits = rflags in
    let sailval : bits(1) = undef_flg() in
    let new_rflags : rflagsbits = [rflags_var with sf = sailval] in rflags = new_rflags;
    let rflags_var : rflagsbits = rflags in
    let sailval : bits(1) = undef_flg() in
    let new_rflags : rflagsbits = [rflags_var with of = sailval] in rflags = new_rflags;
    write_iptr(proc_mode, temp_rip)
}

val x86_bsr : (proc_mode, sbits(48), sbits(48), prefixes, bits(8), bits(8), modr_m, sib) -> unit

function x86_bsr (proc_mode, start_rip, temp_rip, prefixes, rex_byte, opcode, modr_m, sib) = x86_bsx(proc_mode, start_rip, temp_rip, prefixes, rex_byte, opcode, modr_m, sib, true)

val x86_bsf : (proc_mode, sbits(48), sbits(48), prefixes, bits(8), bits(8), modr_m, sib) -> unit

function x86_bsf (proc_mode, start_rip, temp_rip, prefixes, rex_byte, opcode, modr_m, sib) = x86_bsx(proc_mode, start_rip, temp_rip, prefixes, rex_byte, opcode, modr_m, sib, false)
