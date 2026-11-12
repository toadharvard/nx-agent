// PATCH (iv): the base of FS and GS is a free value, per segment.
// SDM Vol. 3, 3.4.3 (segment descriptors: the Base field) and Vol. 1, 3.4.2.1: every segment register has its own base address; in Windows user mode FS (the TEB) and GS
// are not zero while CS, DS, ES, SS are flat (base 0).  The model reads the hidden base of the segment that the access goes through (seg_reg: CS 1, SS 2, DS 3, ES 0, FS 4, GS 5);
// the original takes it from the vector seg_hidden_bases, which isla can only initialise with ONE value for all six segments, so a proof run could not give FS / GS a base
// without giving the same base to the stack (SS) and to ES: the prover's driver compensated by adding the base to EVERY access of an instruction that carries a 0x64 / 0x65 byte,
// which made `push` / `popal` / string stores with an FS / GS byte (where the segment prefix does not apply to the stack or ES:EDI access) fail against the DSL.
// The function below is the original (segmentation.sail, segment_base_and_bounds) with ONLY the `base` line of the compatibility-mode branch replaced: FS / GS read the registers
// fs_base_reg / gs_base_reg (x86_code_regs.sail), left symbolic by the prover.
// Original line:  let base : bits(64) = loghead(64, seg_hidden_bases[seg_reg]);

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
