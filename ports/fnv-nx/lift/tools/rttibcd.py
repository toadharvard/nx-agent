"""rttibcd.py EXE OUT.tsv : Base Class Descriptor facts of the exe's own RTTI, over every Complete Object Locator reached from every vftable.
The vftable/COL discovery is contract/exefacts.py `rtti_vftables` (the RTTI parser we have); this file only reads the structures it points at (MSVC x86-32 layout, absolute pointers):
  COL  {signature, offset, cdOffset, pTypeDescriptor, pClassHierarchyDescriptor}
  CHD  {signature, attributes (bit0 multiple, bit1 virtual inheritance), numBaseClasses, pBaseClassArray}
  BCD  {pTypeDescriptor, numContainedBases, PMD{mdisp, pdisp, vdisp}, attributes, pClassHierarchyDescriptor}
Output rows (tab separated, one per BCD; COL rows carry the COL fields once):
  COL  <col va> <vftable va(s)> <offset> <cdOffset> <TD name> <chd attributes> <numBaseClasses>
  BCD  <col va> <index> <TD name> <numContainedBases> <mdisp> <pdisp> <vdisp> <attributes>
RTTI carries neither class size nor emptiness: nothing here derives either. A structure that does not match the layout (signature, pointer out of the image, TD without a `.?A` name) is a violation and exit status 1."""
import sys, os, struct, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'contract'))
import exefacts
def s32(v): return v - (1 << 32) if v >> 31 else v
def main(exe, out):
    img = exefacts.Image(exe); vft = exefacts.rtti_vftables(img)
    cols = collections.defaultdict(list)
    for v, (name, off, slots, col) in vft.items(): cols[col].append(v)
    bad = []; rows = []; ncls = set(); nbcd = 0; nvirt = 0; nmult = 0; nvb = 0
    def td(a):
        if img.off(a + 8) is None: return None
        n = img.cstr(a + 8); return n if n.startswith('.?A') else None
    for col in sorted(cols):
        sig, offs, cd, ptd, pchd = (img.u32(col + 4 * i) for i in range(5))
        n = td(ptd)
        if sig != 0 or n is None or img.off(pchd) is None: bad.append((col, 'COL layout')); continue
        csig, attr, nb, pbca = (img.u32(pchd + 4 * i) for i in range(4))
        if csig != 0 or img.off(pbca) is None or nb == 0 or nb > 1000: bad.append((col, 'CHD layout')); continue
        rows.append(('COL', '%08x' % col, ','.join('%08x' % v for v in sorted(cols[col])), s32(offs), s32(cd), n, attr, nb)); ncls.add(n)
        nmult += attr & 1 != 0; nvirt += attr & 2 != 0
        for i in range(nb):
            b = img.u32(pbca + 4 * i)
            if img.off(b + 24) is None: bad.append((col, 'BCD %d out of image' % i)); continue
            bptd, nc, mdisp, pdisp, vdisp, battr, bchd = (img.u32(b + 4 * j) for j in range(7))
            bn = td(bptd)
            if bn is None or img.off(bchd) is None: bad.append((col, 'BCD %d layout' % i)); continue
            rows.append(('BCD', '%08x' % col, i, bn, nc, s32(mdisp), s32(pdisp), s32(vdisp), '0x%x' % battr)); nbcd += 1
            if pdisp != 0xffffffff: nvb += 1
    with open(out, 'w') as f:
        f.write('# rtti-bcd/1 exe_sha256=%s base=0x%x: COL rows (col, vftables, offset, cdOffset, TD name, CHD attributes, numBaseClasses) and BCD rows (col, index, TD name, numContainedBases, mdisp, pdisp, vdisp, attributes); BCD attributes bit0 not-visible, bit1 ambiguous, bit2 private/protected, bit3 public-in-private, bit4 virtual-base-of-hierarchy, bit5 non-polymorphic, bit6 has-CHD; a virtual base is a BCD with pdisp != -1 (vbtable displacement); the CHD attribute word is given raw (the exe CHDs with virtual bases carry attributes 0, so the flag is not used)\n' % (img.sha256, img.base))
        for r in rows: f.write('\t'.join(str(x) for x in r) + '\n')
    print('exe %s: vftables %d, COLs %d, classes %d (TD names), BCDs %d, BCDs of virtual bases (pdisp != -1) %d, CHDs multiple-inheritance %d, virtual-inheritance %d, malformed %d' % (img.sha256[:12], len(vft), len(cols), len(ncls), nbcd, nvb, nmult, nvirt, len(bad)))
    for c, w in bad[:10]: print('  %08x %s' % (c, w))
    return 1 if bad else 0
if __name__ == '__main__': sys.exit(main(sys.argv[1], sys.argv[2]))
