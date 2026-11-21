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
    'split': 'the symbolic INT n case split differs from the record rows over the 8-bit vector domain',
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
    'recdiff': 'FAILED(record): the record differs from the SDM rule: %s',
    'larlsl': 'uncertified: %s proved over its domain (ZF and the destination for every selector of the decided classes); %d GDT indexes of lar_lsl.undecided_descriptors are outside it (OutsideDomain) and the Form carries no domain',
    'iret': 'uncertified: IRETD at CPL 3 proved over its domain (EFLAGS.NT = 0, CR4.PVI = CR4.VME = 0, 32-bit operand: the CS classes, the pops, the EFLAGS bits); NT = 1 (iret.task_return) and iret.eflags_restored.pvi_vme are undecided rows of the record and %d GDT indexes of jmp_far.undecided_descriptors are outside it (OutsideDomain)',
    'retf': 'uncertified: far return proved over its domain (cases %s); %d GDT indexes of jmp_far.undecided_descriptors are outside it and the Form carries no domain',
}
LR = {'access': 'access_rights', 'ldt': 'ldt_descriptor_access', 'beyond': 'beyond_gdt_limit', 'idx80': 'index >= 0x80', 'zf1': 'zf1_descriptors', 'val': 'value_with_accessed_clear', 'spr': 'same_privilege_return',
      'csc': 'cs_selector_checks', 'gp': 'gp_selector', 'code': 'code_descriptors', 'ncode': 'descriptors_not_code', 'efl': 'eflags_restored', 'acc': 'accessed'}      # the names of the record's lar_lsl and iret members (keys, not quotes)
TEXT_RECDIFF = {
    'm_beyond': 'beyond_gdt_limit: gdt.limit %d does not make index 0x80 the first beyond it',
    'm_ti1': 'ti1: sldt is %r, not the null LDTR that the TI = 1 rows assume',
    'm_zf1': 'zf1_descriptors: record %s, SDM %s',
    'm_zf': '%s 0x%02x RPL %d: ZF record %d, SDM %d',
    'm_masks': 'lar 0x%02x masks %x %x %x',
    'm_valbits': 'lar 0x%02x value %x has bits outside the mask, in the undefined bits or the Accessed bit',
    'm_valfields': 'lar 0x%02x value %x: type/S/DPL differ from the descriptor %s',
    'm_g': 'lar 0x%02x G (bit 23) %d, lsl G %d',
    'm_lim': 'lsl 0x%02x limit %x with G = %d',
    'm_ret': 'returns_to: record %s, SDM %s',
    'm_code': 'code_descriptors: record %s, SDM %s',
    'm_ncode': 'descriptors_not_code differs from the descriptors that are not code segments',
    'm_bits': 'loaded_bits: record %s, SDM %s',
    'm_mask': 'loaded_mask: record %#x, SDM bits %#x (difference %#x)',
}
LEGACY.update(TEXT_RECDIFF)
