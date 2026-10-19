"""Bulk USB transport for the Worker (interface 0) and Rescue (interface 1) channels."""
import struct, time, fcntl
import usb.core, usb.util
from .. import paths
MAGIC=0x31425255
STREAM_MAGIC=0x4d525453
STREAM_CHUNK=1<<20
class UsbSocket:
    def __init__(self,connect_gdb=False,interface_number=0):
        if interface_number not in (0,1):raise ValueError("Unknown sidecar interface")
        self.interface_number=interface_number
        lockname='sidecar-usb.lock' if interface_number==0 else 'sidecar-rescue-usb.lock'
        lockpath=paths.usb_lock(lockname)
        self.lock=lockpath.open('a')
        self.claimed=False;self.dev=None;self.closed=False;self.faulted=False
        lock_deadline=time.monotonic()+1
        while True:
            try:
                fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic()>=lock_deadline:
                    self.lock.close();raise RuntimeError('Another sidecar/debugger client owns USB')
                time.sleep(.02)
        try:self._open(connect_gdb)
        except BaseException:
            self.close();raise
    def _open(self,connect_gdb):
        self.dev=usb.core.find(idVendor=0x057e,idProduct=0x3000)
        if self.dev is None: raise RuntimeError('USB Debug Relay is not running or cable is disconnected')
        cfg=self.dev.get_active_configuration()
        pairs=[]
        for interface in cfg:
            if interface.bInterfaceNumber!=self.interface_number:continue
            endpoints=[e for e in interface if usb.util.endpoint_type(e.bmAttributes)==usb.util.ENDPOINT_TYPE_BULK]
            ins=[e for e in endpoints if usb.util.endpoint_direction(e.bEndpointAddress)==usb.util.ENDPOINT_IN]
            outs=[e for e in endpoints if usb.util.endpoint_direction(e.bEndpointAddress)==usb.util.ENDPOINT_OUT]
            if ins and outs:pairs.append((interface,ins[0],outs[0]))
        if len(pairs)!=1:raise RuntimeError(f'USB interface {self.interface_number} is unavailable; expected one bulk IN/OUT pair')
        if self.interface_number==1 and (pairs[0][0].bInterfaceClass,pairs[0][0].bInterfaceSubClass,pairs[0][0].bInterfaceProtocol)!=(0xff,0x52,1):
            raise RuntimeError('USB interface is not a recovery channel')
        self.interface,self.epin,self.epout=pairs[0]
        usb.util.claim_interface(self.dev,self.interface.bInterfaceNumber)
        self.claimed=True
        self.buffer=bytearray()
        if connect_gdb:self.connect_gdb()
    def connect_gdb(self):
        error,_,retry=self.command(1)
        deadline=time.monotonic()+5
        while error and retry and time.monotonic()<deadline:
            time.sleep(.05);error,_,retry=self.command(5)
        if error:
            self.close();raise RuntimeError(f'Console debugger connection failed: guest errno {error}')
    def command(self,op,data=b'',timeout=2000):
        if self.closed or self.faulted:
            raise RuntimeError('USB stream is closed or reply state is uncertain; no command sent')
        if len(data)>4080:raise ValueError('USB frame overflow')
        frame=struct.pack('<4I',MAGIC,op,len(data),0)+data+bytes(4080-len(data))
        try:
            if self.epout.write(frame,timeout=2000)!=4096:raise IOError('Short USB write')
            reply=bytes(self.epin.read(4096,timeout=timeout))
            if len(reply)!=4096:raise IOError('Short USB reply')
            magic,error,size,retry=struct.unpack_from('<4I',reply)
            if magic!=MAGIC or size>4080:raise IOError('Malformed USB relay reply')
        except Exception:
            # A timed-out request may still run on the console. A further command,
            # including cleanup, could consume its late reply as its own response.
            self.faulted=True
            raise
        return error,(size if op==2 else reply[16:16+size]),retry
    def drain_pending_replies(self, seconds=10, max_frames=64):
        """Read only; discard old replies and the rest of an interrupted outbound stream, then require a
        fresh status handshake.

        This never replays the operation whose result was lost. In particular,
        it does not resolve whether an earlier mutation completed on the device.
        """
        if self.closed:raise RuntimeError('USB stream is closed')
        if not 0 < seconds <= 10 or not 1 <= max_frames <= 256:
            raise ValueError('Invalid drain bounds')
        self.faulted=True  # close() must not send cleanup on this old stream.
        end=time.monotonic()+seconds;count=quiet=0
        while time.monotonic()<end:
            try:reply=bytes(self.epin.read(STREAM_CHUNK,timeout=250))
            except usb.core.USBTimeoutError:
                quiet+=1
                if quiet==2:return count
                continue
            quiet=0
            if len(reply)==4096 and struct.unpack_from('<I',reply)[0]==MAGIC:
                count+=1
                if count>max_frames:break
        raise TimeoutError('Pending USB replies did not quiesce within drain bounds')
    def stream_command(self,op,data,timeout=2000):
        """Frame op that must answer with STREAM_MAGIC; returns the reply payload. The console may do work
        proportional to the file (hash, allocation) before it replies, so the caller sizes the timeout."""
        error,payload,marker=self.command(op,data,timeout)
        if error:raise RuntimeError(f'Sidecar command {op}: console error 0x{error:x}')
        if marker!=STREAM_MAGIC:
            self.faulted=True
            raise IOError(f'Sidecar command {op}: no stream announced')
        return payload
    def write_stream(self,source,size,timeout=30000):
        """Send exactly size bytes. On failure pad with zeros so the console stays in step."""
        sent=0
        try:
            while sent<size:
                chunk=source.read(min(STREAM_CHUNK,size-sent))
                if not chunk:raise IOError('Local file shrank during upload')
                if self.epout.write(chunk,timeout=timeout)!=len(chunk):raise IOError('Short USB stream write')
                sent+=len(chunk)
        except Exception:
            self.faulted=True
            try:
                while sent<size:
                    n=min(STREAM_CHUNK,size-sent);self.epout.write(bytes(n),timeout=timeout);sent+=n
            except Exception:pass
            raise
    def read_stream(self,sink,size,timeout=30000):
        got=0
        try:
            while got<size:
                data=bytes(self.epin.read(min(STREAM_CHUNK,size-got),timeout=timeout))
                if not data:raise IOError('Empty USB stream read')
                sink.write(data);got+=len(data)
        except Exception:
            self.faulted=True
            raise
    def read_frame(self,timeout):
        try:
            reply=bytes(self.epin.read(4096,timeout=timeout))
            if len(reply)!=4096:raise IOError('Short USB reply')
            magic,error,size,_=struct.unpack_from('<4I',reply)
            if magic!=MAGIC or size>4080:raise IOError('Malformed USB relay reply')
        except Exception:
            self.faulted=True
            raise
        return error
    def sendall(self,data):
        deadline=time.monotonic()+5
        while data:
            error,n,retry=self.command(2,data[:4080])
            if error and not retry:raise IOError(f'Console send errno {error}')
            if not error and n:data=data[n:]
            elif time.monotonic()>deadline:raise TimeoutError('Console send timed out')
            else:time.sleep(.01)
    def recv(self,count):
        deadline=time.monotonic()+5
        while not self.buffer:
            error,data,retry=self.command(3)
            if error and not retry:raise IOError(f'Console receive errno {error}')
            if data:self.buffer.extend(data)
            elif time.monotonic()>deadline:raise TimeoutError('Console debugger did not reply')
            else:time.sleep(.01)
        data=bytes(self.buffer[:count]);del self.buffer[:count];return data
    def close(self):
        if self.closed:return
        try:
            if self.claimed:
                try:
                    if not self.faulted:self.command(4)
                except Exception:pass
                finally:usb.util.release_interface(self.dev,self.interface.bInterfaceNumber)
        finally:
            self.closed=True
            if self.dev is not None:usb.util.dispose_resources(self.dev)
            self.lock.close()
