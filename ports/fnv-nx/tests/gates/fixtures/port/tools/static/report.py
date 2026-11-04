import os

CLASSES = [
    ('W-BENIGN', 'a warning with no effect', '', 'benign', ''),
    ('F2S', 'float -> signed integer', '', 'compiler', 'the fnvnx_ftoi plugin'),
    ('BARRIER', 'compiler barrier', '', 'header', 'dmb fences'),
    ('VOLATILE-DECL', 'volatile without acquire/release', '', 'compiler', 'the fnvnx_order plugin'),
]

# what the current build closes; the real tool's build_closures() reads compile.sh and the shim headers the same way
PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
CLOSED = {}
try:
    with open(os.path.join(PORT, 'tools', 'link', 'compile.sh')) as f:
        if 'toolchain/order' in f.read():
            CLOSED['VOLATILE-DECL'] = 'compiler: fnvnx_order plugin'
except OSError:
    pass
