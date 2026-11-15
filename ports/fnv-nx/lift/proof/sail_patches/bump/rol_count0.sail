// PATCH (v): rol_spec_8/16/32/64 for the masked count 0: no flag is affected and none is undefined.  TRANSCRIPTION, ACL2 books/projects/x86isa/machine/instructions/rotates-spec.lisp at acl2 master
// (sail_pins.txt row acl2) (ASM-009), define rol-spec-gen, lines 285-288:  (0 ;; No flags are affected when the masked count is 0 (per Intel SDM ...) (mv input-rflags 0)).
// The pinned translation (ACL2 8.5, rotates-spec.lisp 286-289 of acl2-85) returned input-rflags with undefined flags = {OF} ("No flags, except OF, affected. OF is undefined.").  Only that arm changes (G1);
// every other line is the pinned translation's own (the arms of count 1 and >= 2 are not changed by master's rotates-spec.lisp for rol).
val rol_spec_8 : (bits(8), bits(6), rflagsbits) -> (bits(8), rflagsbits, rflagsbits)

function rol_spec_8 (dst, src, input_rflags) = {
    let result : bits(8) = rotate_left(dst, 8, unsigned(src));
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => {
              (input_rflags, Mk_rflagsbits(0x00000000))
          },
          1 => {
              let cf : bits(1) = result[0 .. 0];
              let of : int = b_xor(unsigned(cf), unsigned(logbit(7, result)));
              let output_rflags : rflagsbits = [input_rflags with cf, of = bits_of_int(of, 1)];
              (output_rflags, Mk_rflagsbits(0x00000000))
          },
          _ => {
              let cf : bits(1) = result[0 .. 0];
              let output_rflags : rflagsbits = [input_rflags with cf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}

val rol_spec_16 : (bits(16), bits(6), rflagsbits) -> (bits(16), rflagsbits, rflagsbits)

function rol_spec_16 (dst, src, input_rflags) = {
    let result : bits(16) = rotate_left(dst, 16, unsigned(src));
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => {
              (input_rflags, Mk_rflagsbits(0x00000000))
          },
          1 => {
              let cf : bits(1) = result[0 .. 0];
              let of : int = b_xor(unsigned(cf), unsigned(logbit(15, result)));
              let output_rflags : rflagsbits = [input_rflags with cf, of = bits_of_int(of, 1)];
              (output_rflags, Mk_rflagsbits(0x00000000))
          },
          _ => {
              let cf : bits(1) = result[0 .. 0];
              let output_rflags : rflagsbits = [input_rflags with cf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}

val rol_spec_32 : (bits(32), bits(6), rflagsbits) -> (bits(32), rflagsbits, rflagsbits)

function rol_spec_32 (dst, src, input_rflags) = {
    let result : bits(32) = rotate_left(dst, 32, unsigned(src));
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => {
              (input_rflags, Mk_rflagsbits(0x00000000))
          },
          1 => {
              let cf : bits(1) = result[0 .. 0];
              let of : int = b_xor(unsigned(cf), unsigned(logbit(31, result)));
              let output_rflags : rflagsbits = [input_rflags with cf, of = bits_of_int(of, 1)];
              (output_rflags, Mk_rflagsbits(0x00000000))
          },
          _ => {
              let cf : bits(1) = result[0 .. 0];
              let output_rflags : rflagsbits = [input_rflags with cf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}

val rol_spec_64 : (bits(64), bits(6), rflagsbits) -> (bits(64), rflagsbits, rflagsbits)

function rol_spec_64 (dst, src, input_rflags) = {
    let result : bits(64) = rotate_left(dst, 64, unsigned(src));
    let (output_rflags, undefined_flags) : (rflagsbits, rflagsbits) = {
        match src {
          0 => {
              (input_rflags, Mk_rflagsbits(0x00000000))
          },
          1 => {
              let cf : bits(1) = result[0 .. 0];
              let of : int = b_xor(unsigned(cf), unsigned(logbit(63, result)));
              let output_rflags : rflagsbits = [input_rflags with cf, of = bits_of_int(of, 1)];
              (output_rflags, Mk_rflagsbits(0x00000000))
          },
          _ => {
              let cf : bits(1) = result[0 .. 0];
              let output_rflags : rflagsbits = [input_rflags with cf];
              let undefined_flags : rflagsbits = [Mk_rflagsbits(0x00000000) with of = 0b1];
              (output_rflags, undefined_flags)
          }
        }
    };
    (result, output_rflags, undefined_flags)
}

