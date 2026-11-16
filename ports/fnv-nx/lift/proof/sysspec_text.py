"""Result texts of the system specs (sysspec_gap.py, sysspec_int.py): messages only, no SDM quotes (the quote checker reads the spec files, not this one)."""
GAP = {
    'addr': 'address-size prefix 0x67 (16-bit addressing is outside this spec)',
    'addrres': 'address-size prefix 0x67 on an instruction with no memory operand: the SDM calls that use reserved (no behaviour is defined)',
    'seg': 'segment override (the spec is the flat DS/ES of the declared machine)',
    'rep': 'LOCK/REP prefix 0x%02x',
    'segmem': 'segment override on the memory operand',
    'cond': 'conditional store / floating point state',
    'bswap16': 'uncertified: 16-bit BSWAP: the SDM result is undefined',
    'okbswap': 'SDM BSWAP, 32-bit register',
    'okxlat': 'SDM XLAT, 32-bit address size',
    'mov16': 'uncertified: 16-bit register destination (the upper bits are not specified for it)',
    'okmov': 'SDM MOV r/m16, Sreg: DEST := sel_%s',
    'btsw': 'uncertified: operand size %d',
    'btsop': 'uncertified: operand-size prefix on a 32-bit form',
    'okbts': 'SDM BTS: CF := Bit(BitBase, BitOffset); Bit(BitBase, BitOffset) := 1',
    'req': 'sysspec_gap: --x86sys PATH is required\n',
    'unc': 'uncertified: %s',
}
INT = {
    'inv': 'int_ring3.other_vectors is not the access violation of the fault table',
    'bad': 'INT n differs from the record row at vector(s) %s',
    'unc': 'uncertified: int_ring3.excluded.%d: %s (%d of %d vectors proved)',
    'ok': 'SDM INT n: every vector is a record row (%d vectors)',
    'addr': 'the int instruction',
}
SEG = {'none': 'no spec', 'unc': 'uncertified: %s', 'undec': 'uncertified: mov_to_sreg.undecided: %s', 'spurious': 'the Form builder raised UndecidedRow where the record decides the row', 'noundecided': 'the record leaves a selector undecided: a Form must not exist',
       'ok': 'SDM %s Sreg: the segment register %s', 'diff': 'differs: %s'}
FARROW = {'gp': 'gp_selector_descriptors', 'data': 'data_descriptors', 'und': 'undecided_descriptors'}
LEGACY = {
    'ok': 'SDM %s: the Form equals the Operation',
    'diff': 'differs: %s',
    'unc': 'uncertified: %s',
    'cond': 'conditional store / floating point state / selector write',
    'rep': 'LOCK/REP prefix 0x%02x',
    'cleared': 'uncertified: cleared-#UD class, no cell in the SDM opcode map: %s; the Form is fault(UD) under the site certificate of L3a',
    'nospec': 'no spec: the record now decides the rows of this Form',
    'o16': 'uncertified: operand-size prefix 0x66 on a BCD adjust: the SDM states no effect of it',
    'sldt16': 'uncertified: 16-bit register destination (the upper bits are not specified for it)',
    'noundecided': 'the record decides mov_to_sreg: the Form must exist and does not',
    'retf': 'uncertified: far return proved over its domain (cases %s); %d GDT indexes of jmp_far.undecided_descriptors are outside it and the Form carries no domain',
}
