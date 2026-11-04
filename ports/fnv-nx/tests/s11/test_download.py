"""test_download.py SETUP_MSVC OUTDIR: setup_msvc.sh --download against a local server that cuts every response.

The server answers Range requests and closes the connection after CUT bytes of each response, as the proxy cut the
farms' ISO downloads (curl exit 18). The download must resume until size and SHA-256 match; a wrong prefix left by an
earlier run must end in a mismatch at full size and one fresh download; a server that ignores ranges must still work.
"""
import hashlib
import http.server
import os
from pathlib import Path
import subprocess
import sys
import threading

SETUP, OUT = sys.argv[1], Path(sys.argv[2])
DATA = bytes((i * 7 + (i >> 9)) & 0xff for i in range(1 << 20))
SHA = hashlib.sha256(DATA).hexdigest()
CUT = 300_000
seen = []


class Cutting(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        start = 0
        rng = self.headers.get('Range')
        if rng and self.path != '/norange' and rng.startswith('bytes='):
            start = int(rng[6:].split('-')[0] or 0)
        seen.append((self.path, start))
        body = DATA[start:]
        self.send_response(206 if start else 200)
        if start:
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, len(DATA) - 1, len(DATA)))
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Accept-Ranges', 'none' if self.path == '/norange' else 'bytes')
        self.end_headers()
        cut = len(body) if self.path == '/norange' else CUT
        try:
            self.wfile.write(body[:cut])
            self.wfile.flush()
        except ConnectionError:   # curl hangs up on a 200 to a range request (case 4)
            pass
        self.close_connection = True


srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Cutting)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = 'http://127.0.0.1:%d' % srv.server_address[1]
env = dict(os.environ, DL_RETRY='0', DL_PAUSE='0', DL_STALLS='4', no_proxy='127.0.0.1', NO_PROXY='127.0.0.1')
fails = []


def run(path, out):
    seen.clear()
    r = subprocess.run(['bash', SETUP, '--download', url + path, str(out), str(len(DATA)), SHA], env=env,
                       capture_output=True, text=True, timeout=120)
    ok = r.returncode == 0 and out.exists() and hashlib.sha256(out.read_bytes()).hexdigest() == SHA
    return ok, r.stderr, list(seen)


# 1. every response cut at 300 KB: four resumed requests
ok, err, reqs = run('/iso', OUT / 'a.iso')
starts = [s for _, s in reqs]
if not ok or starts != [0, CUT, 2 * CUT, 3 * CUT]:
    fails.append('resume: ok=%s requests=%s %s' % (ok, starts, err[-300:]))
# 2. a wrong prefix from an earlier run: resumed to full size, mismatch, downloaded again
bad = OUT / 'b.iso'
bad.write_bytes(b'x' * 500_000)
ok, err, reqs = run('/iso', bad)
if not ok or 'SHA-256 mismatch' not in err or [s for _, s in reqs][:2] != [500_000, 800_000]:
    fails.append('mismatch: ok=%s requests=%s %s' % (ok, reqs, err[-300:]))
# 3. complete already: no request
ok, err, reqs = run('/iso', OUT / 'a.iso')
if not ok or reqs:
    fails.append('complete: ok=%s requests=%s' % (ok, reqs))
# 4. a server without ranges sends everything from 0 (curl exit 33 on a partial file: start over)
part = OUT / 'c.iso'
part.write_bytes(DATA[:1000])
ok, err, reqs = run('/norange', part)
if not ok:
    fails.append('no ranges: ok=%s requests=%s %s' % (ok, reqs, err[-300:]))
# 5. no progress at all: gives up
srv.shutdown(); srv.server_close()
ok, err, reqs = run('/iso', OUT / 'd.iso')
if ok or 'no progress' not in err:
    fails.append('stall: ok=%s %s' % (ok, err[-300:]))

for f in fails:
    print('FAIL', f)
print('test_download: %d failures' % len(fails))
sys.exit(1 if fails else 0)
