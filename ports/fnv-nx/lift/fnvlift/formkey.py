"""The form key of a decoded instruction: `mnemonic|operand classes|opcode bytes` (R2). One definition, used by the lifter tools (tools/formcert.py) and the tests (test/instest.py)."""
R8 = {'al', 'cl', 'dl', 'bl', 'ah', 'ch', 'dh', 'bh'}; R16 = {'ax', 'cx', 'dx', 'bx', 'sp', 'bp', 'si', 'di'}
R32 = ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi']; SEGS = {'cs', 'ds', 'es', 'ss', 'fs', 'gs'}
PFX = {0x66, 0x67, 0xf0, 0xf2, 0xf3, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65}
def form_key(ins, X):
    raw = bytes(ins.bytes); i = 0
    while i < len(raw) and raw[i] in PFX: i += 1
    opc = raw[i:i + (3 if raw[i] == 0x0f and raw[i + 1] in (0x38, 0x3a) else 2 if raw[i] == 0x0f else 1)]
    ks = []
    for o in ins.operands:
        if o.type == X.X86_OP_REG:
            n = ins.reg_name(o.reg)
            ks.append('xmm' if n.startswith('xmm') else 'st' if n.startswith('st') else 'mm' if n.startswith('mm') else 'seg' if n in SEGS
                      else 'r8' if n in R8 else 'r16' if n in R16 else 'r32' if n in R32 else n)
        elif o.type == X.X86_OP_IMM: ks.append('imm')
        else: ks.append('m%d' % (o.size * 8) + {X.X86_REG_FS: ':fs', X.X86_REG_GS: ':gs'}.get(o.mem.segment, ''))
    return '%s|%s|%s' % (ins.mnemonic, ','.join(ks), opc.hex()), opc
