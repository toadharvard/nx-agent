// PATCH (iv): x86_cmps = the ACL2 master definition of x86-cmps (G3: operand order of the SUB and the rep termination).  TRANSCRIPTION, ACL2 books/projects/x86isa/machine/instructions/string.lisp
// at acl2 master 3ccb342c88bd3b815856f4bbc336382287934405 (ASM-009), def-inst x86-cmps, lines 312-560; the pinned model is the translation of ACL2 8.5, whose x86-cmps differs:
//   8.5:    (gpr-arith/logic-spec operand-size #.*OP-SUB* dst src input-rflags)  with src = [rSI], dst = [rDI]  => computes [rDI] - [rSI]   (the model's CMPS operands are swapped: the first operand is [rSI])
//   master: string.lisp:443  (gpr-arith/logic-spec operand-size #.*OP-SUB* src1 src2 input-rflags)  with src1 = [rSI] (:397-414, segment seg-reg) and src2 = [rDI] (:417-433, segment *es*)  => [rSI] - [rDI]
// and the rep handling: master tests rCX = 0 first (string.lisp:383-386: with REPE/REPZ or REPNE/REPNZ and rCX = 0 only rIP advances: write-*ip), then after the comparison decrements rCX and keeps rIP (:453-456),
// and stops (write-*ip) when REPE and ZF = 0 or REPNE and ZF = 1 (:465-471); 8.5 (string.lisp of acl2-85, same def-inst) decremented first and compared the counter with 0 afterwards.  The master body has no
// check-instruction-length.  The rSI/rDI update (:478-559) is the same as in the 8.5 translation, over the renamed src1-addr/src2-addr.  Every other line is the pinned translation's own.
val x86_cmps : (proc_mode, sbits(48), sbits(48), prefixes, bits(8), bits(8), modr_m, sib) -> unit

