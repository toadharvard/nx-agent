// PATCH (iii): BT r/m, r with a memory operand: the selected bit of the byte is (offset MOD 8) with floor semantics for a negative offset.
// SDM vol. 2, BT: for a memory operand and a register bit offset the offset is signed; the addressed unit is EffectiveAddress + (size/8) * (BitOffset DIV size)
// and the bit is BitOffset MOD size, DIV = floor, MOD = non-negative remainder (AMD APM vol. 3, BT, same).  Viewed byte-wise: byte floor(offset/8), bit offset - 8*floor(offset/8).
// Original model lines (bit.sail, x86_bt_0f_a3), which take the absolute value first and so select the mirror bit for a negative offset (offset -1: bit 1, real bit 7):
//   let bitoffset_int_abs : int = abs(signed(bitoffset_int));
//   let bitnumber : int = mod(bitoffset_int_abs, 8);
// (byte_addr already uses floor2).  Confirmed on the host by test/bthost.py (all 2^16 offsets for m16, a class cover for m32).  The function below is the original with exactly
// those two lines replaced by the single line using floor2.  bts/btr/btc (0F AB/B3/BB) and 0F BA /5../7 are `Opcode Unimplemented` in the model: nothing to patch there.

val x86_bt_0f_a3 : (proc_mode, sbits(48), sbits(48), prefixes, bits(8), bits(8), modr_m, sib) -> unit

function x86_bt_0f_a3 (proc_mode, start_rip, temp_rip, prefixes, rex_byte, opcode, modr_m, sib) = {
    let ctx : string = "X86-BT-0F-A3";
    let r_m : bits(3) = modr_m[r_m];
    let mod_var : bits(2) = modr_m[mod];
    let reg : bits(3) = modr_m[reg];
    let p2 : bits(8) = prefixes[seg];
    let p4? : bool = 0x67 == prefixes[adr];
    let seg_reg : seg_reg_idx = select_segment_register(proc_mode, prefixes, mod_var, r_m, sib);
    let operand_size : {|1, 2, 4, 8|} = select_operand_size(proc_mode, false, rex_byte, false, prefixes, false, false, false);
    let bitoffset : bits(64) = rgfi_size(bits_of_int(operand_size, 4), reg_index(reg, rex_byte, 0b10), rex_byte);
    let (addr, increment_rip_by) : (sbits(64), {|0, 1, 2, 4|}) = {
        if mod_var == 0b11 then {
            (0x0000000000000000, 0)
        } else {
            let p4? : bool = 0x67 == prefixes[adr];
            x86_effective_addr(proc_mode, prefixes, temp_rip, rex_byte, r_m, mod_var, sib, 0b000)
        }
    };
    let increment_rip_by : bits(3) = bits_of_int(increment_rip_by, 3);
    let temp_rip : sbits(48) = add_to_iptr(proc_mode, temp_rip, sail_zero_extend(increment_rip_by, 48));
    let badlength? : option(int) = check_instruction_length(start_rip, temp_rip, 0b000);
    if is_some(badlength?) then x86_fault("(!!FAULT-FRESH :GP 0 :INSTRUCTION-LENGTH BADLENGTH?)");
    let (bitoffset, bitbase) : (int, bits(128)) = {
        if mod_var == 0b11 then {
            (mod(unsigned(bitoffset), ash(operand_size, 3)), sail_zero_extend(rgfi_size(bits_of_int(operand_size, 4), reg_index(r_m, rex_byte, 0b00), rex_byte), 128))
        } else {
            let bitoffset_int : sbits(64) = {
                match operand_size {
                  1 => sail_sign_extend(truncate(bitoffset, 8), 64),
                  2 => sail_sign_extend(truncate(bitoffset, 16), 64),
                  4 => sail_sign_extend(truncate(bitoffset, 32), 64),
                  _ => bitoffset
                }
            };
            let bitnumber : int = signed(bitoffset_int) - 8 * floor2(signed(bitoffset_int), 8);
            let byte_addr : int = signed(addr) + floor2(signed(bitoffset_int), 8);
            let inst_ac? : bool = alignment_checking_enabled_p();
            let byte : bits(128) = {
                if signed_byte_p(64, byte_addr) then {
                    rme_size(proc_mode, select_address_size(proc_mode, Some(prefixes)), 1, bits_of_int(byte_addr, 64), None(), seg_reg, ":R", inst_ac?, false)
                } else {
                    x86_model_error("EFFECTIVE-ADDRESS-ERROR")
                }
            };
            (bitnumber, byte)
        }
    };
    let rflags_var : rflagsbits = rflags in
    let new_rflags : rflagsbits = [rflags_var with cf = logbit(bitoffset, bitbase)] in rflags = new_rflags;
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
