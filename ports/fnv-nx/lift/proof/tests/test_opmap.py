"""opmap.lookup on synthetic maps (the cells are the shapes of Tables A-2, A-3 and A-6, written here; the real tables are read from the pinned SDM PDF by opmap.load)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from proof import opmap

M = {'one': {(0xc4, ''): (['LESi64 Gz, Mp', 'VEX+2byte'], 1), (0x27, ''): (['DAAi64'], 1), (0x63, ''): (['ESC'], 1)},
     'two': {(0x19, ''): (['Reserved-NOP'], 2), (0x1f, ''): (['NOP /0 Ev'], 2)},
     'groups': [((False, (0x80, 0x81, 0x82, 0x83)), '1', '', ['ADD', 'OR', 'ADC', 'SBB', 'AND', 'SUB', 'XOR', 'CMP'], 3),
                ((False, (0xd0,)), '2', '', ['ROL', 'ROR', 'RCL', 'RCR', 'SHL', 'SHR', '', 'SAR'], 3)]}


class T(unittest.TestCase):
    def test_cells(self):
        self.assertEqual(opmap.lookup(M, '', b'\xc4\x00')[0], 'Table A-2')                  # LES: a real entry beside the VEX one
        self.assertEqual(opmap.lookup(M, '', b'\x0f\x19\x00')[0], 'Table A-3')              # Reserved-NOP
        self.assertIsNone(opmap.lookup(M, '', b'\x0f\x1e\x00'))                             # blank cell
        self.assertIsNone(opmap.lookup(M, '', b'\x63\x00'))                                 # an escape cell defines nothing here

    def test_groups(self):
        self.assertEqual(opmap.lookup(M, '', b'\x82\x00')[0], 'Table A-6 Group 1')           # 82 /0 is Group 1
        self.assertIsNone(opmap.lookup(M, '', b'\xd0\xf0'))                                  # D0 /6: blank cell of Group 2


if __name__ == '__main__':
    unittest.main()