function x86_cmps (proc_mode, start_rip, temp_rip, prefixes, rex_byte, opcode, modr_m, sib) = {
    let ctx : string = "X86-CMPS";
    let r_m : bits(3) = modr_m[r_m];
    let mod_var : bits(2) = modr_m[mod];
    let reg : bits(3) = modr_m[reg];
    let group_1_prefix : bits(8) = prefixes[rep];
    let p2 : bits(8) = prefixes[seg];
    let p4? : bool = 0x67 == prefixes[adr];
    let counter_addr_size : address_size = select_address_size(proc_mode, Some(prefixes));
    let counter : bits(64) = rgfi_size(bits_of_int(counter_addr_size, 4), 0x1, rex_byte);
    if (group_1_prefix == 0xf3 | group_1_prefix == 0xf2) & counter == 0x0000000000000000 then {
        write_iptr(proc_mode, temp_rip)
    } else {
        let select_byte_operand : bool = 0xa6 == opcode;
        let operand_size : {|1, 2, 4, 8|} = select_operand_size(proc_mode, select_byte_operand, rex_byte, false, prefixes, false, false, false);
        let counter_addr_size_2_4? : bool = counter_addr_size == 2 | counter_addr_size == 4;
        let src1_addr : sbits(65) = {
            if counter_addr_size_2_4? then {
                sail_zero_extend(rgfi_size(bits_of_int(counter_addr_size, 4), 0x6, rex_byte), 65)
            } else {
                sail_sign_extend(rgfi(6), 65)
            }
        };
        if not_bool(counter_addr_size_2_4?) & not_bool(canonical_address_p(signed(src1_addr))) then x86_model_error(":SRC1-ADDR-NOT-CANONICAL");
        let inst_ac? : bool = alignment_checking_enabled_p();
        let seg_reg : seg_reg_idx = select_segment_register(proc_mode, prefixes, mod_var, r_m, sib);
        let src1 : bits(128) = rme_size(proc_mode, select_address_size(proc_mode, Some(prefixes)), operand_size, truncate(src1_addr, 64), None(), seg_reg, ":R", inst_ac?, false);
        let src2_addr : sbits(65) = {
            if counter_addr_size_2_4? then {
                sail_zero_extend(rgfi_size(bits_of_int(counter_addr_size, 4), 0x7, rex_byte), 65)
            } else {
                sail_sign_extend(rgfi(7), 65)
            }
        };
        if not_bool(counter_addr_size_2_4?) & not_bool(canonical_address_p(signed(src2_addr))) then x86_model_error(":SRC2-ADDR-NOT-CANONICAL");
        let src2 : bits(128) = rme_size(proc_mode, select_address_size(proc_mode, Some(prefixes)), operand_size, truncate(src2_addr, 64), None(), 0, ":R", inst_ac?, false);
        let input_rflags : rflagsbits = rflags;
        let (result, output_rflags, undefined_flags) : (bits(64), rflagsbits, rflagsbits) = gpr_arith_logic_spec(operand_size, 4, truncate(src1, 64), truncate(src2, 64), input_rflags);
        write_user_rflags(output_rflags, undefined_flags);
        if group_1_prefix == 0xf3 | group_1_prefix == 0xf2 then {
            write_rgfi_size(bits_of_int(counter_addr_size, 4), 0x1, bits_of_int(unsigned(counter) - 1, 64), rex_byte)
        } else {
            write_iptr(proc_mode, temp_rip)
        };
        let stop_repeat : bool = (group_1_prefix == 0xf3 & output_rflags[zf] == 0b0) | (group_1_prefix == 0xf2 & output_rflags[zf] == 0b1);
        if stop_repeat then { write_iptr(proc_mode, temp_rip) };
        let df : bits(1) = {
            let rflags_var : rflagsbits = rflags;
            rflags_var[df]
        };
        let (src1_addr, src2_addr) : (int, int) = {
            match operand_size {
              1 => {
                  if df == 0b0 then {
                      (signed(truncate(src1_addr, 48)) + 1, signed(truncate(src2_addr, 48)) + 1)
                  } else {
                      (signed(truncate(src1_addr, 48)) - 1, signed(truncate(src2_addr, 48)) - 1)
                  }
              },
              2 => {
                  if df == 0b0 then {
                      (signed(truncate(src1_addr, 48)) + 2, signed(truncate(src2_addr, 48)) + 2)
                  } else {
                      (signed(truncate(src1_addr, 48)) - 2, signed(truncate(src2_addr, 48)) - 2)
                  }
              },
              4 => {
                  if df == 0b0 then {
                      (signed(truncate(src1_addr, 48)) + 4, signed(truncate(src2_addr, 48)) + 4)
                  } else {
                      (signed(truncate(src1_addr, 48)) - 4, signed(truncate(src2_addr, 48)) - 4)
                  }
              },
              _ => {
                  if df == 0b0 then {
                      (signed(truncate(src1_addr, 48)) + 8, signed(truncate(src2_addr, 48)) + 8)
                  } else {
                      (signed(truncate(src1_addr, 48)) - 8, signed(truncate(src2_addr, 48)) - 8)
                  }
              }
            }
        };
        let src1_addr : sbits(49) = bits_of_int(src1_addr, 49);
        let src2_addr : sbits(49) = bits_of_int(src2_addr, 49);
        (match counter_addr_size {
          2 => write_rgfi_size(0x2, 0x6, sail_zero_extend(truncate(src1_addr, 16), 64), rex_byte),
          4 => write_rgfi_size(0x4, 0x6, sail_zero_extend(truncate(src1_addr, 32), 64), rex_byte),
          _ => write_rgfi(6, sail_sign_extend(src1_addr, 64))
        }) : unit;
        (match counter_addr_size {
          2 => write_rgfi_size(0x2, 0x7, sail_zero_extend(truncate(src2_addr, 16), 64), rex_byte),
          4 => write_rgfi_size(0x4, 0x7, sail_zero_extend(truncate(src2_addr, 32), 64), rex_byte),
          _ => write_rgfi(7, sail_sign_extend(src2_addr, 64))
        }) : unit
    }
}
