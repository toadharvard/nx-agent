// PATCH (vi): sar_spec_8/16/32/64 for a masked count >= the operand size: CF = the most significant bit of the destination, and CF is no longer an undefined flag (G1).  TRANSCRIPTION, ACL2
// books/projects/x86isa/machine/instructions/shifts-spec.lisp at acl2 master 3ccb342c88bd3b815856f4bbc336382287934405 (ASM-009), define sar-spec-gen, lines 797-852 (the `otherwise` arm):
//   (cf (if (<= ,size src) (mbe :logic (part-select dst :low ,size-1 :width 1) ...) (mbe :logic (part-select dst :low (1- src) :width 1) ...)))   then pf zf sf from the result, AF and OF undefined
// The pinned ACL2 8.5 translation had a separate `(if (<= ,size src)` arm (acl2-85 shifts-spec.lisp 791-851) that left CF undefined and unchanged.  Only the `_ =>` arm changes; the count 0 and 1 arms
// and everything before the match are the pinned translation's own (they are not in the master diff).
val sar_spec_8 : (bits(8), bits(6), rflagsbits) -> (bits(8), rflagsbits, rflagsbits)

function sar_spec_8 (dst, src, input_rflags) = {
    let neg_src : sbits(9) = bits_of_int(negate(unsigned(src)), 9);
    let raw_result_not_sign_extended : bits(8) = bits_of_int(ash(unsigned(dst), signed(neg_src)), 8);
    let raw_result : int = {
        if logbit(7, dst) == 0b1 then {
            loghead(8, ash(binary_logext(8, unsigned(dst)), signed(neg_src)))
        } else {
            unsigned(raw_result_not_sign_extended)
        }
    };
    let result : bits(8) = bits_of_int(raw_result, 8);
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => (input_rflags, Mk_rflagsbits(0x00000000)),
          1 => {
              let cf : bits(1) = dst[0 .. 0];
              let pf : bits(1) = pf_spec8(result);
              let zf : bits(1) = zf_spec(unsigned(result));
              let sf : bits(1) = sf_spec8(result);
              let of : {|0|} = 0;
              let output_rflags : rflagsbits = [input_rflags with cf, pf, zf, sf, of = bits_of_int(of, 1)];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with af = 0b1];
              (output_rflags, undefined_flags)
          },
          _ => {
              let cf : bits(1) = {
                  if 8 <= unsigned(src) then {
                      bitslice(dst, 7, 1)
                  } else {
                      bitslice(dst, unsigned(src) - 1, 1)
                  }
              };
              let pf : bits(1) = pf_spec8(result);
              let zf : bits(1) = zf_spec(unsigned(result));
              let sf : bits(1) = sf_spec8(result);
              let output_rflags : rflagsbits = [input_rflags with cf, pf, zf, sf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with af = 0b1, of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}

val sar_spec_16 : (bits(16), bits(6), rflagsbits) -> (bits(16), rflagsbits, rflagsbits)

function sar_spec_16 (dst, src, input_rflags) = {
    let neg_src : sbits(17) = bits_of_int(negate(unsigned(src)), 17);
    let raw_result_not_sign_extended : bits(16) = bits_of_int(ash(unsigned(dst), signed(neg_src)), 16);
    let raw_result : int = {
        if logbit(15, dst) == 0b1 then {
            loghead(16, ash(binary_logext(16, unsigned(dst)), signed(neg_src)))
        } else {
            unsigned(raw_result_not_sign_extended)
        }
    };
    let result : bits(16) = bits_of_int(raw_result, 16);
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => (input_rflags, Mk_rflagsbits(0x00000000)),
          1 => {
              let cf : bits(1) = dst[0 .. 0];
              let pf : bits(1) = pf_spec16(result);
              let zf : bits(1) = zf_spec(unsigned(result));
              let sf : bits(1) = sf_spec16(result);
              let of : {|0|} = 0;
              let output_rflags : rflagsbits = [input_rflags with cf, pf, zf, sf, of = bits_of_int(of, 1)];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with af = 0b1];
              (output_rflags, undefined_flags)
          },
          _ => {
              let cf : bits(1) = {
                  if 16 <= unsigned(src) then {
                      bitslice(dst, 15, 1)
                  } else {
                      bitslice(dst, unsigned(src) - 1, 1)
                  }
              };
              let pf : bits(1) = pf_spec16(result);
              let zf : bits(1) = zf_spec(unsigned(result));
              let sf : bits(1) = sf_spec16(result);
              let output_rflags : rflagsbits = [input_rflags with cf, pf, zf, sf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with af = 0b1, of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}

val sar_spec_32 : (bits(32), bits(6), rflagsbits) -> (bits(32), rflagsbits, rflagsbits)

function sar_spec_32 (dst, src, input_rflags) = {
    let neg_src : sbits(33) = bits_of_int(negate(unsigned(src)), 33);
    let raw_result_not_sign_extended : bits(32) = bits_of_int(ash(unsigned(dst), signed(neg_src)), 32);
    let raw_result : int = {
        if logbit(31, dst) == 0b1 then {
            loghead(32, ash(binary_logext(32, unsigned(dst)), signed(neg_src)))
        } else {
            unsigned(raw_result_not_sign_extended)
        }
    };
    let result : bits(32) = bits_of_int(raw_result, 32);
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => (input_rflags, Mk_rflagsbits(0x00000000)),
          1 => {
              let cf : bits(1) = dst[0 .. 0];
              let pf : bits(1) = pf_spec32(result);
              let zf : bits(1) = zf_spec(unsigned(result));
              let sf : bits(1) = sf_spec32(result);
              let of : {|0|} = 0;
              let output_rflags : rflagsbits = [input_rflags with cf, pf, zf, sf, of = bits_of_int(of, 1)];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with af = 0b1];
              (output_rflags, undefined_flags)
          },
          _ => {
              let cf : bits(1) = {
                  if 32 <= unsigned(src) then {
                      bitslice(dst, 31, 1)
                  } else {
                      bitslice(dst, unsigned(src) - 1, 1)
                  }
              };
              let pf : bits(1) = pf_spec32(result);
              let zf : bits(1) = zf_spec(unsigned(result));
              let sf : bits(1) = sf_spec32(result);
              let output_rflags : rflagsbits = [input_rflags with cf, pf, zf, sf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with af = 0b1, of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}

val sar_spec_64 : (bits(64), bits(6), rflagsbits) -> (bits(64), rflagsbits, rflagsbits)

function sar_spec_64 (dst, src, input_rflags) = {
    let neg_src : sbits(65) = bits_of_int(negate(unsigned(src)), 65);
    let raw_result_not_sign_extended : bits(64) = bits_of_int(ash(unsigned(dst), signed(neg_src)), 64);
    let raw_result : int = {
        if logbit(63, dst) == 0b1 then {
            loghead(64, ash(binary_logext(64, unsigned(dst)), signed(neg_src)))
        } else {
            unsigned(raw_result_not_sign_extended)
        }
    };
    let result : bits(64) = bits_of_int(raw_result, 64);
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => (input_rflags, Mk_rflagsbits(0x00000000)),
          1 => {
              let cf : bits(1) = dst[0 .. 0];
              let pf : bits(1) = pf_spec64(result);
              let zf : bits(1) = zf_spec(unsigned(result));
              let sf : bits(1) = sf_spec64(result);
              let of : {|0|} = 0;
              let output_rflags : rflagsbits = [input_rflags with cf, pf, zf, sf, of = bits_of_int(of, 1)];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with af = 0b1];
              (output_rflags, undefined_flags)
          },
          _ => {
              let cf : bits(1) = {
                  if 64 <= unsigned(src) then {
                      bitslice(dst, 63, 1)
                  } else {
                      bitslice(dst, unsigned(src) - 1, 1)
                  }
              };
              let pf : bits(1) = pf_spec64(result);
              let zf : bits(1) = zf_spec(unsigned(result));
              let sf : bits(1) = sf_spec64(result);
              let output_rflags : rflagsbits = [input_rflags with cf, pf, zf, sf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with af = 0b1, of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}

