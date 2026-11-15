// PATCH (iv): the base of FS and GS is a free value, per segment: an INITIAL-STATE encoding, no computation of the model changes.
// DERIVED FROM the pinned source: ACL2 x86isa `segment-base-and-bounds` (x86isa project of the ACL2 community books, v8.5), as translated by sail-x86-from-acl2 @0c6c023dccc7c20d8d9dbfc481f01d9bb49d62bf,
// model/segmentation.sail `segment_base_and_bounds`: x86isa keeps one hidden base per segment register (field seg-hidden-basei = Sail register seg_hidden_bases, registers.sail:116, indexed by the
// segment register) and the function returns the entry of the register it is asked about.  Secondary: SDM Vol. 3, 3.4.3 (descriptor Base) and Vol. 1, 3.4.2.1: every segment register has its own base.
// isla can initialise seg_hidden_bases with ONE uniform value for all six entries (value_parser.lalrpop `[v; n]`), so a proof run could not give FS / GS a base without giving the same base to SS and ES;
// the prover driver compensated by adding the base to every access of an instruction with a 0x64 / 0x65 byte, which broke push / popal / string stores (stack and ES:EDI are not FS / GS).
// The function below is the original with ONLY the `base` line of the compatibility-mode branch replaced: entries 4 (FS) and 5 (GS) read the registers fs_base_reg / gs_base_reg
// (x86_code_regs.sail), left symbolic by the prover; every other entry is read as in the original.
// Original line:  let base : bits(64) = loghead(64, seg_hidden_bases[seg_reg]);
//
val segment_base_and_bounds : (proc_mode, range(0, 5)) -> (bits(64), int, bits(32))

function segment_base_and_bounds (proc_mode, seg_reg) = {
    if in_64bit_mode(proc_mode) then {
        if seg_reg == 4 then {
            (read_msr(1), 0, 0x00000000)
        } else if seg_reg == 5 then {
            (read_msr(2), 0, 0x00000000)
        } else {
            (0x0000000000000000, 0, 0x00000000)
        }
    } else if in_compatibility_mode(proc_mode) then {
        let base : bits(64) = {
            if seg_reg == 4 then sail_zero_extend(fs_base_reg, 64)
            else if seg_reg == 5 then sail_zero_extend(gs_base_reg, 64)
            else loghead(64, seg_hidden_bases[seg_reg])
        };
        let limit : bits(32) = loghead(32, seg_hidden_limits[seg_reg]);
        let attr : bits(16) = loghead(16, seg_hidden_attrs[seg_reg]);
        let d_b : bits(1) = {
            if seg_reg == 1 then {
                Mk_code_segment_descriptor_attributesbits(attr)[d]
            } else {
                Mk_data_segment_descriptor_attributesbits(attr)[d_b]
            }
        };
        let e : bits(1) = {
            if seg_reg == 1 then 0b0 else {
                Mk_data_segment_descriptor_attributesbits(attr)[e]
            }
        };
        let lower : int = {
            if e == 0b1 then {
                1 + unsigned(limit)
            } else 0
        };
        let upper : bits(32) = {
            if e == 0b1 then {
                if d_b == 0b1 then 0xffffffff else 0x0000ffff
            } else {
                limit
            }
        };
        (sail_zero_extend(truncate(base, 32), 64), lower, upper)
    } else {
        (0x0000000000000000, 0, 0x00000000)
    }
}
