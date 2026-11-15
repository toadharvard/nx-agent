"""The CPU fault table (fnvlift/faults.json) as Python: the one source of dsl.FAULTS, dsl.FLT_STATUS and proof/lift_dsl.FAULT_MAP.  The C side (rt/fnvl_faults.h,
rt/seh_faults.inc) is generated from the same record by tools/gen_faults.py."""
import json, os
ROWS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'faults.json')))['rows']
assert len({r['code'] for r in ROWS}) == len(ROWS) and len({r['name'] for r in ROWS}) == len(ROWS)
BYNAME = {r['name']: r for r in ROWS}
FAULTS = {r['code']: r['name'] for r in ROWS if r['form']}                 # the Form kinds
FLT_STATUS = {r['code']: int(r['status'], 16) for r in ROWS if r['name'].startswith('FLT_')}
def runtime_name(r): return r.get('alias', r['name'])
def fault_map():
    """Form kind -> (FNVL_* name or 'AV', at, trap); DE0's kind is split into DE0 / DEOVF by the condition (lift_dsl.de_code), so it is not a row of the map"""
    m = {}
    for r in ROWS:
        if not r['form'] or r['name'] == 'DE0': continue
        m[r['code']] = ('AV' if r.get('entry') == 'raise_av' else 'FNVL_' + runtime_name(r), r['at'], r['trap'])
    return m
