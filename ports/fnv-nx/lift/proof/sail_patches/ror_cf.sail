// PATCH (i): ROR carry flag for masked counts >= 2 is the most significant bit of the result.
// SDM vol. 2, ROR: "the CF flag receives a copy of the bit that was shifted from one end to the other", i.e. for ROR
// (bits leave at the least-significant end and enter at the most-significant end) CF = MSB of the result.
// Original model line (rotates_spec.sail, in the `_ =>` arm of ror_spec_8/16/32/64; the `1 =>` arm already uses the msb):
//   let cf : bits(1) = result[0 .. 0];
// The functions below are the originals with exactly that line changed to the msb of the result.

val ror_spec_8 : (bits(8), bits(6), rflagsbits) -> (bits(8), rflagsbits, rflagsbits)

function ror_spec_8 (dst, src, input_rflags) = {
    let result : bits(8) = rotate_right(dst, 8, unsigned(src));
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => {
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (input_rflags, undefined_flags)
          },
          1 => {
              let cf : bits(1) = logbit(7, result);
              let of : int = b_xor(unsigned(logbit(7, result)), unsigned(result[6 .. 6]));
              let output_rflags : rflagsbits = [input_rflags with cf, of = bits_of_int(of, 1)];
              (output_rflags, Mk_rflagsbits(0x00000000))
          },
          _ => {
              let cf : bits(1) = logbit(7, result);
              let output_rflags : rflagsbits = [input_rflags with cf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}

val ror_spec_16 : (bits(16), bits(6), rflagsbits) -> (bits(16), rflagsbits, rflagsbits)

function ror_spec_16 (dst, src, input_rflags) = {
    let result : bits(16) = rotate_right(dst, 16, unsigned(src));
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => {
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (input_rflags, undefined_flags)
          },
          1 => {
              let cf : bits(1) = logbit(15, result);
              let of : int = b_xor(unsigned(logbit(15, result)), unsigned(result[14 .. 14]));
              let output_rflags : rflagsbits = [input_rflags with cf, of = bits_of_int(of, 1)];
              (output_rflags, Mk_rflagsbits(0x00000000))
          },
          _ => {
              let cf : bits(1) = logbit(15, result);
              let output_rflags : rflagsbits = [input_rflags with cf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}

val ror_spec_32 : (bits(32), bits(6), rflagsbits) -> (bits(32), rflagsbits, rflagsbits)

function ror_spec_32 (dst, src, input_rflags) = {
    let result : bits(32) = rotate_right(dst, 32, unsigned(src));
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => {
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (input_rflags, undefined_flags)
          },
          1 => {
              let cf : bits(1) = logbit(31, result);
              let of : int = b_xor(unsigned(logbit(31, result)), unsigned(result[30 .. 30]));
              let output_rflags : rflagsbits = [input_rflags with cf, of = bits_of_int(of, 1)];
              (output_rflags, Mk_rflagsbits(0x00000000))
          },
          _ => {
              let cf : bits(1) = logbit(31, result);
              let output_rflags : rflagsbits = [input_rflags with cf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}

val ror_spec_64 : (bits(64), bits(6), rflagsbits) -> (bits(64), rflagsbits, rflagsbits)

function ror_spec_64 (dst, src, input_rflags) = {
    let result : bits(64) = rotate_right(dst, 64, unsigned(src));
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => {
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (input_rflags, undefined_flags)
          },
          1 => {
              let cf : bits(1) = logbit(63, result);
              let of : int = b_xor(unsigned(logbit(63, result)), unsigned(result[62 .. 62]));
              let output_rflags : rflagsbits = [input_rflags with cf, of = bits_of_int(of, 1)];
              (output_rflags, Mk_rflagsbits(0x00000000))
          },
          _ => {
              let cf : bits(1) = logbit(63, result);
              let output_rflags : rflagsbits = [input_rflags with cf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}
