"""Append the Remote Hekate boot entry to hekate_ipl.ini without touching anything else."""
from ..common import OpError
from ..ops import REMOTE_ENTRY, hekate_entry_index

PAYLOAD = 'bootloader/payloads/Remote-Hekate.bin'
R2C_MAX_INDEX = 8


def entry_count(text):
    return sum(1 for line in text.splitlines()
               if line.strip().startswith('[') and ']' in line and line.strip()[1:line.strip().index(']')] != 'config')


def add_remote_entry(text, name=REMOTE_ENTRY):
    index = hekate_entry_index(text, name)

    if index is not None:
        if index > R2C_MAX_INDEX:
            raise OpError('invalid', f'[{name}] is entry {index}; R2C reaches only entries 1..{R2C_MAX_INDEX}. '
                          'Move it higher in hekate_ipl.ini manually.')

        return text, {'changed': False, 'index': index}

    index = entry_count(text) + 1

    if index > R2C_MAX_INDEX:
        raise OpError('invalid', f'hekate_ipl.ini already has {index - 1} boot entries; a new entry would be {index}, '
                      f'but R2C reaches only 1..{R2C_MAX_INDEX}. Remove an unused entry first.')

    newline = '\r\n' if '\r\n' in text else '\n'
    body = text if text.endswith(newline) or not text else text + newline
    block = f'{newline}[{name}]{newline}payload={PAYLOAD}{newline}'

    return body + block, {'changed': True, 'index': index}
