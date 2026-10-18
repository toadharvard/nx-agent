import pathlib,subprocess,json,sys
from .paths import tool_path
out=pathlib.Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
with (out/'transport.log').open('a') as log:
 p=subprocess.Popen([str(tool_path('mtp_read'))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1)
 def call(s):
  p.stdin.write(s+'\n');p.stdin.flush()
  while True:
   l=p.stdout.readline()
   if not l:raise RuntimeError('MTP ended')
   if l.startswith('@JSON '):return json.loads(l[6:])
   log.write(l)
 cache={}
 def children(parent):
  if parent not in cache:cache[parent]=call('L\t'+str(parent))
  return cache[parent]
 try:
  for path in sys.argv[2:]:
   par=0xffffffff;x=None
   for part in path.split('/'):
    x=next((x for x in children(par) if x['name']==part),None)
    if x is None:break
    par=x['id']
   if x is None:print('MISSING',path);continue
   if x['folder']:print('FOLDER',path,json.dumps(children(x['id']),ensure_ascii=False));continue
   if x['size']>16*1024*1024:print('TOO_LARGE',path,x['size']);continue
   dest=out/path;dest.parent.mkdir(parents=True,exist_ok=True)
   result=call('G\t'+str(x['id'])+'\t'+str(dest.resolve()))
   print('READ',path,result,'bytes',dest.stat().st_size)
 finally:p.stdin.close();p.wait(timeout=10)
