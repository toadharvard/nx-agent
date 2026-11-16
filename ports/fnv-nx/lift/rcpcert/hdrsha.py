#!/usr/bin/env python3
"""Generate step of lift/rcpcert: the sha256 of the generated rcpref.h this root's rcpcert is compiled against (output HDR_SHA256 of the root).
usage: hdrsha.py HEADER OUT     Writes the hex digest and a newline. The certificate's header_sha256 is read from this file, so the header is never read a second time from another place."""
import hashlib, sys
open(sys.argv[2], "w").write(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest() + "\n")
