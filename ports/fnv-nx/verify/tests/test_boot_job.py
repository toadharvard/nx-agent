#!/usr/bin/env python3
"""Host test of farm/boot_job.sh: a local bare git repository stands in for the artifacts repository (branches
l1-contract/elf/<id>), a scratch directory for HOME and a directory with a dummy FalloutNV.exe for the game. The guests
are the tiny ELFs of test_bootmachine.build (MODE 6: a clean exit; MODE 3: a fault), with the code section renamed to
.text.fnvl as the job's icount spec expects. No network, no game data, no package installs. Plain script: exit 0 on pass.
Scratch: tempfile.mkdtemp, removed at exit."""
import atexit
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
NX = os.path.dirname(VERIFY)
sys.path.insert(0, HERE)
sys.path.insert(0, VERIFY)
import boot  # noqa: E402
import test_bootmachine as tb  # noqa: E402
import test_bootgpu as tg  # noqa: E402

JOB = os.path.join(NX, "farm", "boot_job.sh")
SCRATCH = tempfile.mkdtemp(prefix="bootjob-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
LINE = "fnvnx-start: identity ok"
TID = "ef" * 32
# the contract's own formats (see verify/bootinputs.py): milestones.tsv has the exe_entry row only (the contract's row grammar); the loader console lines
# come from run.txt
MILESTONES = "exe_entry\texe_entry\t00003000\texe PE ImageBase+AddressOfEntryPoint\n# table_id %s\n" % TID
RUN = "emulator run: header\n   %s\n   [fnvnx] not a loader line\n" % LINE
MANIFEST = "00001000\tfnvboot_a\tcode\n00002000\tfnvboot_b\tcode\n00003000\tfnvboot_c\tcode\n000ff000\tI_rt\timport\n# table_id %s\n" % TID
MODULES = ("# name\tsha256\tsize\tpreferred_base\tassigned_base\tsize_of_image\thas_tls\tentry_rva\tstatic\tload_index\n"
           "m.dll\t%s\t16\t10000000\t01410000\t00001000\t0\t00000010\t0\t\n" % ("a" * 64))
KEYS_TSV = "# table_id %s\n000ff000\tm.dll\tf\t1\tstdcall\ti\ti\tI_rt\n" % TID
FINAL = re.compile(r"^BOOT elf=(\S+) rc=(\d+) reason=(\S+) last=(\S+) instr=(\d+) main=(\d+) threads=(\d+) seconds=(\d+) "
                   r"published=(\S+)@([0-9a-f]{12})( presents=\d+)? stage_table=(\S+)$")
HOME = os.path.join(SCRATCH, "home")
BARE = os.path.join(SCRATCH, "artifacts.git")
GAME = os.path.join(SCRATCH, "game")
def _exe():
    """the player's FalloutNV.exe: the pinned gamedata/exe input of verify/tests/inputs.json (the gate fetches it by commit + tree hash)"""
    with open(os.path.join(os.environ["FNVNX_INPUT_EXE"], "FalloutNV.exe"), "rb") as f:
        return f.read()
PINS = os.path.join(SCRATCH, "boot_inputs.json")   # the declared-inputs pin file of the job: a fixture set (not the real fonts) in the test's bare repository
FONT_FILES = {"fonts/A.ttf": b"alpha", "fonts/Sub/B.ttf": b"bravo"}


EXE = _exe()


def git(*args, cwd=None, check=True):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@invalid", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout


def lifted_elf(mode):
    """the guest image of MODE as the recipe builds it: the three fnvboot_* functions stand for lifted code and sit in the input section
    .text.fnvl by their own source (tests/bootguest/guest.c); the crt0 header words are written by the recipe's ldcheck stage"""
    return tb.build(mode)[0]


def publish_branch(name, files):
    work = os.path.join(SCRATCH, "w-" + name.replace("/", "-"))
    os.makedirs(work)
    git("init", "-q", work)
    git("checkout", "-q", "--orphan", name, cwd=work)
    for fn, data in files.items():
        os.makedirs(os.path.dirname(os.path.join(work, fn)), exist_ok=True)
        with open(os.path.join(work, fn), "wb") as f:
            f.write(data)
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "contract", cwd=work)
    git("push", "-q", BARE, "%s:refs/heads/%s" % (name, name), cwd=work)


