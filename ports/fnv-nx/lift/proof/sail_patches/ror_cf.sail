// PATCH (i): ror_spec_8/16/32/64.  Two hunks, both now TRANSCRIPTIONS of ACL2 books/projects/x86isa/machine/instructions/rotates-spec.lisp at acl2 master 3ccb342c88bd3b815856f4bbc336382287934405 (ASM-009):
//   (a) masked count 0: no flag affected, none undefined (G1; master rotates-spec.lisp:662-665 `(0 ;; No flags are affected when the masked count is 0 ... (mv input-rflags 0))`; the pinned
//       ACL2 8.5 translation returned the undefined flag OF here);
//   (b) the `_ =>` arm: CF = MSB of the result (master :709-717 `(cf ;; CF = MSB of the result. (mbe :logic (logbit ,size-1 result) ...))`; the pinned translation had result[0 .. 0]).
// Original model line of (b) (rotates_spec.sail): let cf : bits(1) = result[0 .. 0]; the `1 =>` arm already uses the msb.

val ror_spec_8 : (bits(8), bits(6), rflagsbits) -> (bits(8), rflagsbits, rflagsbits)

function ror_spec_8 (dst, src, input_rflags) = {
    let result : bits(8) = rotate_right(dst, 8, unsigned(src));
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => {
              (input_rflags, Mk_rflagsbits(0x00000000))
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
              (input_rflags, Mk_rflagsbits(0x00000000))
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
              (input_rflags, Mk_rflagsbits(0x00000000))
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
              (input_rflags, Mk_rflagsbits(0x00000000))
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
