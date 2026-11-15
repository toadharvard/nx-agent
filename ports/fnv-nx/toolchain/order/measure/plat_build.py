import sys, os, json
repo=sys.argv[1]
sys.path.insert(0, os.path.join(repo,'ports/fnv-nx/verify'))
import recipe
store='/tmp/claude-0/s/gc'
out='/tmp/claude-0/s/rc-out'
res=recipe.build(repo,'ports/fnv-nx/platform',store,out)
print(res['status'], json.dumps(res.get('failure'))[:2000])
json.dump([{'unit':o['unit'],'f2i':o.get('f2i')} for o in res.get('objects',[])], open('/tmp/claude-0/s/esc-objs.json','w'))
print(len(res.get('objects',[])),'objects', res['stages'])
