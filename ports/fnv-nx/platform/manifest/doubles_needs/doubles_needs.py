"""doubles_needs.py - per test, what it needs from the boundary below the product (platform/manifest/DOUBLES.md, section
'Needs of the tests'). Reads the call counts of the boundary entry points (hooks.cpp, -finstrument-functions on the host twins of
the boundary) of one run per test minus the counts of a run of no test; prints the totals per need and, with an argument,
writes the per-test table (TSV)."""
import re,os,glob,collections,sys
W=os.environ.get('DOUBLES_WORK','/tmp/doubles-needs')   # per/<test>.tsv (counts of one run), base.tsv (a run of no test)
R=os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','..','..'))
sec={}
for h in ['os.h','os_clock.h','os_shm.h','os_world.h']:
    cur=h
    for line in open(f'{R}/platform/os/{h}',errors='replace'):
        m=re.match(r'/\* ---- ([A-Za-z ]+?)[\s:.(,]',line)
        if m: cur=h+': '+m.group(1).strip().lower()
        for fn in re.findall(r'\b(os_[a-z0-9_]+)\s*\(',line):
            if fn not in sec: sec[fn]=cur
NEED={'os.h: locks':'threads+futex: locks','os.h: condition':'threads+futex: condition variables','os.h: threads':'threads+futex: threads','os_world.h':'threads+futex: world stop/resume','os.h: process':'process/host info (exit, ids, memory info, messages, text entry)','os.h: files':'fs: files and directories','os.h: host':'fs: files and directories','os.h: virtual':'vm: reserve/commit/protect','os_shm.h: shared':'section services (os_shm_*)','os_clock.h':'time: clocks and time zone'}
cat=lambda f: ('gpu: nxd3d_gpu_call' if f=='nxd3d_gpu_call' else
  'input poll: fnvnx_input_pump' if f=='fnvnx_input_pump' else
  'boundary (loader/process/threads of the game): fnvnx_bnd_*' if f.startswith('fnvnx_bnd_') and f not in ('fnvnx_bnd_locks_held','fnvnx_bnd_print_held','fnvnx_bnd_print_lock_violations','fnvnx_bnd_violation_mark','fnvnx_bnd_violation_forget') else
  'exit slot: fnvnx_reach_violation' if f=='fnvnx_reach_violation' else
  'test observation (lock counting, violations)' if f.startswith('fnvnx_bnd_') or f=='fnvnx_lockcount_note' else
  NEED.get(sec.get(f), ('UNMAPPED:'+f if f not in sec else sec.get(f))))
base={l.split('\t')[0]:int(l.split('\t')[1]) for l in open(W+'/base.tsv')}
per={}
for p in glob.glob(W+'/per/*.tsv'):
    n=os.path.basename(p)[:-4]
    d=collections.Counter()
    for l in open(p):
        f,c=l.rstrip('\n').split('\t')
        f={'_Z14nxd3d_gpu_calljPKmj':'nxd3d_gpu_call'}.get(f,f)
        c=max(0,int(c)-base.get(f,0))
        if c==0: continue
        c_=cat(f)
        if c_: d[c_]+=int(c)
        else: d['UNMAPPED:'+f]+=int(c)
    per[n]=d
tot=collections.Counter(); ntests=collections.Counter()
for n,d in per.items():
    for k,v in d.items(): tot[k]+=v; ntests[k]+=1
print(len(per),'tests measured')
for k,v in sorted(ntests.items(), key=lambda kv:-kv[1]): print('%4d tests  %10d calls  %s'%(v,tot[k],k))
if len(sys.argv)>1:
    with open(sys.argv[1],'w') as f:
        ks=sorted(ntests)
        f.write('test\t'+'\t'.join(ks)+'\n')
        for n in sorted(per): f.write(n+'\t'+'\t'.join(str(per[n].get(k,0)) for k in ks)+'\n')
