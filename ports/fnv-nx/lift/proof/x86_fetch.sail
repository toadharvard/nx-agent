// Splice (x86_code_regs.sail is compiled first and declares the two registers) for the Sail x86 model (isla-sail -splice): instruction fetch reads come from a register window instead of memory.
// `code_bytes` holds up to 16 instruction bytes (byte 0 = lowest bits) starting at linear address `code_base`; only
// accesses with access kind ":X" (instruction fetch) are redirected, data reads/writes are unchanged (symbolic memory).
val rb : forall 'addr_width 'n, 'addr_width > 0 & 'n > 0. (int('n), bits('addr_width), string) -> bits(8 * 'n)

function rb (n, addr, access_kind) = {
  result : bits(8 * 'n) = sail_zeros(8 * n);
  foreach (offset from 0 to (n - 1)) {
    if not_bool(canonical_address_p(signed(addr) + offset)) then x86_model_error("rb: non-canonical address");
    let lin_addr : bits(48) = add_bits_int(sail_mask_signed(48, addr), offset);
    if access_kind == ":X" then {
      let i : int = unsigned(lin_addr) - unsigned(code_base);
      if i < 0 | i > 15 then x86_model_error("fetch outside the code window");
      result[((8 * offset) + 7) .. (8 * offset)] = slice(code_bytes, 8 * i, 8)
    } else {
      let phys_addr : bits(52) =
        if app_view then sail_mask_signed(52, lin_addr) else ia32e_la_to_pa(lin_addr, access_kind)
      in
      result[((8 * offset) + 7) .. (8 * offset)] = memi(phys_addr)
    }
  };
  return result
}
