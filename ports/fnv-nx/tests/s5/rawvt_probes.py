#!/usr/bin/env python3
"""rawvt_probes.py - emu_guest.py --probe lines for S1's raw-slot runtime list (fnv-nx-build recon/s1_rawvt_sites
rawvt_runtime.tsv): at each listed function's vtable slot load feeding a blr/br (offset slot*4 or (slot+1)*4), the
base register (the vptr).

    tests/s5/rawvt_probes.py <rawvt_runtime.tsv> <guest fnvnx.elf> <probes.txt>   then --probe @probes.txt

Prints one line per row: exe function, slot, sites found. Names and addresses only."""
import sys,re,subprocess
tsv,elf,out=sys.argv[1:4]
nm=subprocess.run(['aarch64-linux-gnu-nm','-S',elf],capture_output=True,text=True).stdout.split('\n')
sz={};addr={}
for l in nm:
    f=l.split()
    if len(f)==4: addr[f[3]]=int(f[0],16); sz[f[3]]=int(f[1],16)
    elif len(f)==3: addr.setdefault(f[2],int(f[0],16))
probes=[];meta=[]
for l in open(tsv):
    if l.startswith('#') or not l.strip(): continue
    c=l.rstrip('\n').split('\t'); unit,line,rt,recv,slot,why,exe_fn=c[:7]
    fn='__fnv_'+exe_fn
    if fn not in addr: meta.append((exe_fn,slot,'no-native')); continue
    # find the real (sized) symbol at that address
    a=addr[fn]; n=max([sz[k] for k,v in addr.items() if v==a and k in sz]+[0x400])
    d=subprocess.run(['aarch64-linux-gnu-objdump','-d','--no-show-raw-insn',f'--start-address={a:#x}',f'--stop-address={a+n:#x}',elf],capture_output=True,text=True).stdout
    found=0
    for m in re.finditer(r'^\s*([0-9a-f]+):\s+ldr\s+w(\d+), \[x(\d+), #(\d+)\]',d,re.M):
        pc,dst,base,off=int(m.group(1),16),m.group(2),m.group(3),int(m.group(4))
        if off not in (int(slot)*4,(int(slot)+1)*4): continue
        rest=d[m.end():m.end()+400]
        if not re.search(r'(blr\s+x%s\b|mov\s+x16, x%s\b)'%(dst,dst),rest): continue
        probes.append(f"{fn}+{pc-a:#x}:x{base}"); found+=1
    meta.append((exe_fn,slot,f'{found} sites'))
open(out,'w').write('\n'.join(probes)+'\n')
for m in meta: print(*m,sep='\t')
