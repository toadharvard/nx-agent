"""verify/cpuref.json (the pinned host CPU of the `run` stage) and rt/fnvl_abi.h (FNVL_CPUREF_*, FNVL_CPUID1_EAX) state one identity: the three numbers agree, and leaf 1 EAX decodes to them
by the SDM Vol 2A CPUID leaf 1 formula. rt/cpumodel.c static_asserts the same decode in the recipe build. microcode null = the reference host exposes no microcode line (a virtual machine)."""
import json, re, sys, unittest
from pathlib import Path
R = Path(__file__).resolve().parents[3]

def macro(t, n):
    m = re.search(r"#define\s+%s\s+(0x[0-9a-fA-F]+|\d+)u?\b" % n, t)
    return int(m.group(1), 0)

class T(unittest.TestCase):
    def test_identity(self):
        pin = json.loads((R / "verify/cpuref.json").read_text())
        self.assertEqual(set(pin), {"vendor_id", "family", "model", "stepping", "microcode", "model_name"})
        import sys, tempfile
        sys.path.insert(0, str(R / "lift"))
        import subprocess
        gd = tempfile.mkdtemp(prefix="cpuref_")
        subprocess.run([sys.executable, "-I", str(R / "lift/tools/gen_cpuref.py"), gd], check=True)      # the rt root's own generator stage
        t = (Path(gd) / "fnvl_cpuref.h").read_text()
        eax = macro(t, "FNVL_CPUID1_EAX")
        fam = (eax >> 8 & 15) + ((eax >> 20 & 255) if (eax >> 8 & 15) == 15 else 0)
        mod = (eax >> 4 & 15) | ((eax >> 16 & 15) << 4 if (eax >> 8 & 15) in (6, 15) else 0)
        self.assertEqual((fam, mod, eax & 15), (pin["family"], pin["model"], pin["stepping"]))
        self.assertEqual((macro(t, "FNVL_CPUREF_FAMILY"), macro(t, "FNVL_CPUREF_MODEL"), macro(t, "FNVL_CPUREF_STEPPING")), (fam, mod, eax & 15))
        self.assertIsNone(pin["microcode"])

if __name__ == "__main__":
    unittest.main()
