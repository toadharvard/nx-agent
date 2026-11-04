"""fixture: the S13b cause policy (tools/difffuzz/causes.py POLICY), a few causes with made-up reasons"""
POLICY = {
    'x87-precision': ('by-design', '-', False, 'x87 80-bit intermediates'),
    'nan-bits': ('by-design', '-', False, 'NaN payload only'),
    'new-null': ('artifact', 'S13', False, 'the harness allocator returns 0'),
    'native-undefined': ('real-bug', 'S1', True, 'an unbound name'),
    'split-global': ('real-bug', 'S2c', True, 'the unit copy of an image variable'),
    'call-flow': ('triage', 'S13b', True, 'the call sequence differs'),
}
