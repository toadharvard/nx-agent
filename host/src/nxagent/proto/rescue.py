"""Independent rescue USB interface. Never substitutes a worker connection.

A rollback image is promoted only explicitly after on-device acceptance tests.
No mutation is automatically replayed after an uncertain USB result.
"""
import hashlib,json,struct,time
from pathlib import Path
from .. import paths
from .worker import Sidecar
from .transport import UsbSocket
WORKER='sdmc:/atmosphere/contents/420000000000D06B/exefs.nsp'
GOOD='sdmc:/config/usb-rescue/known-good.nsp'
PM_NOT_FOUND=0x20f

class Rescue(Sidecar):
    @classmethod
    def drain_usb(cls):
        transport=UsbSocket(connect_gdb=False,interface_number=1)
        try:return transport.drain_pending_replies()
        finally:transport.close()

    def __init__(self):
        self.usb=UsbSocket(connect_gdb=False,interface_number=1)
        try:
            version,role,interface,_=struct.unpack('<4I',self.call(6))
            if version not in (6,7) or (role,interface)!=(0x52534355,1):
                raise RuntimeError('Not the independent rescue module')
            self.capabilities=struct.unpack('<6I',self.call(9))
        except BaseException:
            self.usb.close();raise
    def query_worker(self):
        rc,pid=struct.unpack('<IQ',self.call(60))
        if rc not in (0,PM_NOT_FOUND):raise RuntimeError(f'Worker query failed: 0x{rc:x}')
        return {'running':rc==0,'pid':pid,'result':hex(rc)}
    def stop_worker(self):
        self.call(61,b'STOP')
        end=time.monotonic()+10
        while time.monotonic()<end:
            status=self.query_worker()
            if not status['running']:return status
            time.sleep(.1)
        raise TimeoutError('Worker did not stop; no file replacement allowed')
    def launch_status(self):
        if not self.capabilities[1]&1:
            return {'supported':False}
        state,result,pid,job=struct.unpack('<IIQQ',self.call(65))
        return {'supported':True,'state':state,'result':result,'pid':pid,'job':job}
    def start_worker(self):
        if not self.capabilities[1]&1:
            return struct.unpack('<Q',self.call(62,b'START'))[0]
        job=struct.unpack('<Q',self.call(64,b'START'))[0]
        end=time.monotonic()+15
        while time.monotonic()<end:
            status=self.launch_status()
            if status['job']!=job:
                raise RuntimeError('Launch job changed; do not resend START')
            if status['state']==3:
                self.call(66,struct.pack('<Q',job))
                if status['result']:
                    raise RuntimeError(f"Worker launch failed: 0x{status['result']:x}")
                return status['pid']
            time.sleep(.1)
        raise TimeoutError(f'Launch job {job} still pending; inspect launch-status, never replay START')
    def verified_image(self,path,expected):
        content=self.get(path,16*1024*1024)
        if hashlib.sha256(content).hexdigest()!=expected:
            raise RuntimeError('Image hash differs from accepted hash; no write performed')
        if not content.startswith(b'PFS0'):raise RuntimeError('Expected ExeFS NSP')
        return content
    def good_exists(self):
        e,_,_=self.usb.command(18,GOOD.encode())
        if e==2:return False
        if e:raise RuntimeError(f'Cannot inspect rollback image: 0x{e:x}')
        return True

def verify_worker(seconds=15):
    s=Sidecar.wait_for_usb(seconds)
    try:
        if s.capabilities[0] not in (6,7):raise RuntimeError('Unexpected worker protocol version')
        return {'capabilities':list(s.capabilities),'app':s.app(),
                'scope':'Protocol and app-query check only; not full stability acceptance'}
    finally:s.close()

OPERATIONS=('status','stop','start','launch-status','promote','rollback','install')

def recover_stream():
    u=UsbSocket(connect_gdb=False,interface_number=1)
    try:count=u.drain_pending_replies()
    finally:u.close()
    r=Rescue()
    try:return {'drained':count,'worker':r.query_worker()}
    finally:r.close()

def operation(cmd,sha256=None,fallback_sha256=None,image=None,device_tested=False):
    if cmd not in OPERATIONS:raise ValueError(f'Unknown rescue operation {cmd}')
    if cmd=='promote' and not device_tested:raise ValueError('promote requires device_tested=True')
    if cmd in ('promote','rollback','install') and not sha256:raise ValueError(f'{cmd} requires sha256')
    if cmd=='install' and (not fallback_sha256 or image is None):raise ValueError('install requires image and fallback_sha256')
    log={'operation':cmd,'started_ns':time.time_ns(),'steps':[],'status':'started'}
    run=paths.operations_dir()/f'rescue-operation-{log["started_ns"]}'
    run.mkdir(parents=True)
    def record(stage,value):
        log['steps'].append({'stage':stage,'value':value});(run/'manifest.json').write_text(json.dumps(log,indent=2)+'\n')
    r=None
    try:
        r=Rescue();record('rescue_connected',True)
        if cmd=='launch-status':record('launch',r.launch_status())
        elif cmd=='status':record('worker',r.query_worker())
        elif cmd=='stop':record('worker_stopped',r.stop_worker())
        elif cmd=='start':record('worker_started',r.start_worker());record('worker_probe',verify_worker())
        else:
            if cmd=='promote':
                record('worker_probe',verify_worker());content=r.verified_image(WORKER,sha256)
                create=not r.good_exists();target=GOOD
            elif cmd=='rollback':content=r.verified_image(GOOD,sha256);target=WORKER;create=False
            else:
                r.verified_image(GOOD,fallback_sha256)
                content=Path(image).read_bytes()
                if hashlib.sha256(content).hexdigest()!=sha256 or not content.startswith(b'PFS0'):
                    raise RuntimeError('Candidate hash/format mismatch')
                target=WORKER;create=False
            record('worker_stopped',r.stop_worker())
            record('write',r.put(target,content,create=create))
            record('worker_started',r.start_worker())
            record('worker_probe',verify_worker())
        log['status']='completed';record('complete',True)
    except BaseException as e:
        log['status']='failed_or_uncertain_inspect_before_retry';record('error',str(e));raise
    finally:
        if r:r.close()
    return {'operation_log':str(run/'manifest.json'),**log}
