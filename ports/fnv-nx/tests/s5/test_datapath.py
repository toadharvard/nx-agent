#!/usr/bin/env python3
"""test_datapath.py - tests/s5/datapath.py (the emulated boot's SD mounts and data milestones) on synthetic files we
make here: a tiny INI, two empty-shaped archives and ESM-shaped files with a TES4 tag. No game data.

    python3 -I tests/s5/test_datapath.py
"""
from __future__ import annotations

import errno
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import datapath  # noqa: E402


def put(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class DataPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s5-datapath-")
        self.root = os.path.join(self.tmp, "sd")
        self.game = os.path.join(self.tmp, "game")
        os.makedirs(self.root)
        put(os.path.join(self.game, "Fallout_default.ini"),
            b"[General]\r\nsLanguage=ENGLISH\r\n[Archive]\r\nSArchiveList=Synth - One.bsa, Synth - Two.bsa\r\n")
        put(os.path.join(self.game, "Data", "Synth - One.bsa"), b"BSA\0" + bytes(60))
        put(os.path.join(self.game, "Data", "Synth - Two.bsa"), b"BSA\0" + bytes(60))
        put(os.path.join(self.game, "Data", "Synth - Unlisted.bsa"), b"BSA\0")
        self.esm = b"TES4" + bytes(20) + b"GRUP" + bytes(100)
        put(os.path.join(self.game, "Data", "FalloutNV.esm"), self.esm)
        put(os.path.join(self.game, "Data", "Extra.esm"), b"TES4" + bytes(40))
        put(os.path.join(self.root, "switch", "fnv", "user", datapath.PLUGINS), b"# synthetic\r\nFalloutNV.esm\r\nExtra.esm\r\nMissing.esm\r\n")
        self.dp = datapath.DataPath(self.root, self.game)
        self.hits = []
        self.dp.notify = lambda n, how: self.hits.append(n)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mounts_and_case(self):
        dp = self.dp
        want = os.path.join(self.game, "Data", "FalloutNV.esm")
        self.assertEqual(dp.resolve("sdmc:/switch/fnv/Data/FalloutNV.esm"), want)
        self.assertEqual(dp.resolve("sdmc:/SWITCH/FNV/data/falloutnv.ESM"), want)       # FAT folds case
        self.assertEqual(dp.resolve("sdmc:/fnvnx/game/DATA/FalloutNV.esm"), want)       # guests before os_game_dir
        self.assertEqual(dp.resolve("sdmc:/fnvnx/game/Data/New File.txt"),
                         os.path.join(self.game, "Data", "New File.txt"))               # new files keep their name
        self.assertEqual(dp.resolve("sdmc:/fnvnx/fnvnx.ini"), os.path.join(self.root, "fnvnx", "fnvnx.ini"))
        # the port's own entries of sdmc:/switch/fnv stay on the card, the rest is the install (sdlayout.h)
        sw = os.path.join(self.root, "switch", "fnv")
        self.assertEqual(dp.resolve("sdmc:/switch/fnv/fnvnx.ini"), os.path.join(sw, "fnvnx.ini"))
        self.assertEqual(dp.resolve("sdmc:/switch/fnv/User/Documents"), os.path.join(sw, "user", "Documents"))   # folded
        self.assertEqual(dp.resolve("sdmc:/switch/fnv/guest.nso"), os.path.join(sw, "guest.nso"))
        self.assertEqual(dp.resolve("sdmc:/switch/fnv/drive_c/Windows"), os.path.join(sw, "drive_c", "Windows"))
        self.assertEqual(dp.resolve("sdmc:/switch/fnv/Fallout_default.ini"),
                         os.path.join(self.game, "Fallout_default.ini"))
        self.assertEqual(dp.resolve("sdmc:/../../etc/passwd"), os.path.join(self.root, "etc", "passwd"))
        self.assertEqual(dp.resolve("sdmc:/switch/fnv/../../../x"), os.path.join(self.root, "x"))

    def test_no_game_mount(self):
        card = os.path.join(self.tmp, "emptycard"); os.makedirs(card)
        dp = datapath.DataPath(card)   # both prefixes are <root>/fnvnx/game: old and new guests find one exe
        want = os.path.join(card, "fnvnx", "game", "FalloutNV.exe")
        self.assertEqual(dp.resolve("sdmc:/switch/fnv/FalloutNV.exe"), want)
        self.assertEqual(dp.resolve("sdmc:/fnvnx/game/FalloutNV.exe"), want)

    def test_install_on_the_card(self):
        put(os.path.join(self.root, "switch", "fnv", "Data", "FalloutNV.esm"), b"TES4")   # no --game
        dp = datapath.DataPath(self.root)
        want = os.path.join(self.root, "switch", "fnv", "Data", "FalloutNV.esm")
        self.assertEqual(dp.resolve("sdmc:/switch/fnv/DATA/falloutnv.esm"), want)
        self.assertEqual(dp.resolve("sdmc:/fnvnx/game/Data/FalloutNV.esm"), want)

    def test_targets(self):
        bsas, masters = self.dp.targets()
        self.assertEqual([os.path.basename(b) for b in bsas], ["Synth - One.bsa", "Synth - Two.bsa"])
        self.assertEqual([os.path.basename(m) for m in masters], ["FalloutNV.esm", "Extra.esm"])   # Missing.esm: none

    def test_user_ini_overrides(self):
        put(os.path.join(self.root, "switch", "fnv", "user", datapath.USER_INI), b"[Archive]\nSArchiveList=Synth - Two.bsa\n")
        bsas, _ = datapath.DataPath(self.root, self.game).targets()
        self.assertEqual([os.path.basename(b) for b in bsas], ["Synth - Two.bsa"])

    def test_old_profile_layout(self):
        os.remove(os.path.join(self.root, "switch", "fnv", "user", datapath.PLUGINS))
        put(os.path.join(self.root, "fnvnx", "user", datapath.PLUGINS), b"Extra.esm\r\n")   # guests before sdlayout
        _, masters = datapath.DataPath(self.root, self.game).targets()
        self.assertEqual([os.path.basename(m) for m in masters], ["FalloutNV.esm", "Extra.esm"])

    def test_no_ini_takes_all_archives(self):
        os.remove(os.path.join(self.game, "Fallout_default.ini"))
        bsas, _ = datapath.DataPath(self.root, self.game).targets()
        self.assertEqual(len(bsas), 3)

    def open_read(self, fd, sd, chunks):
        host = self.dp.resolve(sd)
        self.dp.on_open(fd, sd, host)
        with open(host, "rb") as f:
            data = f.read()
        off = 0
        for n in chunks:
            self.dp.on_read(fd, off, data[off:off + n])
            off += n
        self.dp.on_close(fd)

    def test_milestones_in_order(self):
        dp = self.dp
        self.open_read(3, "sdmc:/switch/fnv/fallout_default.ini", [4096])
        self.assertEqual(self.hits, ["ini-read"])
        self.open_read(4, "sdmc:/switch/fnv/Data/synth - one.bsa", [16])
        self.assertEqual(self.hits, ["ini-read", "archive-list"])
        self.open_read(5, "sdmc:/switch/fnv/Data/Synth - Unlisted.bsa", [4])
        self.assertNotIn("bsa-opened", self.hits)
        self.open_read(6, "sdmc:/switch/fnv/Data/Synth - Two.bsa", [16])
        self.assertIn("bsa-opened", self.hits)
        self.open_read(7, "sdmc:/switch/fnv/Data/FalloutNV.esm", [24, 24])
        self.assertIn("esm-header", self.hits)
        self.assertNotIn("masters-loaded", self.hits)                  # read only part of it
        self.open_read(8, "sdmc:/switch/fnv/Data/FalloutNV.esm", [24, 24, 1000])
        self.assertNotIn("masters-loaded", self.hits)                  # Extra.esm not read yet
        self.open_read(9, "sdmc:/switch/fnv/Data/Extra.esm", [100])
        self.assertEqual(self.hits, list(datapath.DATA_MILESTONES))
        c = dp.counts()
        self.assertEqual((c["bsa_opened"], c["bsa_listed"], c["masters_read"], c["masters"]), (2, 2, 2, 2))

    def test_esm_header_needs_offset0_tag(self):
        host = self.dp.resolve("sdmc:/switch/fnv/Data/FalloutNV.esm")
        self.dp.on_open(3, "x", host)
        self.dp.on_read(3, 24, b"TES4")                                # not at offset 0
        self.dp.on_read(3, 0, b"GRUP")                                 # not the tag
        self.assertNotIn("esm-header", self.hits)

    def test_faults(self):
        self.dp.fault("open", "sdmc:/switch/fnv/Data/Nope.bsa", errno.ENOENT)
        self.dp.fault("open", "sdmc:/switch/fnv/Data/Nope.bsa", errno.ENOENT)
        self.dp.fault("read", "x", errno.EIO)
        c = self.dp.counts()
        self.assertEqual((c["file_faults"], c["file_faults_distinct"], c["file_faults_enoent"]), (3, 2, 2))
        self.assertTrue(self.dp.lines()[1].startswith("file-fault\t2\topen\tENOENT\t"))


class SynthInstallTest(unittest.TestCase):
    def test_install(self):
        import synth_install
        tmp = tempfile.mkdtemp(prefix="s5-synth-")
        try:
            exe = os.path.join(tmp, "fake.exe")
            put(exe, b"MZ")
            g = os.path.join(tmp, "game")
            self.assertEqual(synth_install.main([g, exe]), 0)
            self.assertTrue(os.path.isfile(os.path.join(g, "SYNTHETIC")))
            dp = datapath.DataPath(os.path.join(tmp, "sd"), g)
            bsas, masters = dp.targets()
            self.assertEqual([os.path.basename(b) for b in bsas], synth_install.ARCHIVES)
            self.assertEqual([os.path.basename(m) for m in masters], ["FalloutNV.esm"])
            with open(masters[0], "rb") as f:
                rec = f.read()
            self.assertEqual(rec[:4], b"TES4")
            self.assertEqual(int.from_bytes(rec[4:8], "little") + 24, len(rec))   # data size + the 24-byte header
            self.assertEqual(synth_install.main([os.path.join(HERE, "x"), exe]), 1)   # never inside the repository
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class PortNamesTest(unittest.TestCase):
    def test_from_sdlayout(self):
        tmp = tempfile.mkdtemp(prefix="s5-sdl-")
        try:
            h = os.path.join(tmp, "sdlayout.h")
            put(h, b'#define FNVNX_SD_PORT_NAMES "fnv.nro guest.nso user drive_* " \\\n    "Shaders.nvc0cache"\n')
            names, pre = datapath.port_names(h)
            self.assertEqual(names, {"fnv.nro", "guest.nso", "user", "shaders.nvc0cache"})
            self.assertEqual(pre, ("drive_",))
            names, pre = datapath.port_names(os.path.join(tmp, "absent.h"))   # the fallback copy
            self.assertIn("nxgpu.log", names)
            self.assertEqual(pre, ("drive_",))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class MilestoneTableTest(unittest.TestCase):
    def test_table_parses(self):
        import emu_guest
        rows = emu_guest.parse_milestones(os.path.join(HERE, "milestones.txt"))
        data = [t for _, k, t in rows if k == "data"]
        self.assertEqual(data, list(datapath.DATA_MILESTONES))


if __name__ == "__main__":
    unittest.main()
