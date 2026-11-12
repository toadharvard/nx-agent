// PATCH (ii): rotate_left_bits / rotate_right_bits must reduce the rotation count modulo the operand width.
// SDM vol. 2 (ROL/ROR/RCL/RCR): the count is masked to 5 bits, then for 8- and 16-bit operands the rotation is by
// count MOD size (RCL/RCR: MOD size+1; the model rotates a width+1 bit value, so the same fix applies).
// Original model lines (prelude.sail 510-514 and 528-532), which shift by `width-places`, i.e. by a negative amount (= a left
// shift) when places > width, so rol bl,9 of 0x7f gave 0 instead of 0xfe:
//   function rotate_left_bits (x, width, places) = { let high = sail_shiftleft(x, places); let low = sail_shiftright(x, width-places); high | low }
//   function rotate_right_bits (x, width, places) = { let low = sail_shiftright(x, places); let high = sail_shiftleft(x, width-places); high | low }
val rotate_left_bits : forall 'n. (bits('n), int, int) -> bits('n)
function rotate_left_bits (x, width, places) = {
  let p = mod(places, width);
  let high = sail_shiftleft(x, p);
  let low = sail_shiftright(x, width - p);
  high | low
}

val rotate_right_bits : forall 'n. (bits('n), int, int) -> bits('n)
function rotate_right_bits (x, width, places) = {
  let p = mod(places, width);
  let low = sail_shiftright(x, p);
  let high = sail_shiftleft(x, width - p);
  high | low
}