def run_job(args, env_extra=None, game=GAME):
    env = {"PATH": os.environ["PATH"], "HOME": HOME, "ARTIFACTS_URL": BARE, "LANG": "C.UTF-8", "BOOT_INPUTS": PINS}
    if game:
        env["GAME_DIR"] = game
    env.update(env_extra or {})
    r = subprocess.run(["bash", JOB, *args], env=env, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def published(branch):
    """{file: bytes} of the published branch, its commit message and its commit id"""
    cm = git("rev-parse", "refs/heads/" + branch, cwd=BARE).strip()
    names = git("ls-tree", "-r", "--name-only", cm, cwd=BARE).split()
    files = {n: subprocess.run(["git", "show", "%s:%s" % (cm, n)], cwd=BARE, capture_output=True, check=True).stdout for n in names}
    return files, git("log", "-1", "--format=%B", cm, cwd=BARE), cm


def main():
    t0 = time.time()
    os.makedirs(HOME)
    os.makedirs(GAME)
    with open(os.path.join(GAME, "FalloutNV.exe"), "wb") as f:
        f.write(EXE)
    git("init", "-q", "--bare", BARE)
    git("config", "uploadpack.allowFilter", "true", cwd=BARE)
    contract = {"entries.tsv": MANIFEST.encode(), "modules.tsv": MODULES.encode(), "keys.tsv": KEYS_TSV.encode(),
                "milestones.tsv": MILESTONES.encode(), "run.txt": RUN.encode()}
    ok_elf, fault_elf = lifted_elf(6), lifted_elf(3)
    publish_branch("l1-contract/elf/testid", dict(contract, **{"guest.elf": ok_elf}))
    publish_branch("l1-contract/elf/faultid", dict(contract, **{"guest.elf": fault_elf}))
    publish_branch("l1-contract/elf/softid", dict(contract, **{"guest.elf": ok_elf, "softfloat.tsv": b"fnvboot_b\n"}))
    # the guest in three parts + manifest.json (joined and verified by the job); a corrupt part must fail the job
    cut = [0, len(ok_elf) // 3, 2 * len(ok_elf) // 3, len(ok_elf)]
    pieces = {"part.%03d" % i: ok_elf[cut[i]:cut[i + 1]] for i in range(3)}
    man = {"schema": "fnvnx-elf/1", "name": "guest.elf", "size": len(ok_elf), "sha256": hashlib.sha256(ok_elf).hexdigest(),
           "parts": [[n, len(b), hashlib.sha256(b).hexdigest()] for n, b in pieces.items()]}
    publish_branch("l1-contract/elf/partsid", dict(contract, **pieces, **{"manifest.json": json.dumps(man).encode()}))
    bad = dict(pieces, **{"part.001": b"X" + pieces["part.001"][1:]})
    publish_branch("l1-contract/elf/badpartid", dict(contract, **bad, **{"manifest.json": json.dumps(man).encode()}))
    publish_branch("l1-contract/elf/badrowid", dict(contract, **{"guest.elf": ok_elf, "keys.tsv": KEYS_TSV.encode() + b"junk\n"}))
    noent = {k: v for k, v in contract.items() if k != "entries.tsv"}
    publish_branch("l1-contract/elf/noentries", dict(noent, **{"guest.elf": ok_elf}))
    # the REAL renderer files: the pinned nxgl4 and bridge branch commits of the artifacts repository (test_bootgpu.fetch_renderer), published
    # unchanged into the test's bare repository under their real branch names; the broken-chain cases below are mutations of these inputs
    gdir, bdir = tg.fetch_renderer(SCRATCH)
    rd = lambda d, n: open(os.path.join(d, n), "rb").read()  # noqa: E731
    lib, meta_j, ar, build_j = rd(gdir, "libnxgl4.so"), rd(gdir, "meta.json"), rd(bdir, "libfnvnx-d3d9.a"), rd(bdir, "build.json")
    GB, BB = tg.NXGL4["ref"], tg.BRIDGE["ref"]
    gpu_elf = lifted_elf(80)
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    jd = lambda d: json.dumps(d).encode()  # noqa: E731
    gman = lambda render: jd(dict({"schema": "fnvnx-elf/1", "name": "guest.elf", "size": len(gpu_elf), "sha256": sha(gpu_elf), "parts": []},
                                  **({"render": render} if render is not None else {})))
    render = {"branch": BB, "archive_sha256": json.loads(build_j)["archive_sha256"]}
    publish_branch("l1-contract/elf/gpuid", dict(contract, **{"guest.elf": gpu_elf, "manifest.json": gman(render)}))
    publish_branch("l1-contract/elf/gpunoman", dict(contract, **{"guest.elf": gpu_elf}))
    publish_branch("l1-contract/elf/gpunorender", dict(contract, **{"guest.elf": gpu_elf, "manifest.json": gman(None)}))
    publish_branch("l1-contract/elf/gpuothersha", dict(contract, **{"guest.elf": gpu_elf, "manifest.json": gman(dict(render, archive_sha256="0" * 64))}))
    publish_branch("l1-contract/elf/gpuoldbridge", dict(contract, **{"guest.elf": gpu_elf, "manifest.json": gman(dict(render, branch="bridge-h2"))}))
    publish_branch("l1-contract/elf/gpunocommit", dict(contract, **{"guest.elf": gpu_elf, "manifest.json": gman(dict(render, branch="bridge-nocommit"))}))
    publish_branch(BB, {"build.json": build_j, "libfnvnx-d3d9.a": ar})
    bj = json.loads(build_j)
    publish_branch("bridge-h2", {"build.json": jd(dict(bj, nx_agent_commit="2" * 40)), "libfnvnx-d3d9.a": ar})
    publish_branch("bridge-nocommit", {"build.json": jd({k: v for k, v in bj.items() if k != "nx_agent_commit"}), "libfnvnx-d3d9.a": ar})
    publish_branch(GB, {"libnxgl4.so": lib, "meta.json": meta_j})
    publish_branch("gpu-nometa", {"libnxgl4.so": lib})
    publish_branch("gpu-othersha", {"libnxgl4.so": lib, "meta.json": jd(dict(json.loads(meta_j), sha256="0" * 64))})
    publish_branch("gpu-empty", {"README": b"no library here\n"})
    publish_branch("gpu-bad", {"libnxgl4.so": b"not an elf\n", "meta.json": jd(dict(json.loads(meta_j), sha256=sha(b"not an elf\n")))})
    # the declared inputs: a fixture font set + its sums file at the root of a branch; the pin file names that commit
    fsums = "".join("%s  %s\n" % (hashlib.sha256(v).hexdigest(), k) for k, v in sorted(FONT_FILES.items())).encode()
    publish_branch("test/fonts", dict(FONT_FILES, SHA256SUMS=fsums))
    fcommit = git("rev-parse", "refs/heads/test/fonts", cwd=BARE).strip()

    def write_pins(path, sums_sha, commit=fcommit):
        open(path, "w").write(json.dumps({"format": "fnvnx-boot-inputs/1", "inputs": [
            {"name": "fonts", "repo": "fnv-nx-build", "ref": "test/fonts", "commit": commit, "path": "fonts", "sums": "SHA256SUMS",
             "sums_sha256": sums_sha, "mount": "switch/fnv/fonts"}]}))
    write_pins(PINS, hashlib.sha256(fsums).hexdigest())
    tb.case("setup: contract branches built", True)

    rc, out, err = run_job(["testid"])
    last = out.strip().splitlines()[-1] if out.strip() else ""
    m = FINAL.match(last)
    tb.case("clean guest: exit 0, final line has every field", rc == 0 and m is not None and out.count("\n") == 1, (rc, out, err[-600:]))
    f = m.groups() if m else ("",) * 10
    tb.case("final line values", f[0] == "testid" and f[1] == "0" and f[2] == "exit" and f[3] == "exe-entry" and int(f[4]) > 0
            and int(f[5]) > 0 and f[6] == "1" and f[8] == "l4-verify/boot/testid", f)
    files, msg, cm = published("l4-verify/boot/testid")
    tb.case("published: exactly the four files", sorted(files) == ["OUT.console.txt", "OUT.json", "meta.json", "spec.json"], sorted(files))
    tb.case("published commit id is the one reported", cm[:12] == f[9], (cm, f[9]))
    meta = json.loads(files["meta.json"]) if "meta.json" in files else {}
    elf_commit = git("rev-parse", "refs/heads/l1-contract/elf/testid", cwd=BARE).strip()
    nx_commit = git("rev-parse", "HEAD", cwd=NX).strip()
    tb.case("meta.json keys and values", sorted(meta) == sorted(["elf_id", "elf_branch_commit", "nx_agent_commit", "exe_sha256", "boot_rc",
            "seconds", "limit_instructions", "profile", "host_cpu_model", "softfloat", "data_items"])
            and meta["elf_id"] == "testid" and meta["elf_branch_commit"] == elf_commit and meta["nx_agent_commit"] == nx_commit
            and meta["exe_sha256"] == hashlib.sha256(EXE).hexdigest() and meta["boot_rc"] == 0 and meta["limit_instructions"] is None
            and meta["profile"] == "handheld" and meta["host_cpu_model"] and meta["softfloat"] == "absent", meta)
    tb.case("commit message is one line without a trailer", len(msg.rstrip("\n").splitlines()) == 1 and "Co-Authored-By" not in msg, msg)
    blob = b"\n".join(files.values()).decode(errors="replace") + msg
    tb.case("no session text anywhere", "session" not in blob.lower() and "claude.ai" not in blob.lower(), "")
    doc = json.loads(files["OUT.json"]) if "OUT.json" in files else {}
    spec = json.loads(files["spec.json"]) if "spec.json" in files else {}
    tb.case("OUT.json is the boot result, spec.json has the lifted range and no softfloat class",
            doc.get("last_reached") == "exe-entry" and doc.get("manifest_id") == TID and "lifted" in spec.get("ranges", {}) and "softfloat" not in spec.get("entries", {}), (doc.get("last_reached"), spec))
    tb.case("console file holds the guest's line", LINE in files.get("OUT.console.txt", b"").decode(), files.get("OUT.console.txt"))
    tb.case("milestones: loader console line of run.txt reached in order, then exe-entry",
            [m["name"] for m in doc.get("milestones", [])] == ["start-01", "exe-entry"] and all(m["reached"] for m in doc["milestones"]), doc.get("milestones"))
    tb.case("OUT.json inputs.declared records {name, commit, sums_sha256, files}", doc.get("inputs", {}).get("declared") ==
            [{"name": "fonts", "commit": fcommit, "sums_sha256": hashlib.sha256(fsums).hexdigest(), "files": 2}], doc.get("inputs"))
    n_pub = git("rev-parse", "refs/heads/l4-verify/boot/testid", cwd=BARE).strip()
    for what, pinfile in (("a wrong sums hash", ("w1", hashlib.sha256(fsums).hexdigest()[::-1], fcommit)),
                          ("a commit the repository does not have", ("w2", hashlib.sha256(fsums).hexdigest(), "1" * 40))):
        bp = os.path.join(SCRATCH, pinfile[0] + ".json")
        write_pins(bp, pinfile[1], pinfile[2])
        rc3, out3, err3 = run_job(["testid"], {"BOOT_INPUTS": bp})
        tb.case("declared input with %s: the job ends as input-error before the boot, nothing published" % what, rc3 == 1 and "input-error" in err3
                and out3 == "" and git("rev-parse", "refs/heads/l4-verify/boot/testid", cwd=BARE).strip() == n_pub, (rc3, out3, err3[-300:]))
    rc2, out2, err2 = run_job(["testid"])
    files2 = published("l4-verify/boot/testid")[0]
    tb.case("second run republishes byte-equal OUT.json and console", rc2 == 0 and files2.get("OUT.json") == files["OUT.json"]
            and files2.get("OUT.console.txt") == files["OUT.console.txt"], (rc2, err2[-400:]))

    rc, out, err = run_job(["testid", "--limit-instructions", "40", "--profile", "docked"])
    m = FINAL.match(out.strip().splitlines()[-1]) if out.strip() else None
    meta = json.loads(published("l4-verify/boot/testid")[0]["meta.json"])
    tb.case("limit and profile: reason limit, recorded in meta.json", rc == 0 and m and m.group(3) == "limit"
            and meta["limit_instructions"] == 40 and meta["profile"] == "docked", (rc, out, err[-400:], meta))

    rc, out, err = run_job(["faultid"])
    m = FINAL.match(out.strip().splitlines()[-1]) if out.strip() else None
    tb.case("faulting guest: boot rc 2 is a published result, job exit 0", rc == 0 and m and m.group(2) == "2" and m.group(3) == "fault"
            and m.group(9) == "l4-verify/boot/faultid" and json.loads(published("l4-verify/boot/faultid")[0]["meta.json"])["boot_rc"] == 2,
            (rc, out, err[-400:]))

    rc, out, err = run_job(["softid"])
    meta = json.loads(published("l4-verify/boot/softid")[0]["meta.json"]) if rc == 0 else {}
    spec = json.loads(published("l4-verify/boot/softid")[0]["spec.json"]) if rc == 0 else {}
    tb.case("softfloat.tsv present: class in the spec, recorded", rc == 0 and meta.get("softfloat") == "present"
            and len(spec.get("entries", {}).get("softfloat", [])) == 1, (rc, err[-400:], spec))

    rc, out, err = run_job(["partsid"])
    f2 = published("l4-verify/boot/partsid")[0] if rc == 0 else {}
    tb.case("guest in parts: joined, same result as the single file", rc == 0 and f2.get("OUT.json") == files["OUT.json"].replace(
        b'"manifest_id"', b'"manifest_id"') and f2.get("OUT.console.txt") == files["OUT.console.txt"], (rc, err[-400:]))
    rc, out, err = run_job(["badpartid"])
    tb.case("corrupt part: job fails naming the part, nothing published", rc == 1 and "part.001" in err
            and subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/heads/l4-verify/boot/badpartid"], cwd=BARE).returncode != 0, (rc, err[-400:]))
    rc, out, err = run_job(["badrowid"])
    tb.case("a keys.tsv row that is not understood fails the job", rc == 1 and "keys.tsv" in err, (rc, err[-400:]))
    rc, out, err = run_job(["noentries"])
    tb.case("missing entries.tsv: job fails naming the file, nothing published", rc == 1 and "entries.tsv" in err and "lacks" in err
            and subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/heads/l4-verify/boot/noentries"], cwd=BARE).returncode != 0, (rc, err[-400:]))
    tb.case("no stage branch: final line says stage_table=absent", m is not None and m.group(12) == "absent", last)
    publish_branch("test-stage", {"stage.milestones": b"late any call 0x2000\n"})
    publish_branch("test-stage-bad", {"stage.milestones": b"late any call 0x7000\n"})
    publish_branch("test-stage-nofile", {"README": b"x\n"})
    rc, out, err = run_job(["testid"], {"BOOT_STAGE_TABLE_BRANCH": "test-stage"})
    m = FINAL.match(out.strip().splitlines()[-1]) if out.strip() else None
    doc = json.loads(published("l4-verify/boot/testid")[0]["OUT.json"]) if rc == 0 else {}
    tb.case("stage branch: rows appended after the contract's, final line names branch@commit",
            rc == 0 and m and m.group(12) == "test-stage@" + git("rev-parse", "refs/heads/test-stage", cwd=BARE).strip()[:12]
            and [x["name"] for x in doc["milestones"]] == ["start-01", "exe-entry", "late"], (rc, out, err[-400:], doc.get("milestones")))
    rc, out, err = run_job(["testid"], {"BOOT_STAGE_TABLE_BRANCH": "test-stage-bad"})
    tb.case("stage row on an address without a lifted entry: job fails naming the stage table", rc == 1 and "stage table" in err, (rc, err[-300:]))
    rc, out, err = run_job(["testid"], {"BOOT_STAGE_TABLE_BRANCH": "test-stage-nofile"})
    tb.case("stage branch without stage.milestones: job fails naming it", rc == 1 and "stage.milestones" in err, (rc, err[-300:]))
    rc, out, err = run_job(["nosuchid"])
    tb.case("missing branch: job fails naming it", rc == 1 and "l1-contract/elf/nosuchid" in err, (rc, err[-400:]))
    empty = os.path.join(SCRATCH, "nogame")
    os.makedirs(empty)
    rc, out, err = run_job(["testid"], game=empty)
    tb.case("game without FalloutNV.exe: job fails naming it", rc == 1 and "FalloutNV.exe" in err, (rc, err[-400:]))
    rc, out, err = run_job(["testid", "--profile", "tv"])
    tb.case("bad profile: job fails", rc == 1 and "--profile" in err, (rc, err[-400:]))
    rc, out, err = run_job(["testid", "--limit-instructions", "0"])
    tb.case("bad limit: job fails", rc == 1 and "--limit-instructions" in err, (rc, err[-400:]))
    # --- the renderer: --gpu-branch
    genv = {}   # the host packages come from the declared environment (verify/env.json); the job requires them and installs nothing
    rc, out, err = run_job(["gpuid", "--gpu-branch", GB], genv)
    last = out.strip().splitlines()[-1] if out.strip() else ""
    m = FINAL.match(last)
    tb.case("--gpu-branch: exit 0, final line carries presents=4", rc == 0 and m is not None and m.group(11) == " presents=4", (rc, out, err[-600:]))
    if rc == 0:
        files, msg, cm = published("l4-verify/boot/gpuid")
        fr = sorted(n for n in files if n.startswith("frames/"))
        tb.case("published: OUT.json, console, spec, meta and the first 3 frames (.rgba + .txt) only",
                sorted(n for n in files if "/" not in n) == ["OUT.console.txt", "OUT.json", "meta.json", "spec.json"]
                and fr == ["frames/frame_%06u.%s" % (i, e) for i in range(3) for e in ("rgba", "txt")], sorted(files))
        tb.case("frame files hold the library's output", files["frames/frame_000001.txt"] == b"1280 720\n"
                and len(files["frames/frame_000001.rgba"]) == 1280 * 720 * 4, files.get("frames/frame_000001.txt"))
        doc, meta = json.loads(files["OUT.json"]), json.loads(files["meta.json"])
        tb.case("OUT.json gpu record, meta.json gpu fields", doc["gpu"]["presents"] == 4 and doc["gpu"]["first_module10_call"]
                and meta["gpu_branch"] == GB
                and meta["gpu_branch_commit"] == git("rev-parse", "refs/heads/" + GB, cwd=BARE).strip(), (doc.get("gpu"), meta))
        tb.case("meta.json records the renderer pair", meta.get("bridge_branch") == BB and meta.get("render_head") == tg.NX_AGENT_COMMIT
                and meta.get("bridge_branch_commit") == git("rev-parse", "refs/heads/" + BB, cwd=BARE).strip()
                and "data_items" in meta and meta["data_items"] is None, meta)
    # the renderer pair rule: every broken link of the chain ELF -> bridge -> nxgl4 fails the job before the run
    for eid, gb, word, what in (("gpunoman", GB, "manifest.json", "ELF branch without manifest.json"),
                                ("gpunorender", GB, "render", "manifest.json without the render object"),
                                ("gpuothersha", GB, "bridge archive", "ELF linked with another archive than the bridge branch holds"),
                                ("gpuoldbridge", GB, "!= nxgl4 head", "bridge head != nxgl4 head"),
                                ("gpunocommit", GB, "nx_agent_commit", "bridge build.json without nx_agent_commit"),
                                ("gpuid", "gpu-nometa", "meta.json", "nxgl4 branch without meta.json"),
                                ("gpuid", "gpu-othersha", "libnxgl4.so", "libnxgl4.so bytes differ from meta.json")):
        rc, out, err = run_job([eid, "--gpu-branch", gb], genv)
        tb.case("pair rule: %s: job fails naming it, nothing published" % what, rc == 1 and word in err and out == ""
                and (eid == "gpuid" or not git("ls-remote", BARE, "refs/heads/l4-verify/boot/" + eid)), (rc, err[-400:]))
    rc, out, err = run_job(["gpuid", "--gpu-branch", "no-such-gpu"], genv)
    tb.case("missing renderer branch: job fails", rc == 1 and "no-such-gpu" in err, (rc, err[-300:]))
    rc, out, err = run_job(["gpuid", "--gpu-branch", "gpu-empty"], genv)
    tb.case("renderer branch without libnxgl4.so: job fails naming it", rc == 1 and "libnxgl4.so" in err, (rc, err[-300:]))
    rc, out, err = run_job(["gpuid", "--gpu-branch", "gpu-bad"], genv)
    tb.case("library that does not load: job fails", rc == 1 and "does not load" in err, (rc, err[-300:]))
    rc, out, err = run_job(["testid", "--gpu-branch", "-bad"], genv)
    tb.case("bad --gpu-branch value: job fails", rc == 1 and "--gpu-branch" in err, (rc, err[-300:]))
    # nothing is installed: a host that does not match the declared environment is an input error before anything is fetched or published
    bad = os.path.join(SCRATCH, "badenv")
    os.makedirs(bad)
    for tag, edit in (("a pinned program missing", lambda d: d["programs"].append("fnvnx-no-such-program")),
                      ("a pinned python module missing", lambda d: d["python"]["modules"].__setitem__("fnvnx-no-such-module", "1.0"))):
        doc = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "env.json")))
        edit(doc)
        ef = os.path.join(bad, tag.replace(" ", "_") + ".json")
        json.dump(doc, open(ef, "w"), sort_keys=True)
        rc, out, err = run_job(["envbad"], {"BOOT_ENV": ef})
        tb.case("%s: input error rc 1 naming setup_verdict_env.sh, nothing published" % tag,
                rc == 1 and "input-error" in err and "setup_verdict_env.sh" in err and "envbad" not in git("ls-remote", BARE, check=False), (rc, err[-400:]))
    src = open(JOB).read()
    tb.case("job text installs nothing", not any(w in src for w in ("apt-get install", "pip install", "sudo ")), None)
    print("%d cases, %.1f s" % (tb.N[0], time.time() - t0))
    return 1 if tb.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
