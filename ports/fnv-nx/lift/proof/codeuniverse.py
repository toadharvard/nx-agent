"""codeuniverse: the code rule of gate forms. For a lifted product root, the shape keys of the code the pack lifts equal the pack's shapes.tsv, and the
list of lifted instructions the keys are computed from is the code the pack emits.
Inputs of one pack (a content-pinned external of the root's recipe) and of the images (content-pinned externals of formgate_inputs.json, by sha256):
    instrs.tsv   `image<TAB>va<TAB>len<TAB>hex`, one row per instruction the lifter emitted a body for (lift/tools/lifted.py `lifted()`: an instruction inside
                 several entries counts once per entry), sorted by (image, va). image = a declared image name; va 8 lower-case hex digits.
    shapes.tsv   `key<TAB>family<TAB>count`, sorted by key (the census of the lifter's own run)
    u/*.cpp      the emitted C++. MARKER: the lifter writes the line `L_<va 8 hex>: ;` for every instruction of a body, and only for them
                 (fnvlift/core.py `em('%s: ;' % cx.lab(a))`, the loop `for idx, a in enumerate(order)`); the label of bytes that do not decode
                 (core.py `for a in sorted(bad)`) is on a line that goes on with SYNC_OUT, so the whole line `L_xxxxxxxx: ;` is exactly an instruction.
Checks, each finding names its image and va (a key finding names the first site of the key):
    code-image        a row names an image that is not declared; or two declared images overlap (a va would name two images)
    code-bytes        the row's hex differs from the image's bytes at va (the image as the loader maps it at its declared base)
    code-decode       the bytes at va are not exactly one instruction of the row's length, or proof/shape.py refuses a key for it
    code-key-missing  a key of the instructions that shapes.tsv lacks;   code-key-extra  a key of shapes.tsv that no instruction has
    code-count        the same key with another count or family
    code-marker-missing / code-marker-extra / code-marker-count   the address set (with multiplicity) of the rows differs from the markers of u/*.cpp
The key of every row is recomputed from the image's bytes by proof/shape.py shape_key / family (the census's own key function); nothing is read
from liftgen's key output except shapes.tsv itself, which is the thing judged. No sampling, no tolerance, no heuristic over the text."""
import collections
import glob
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from proof import shape  # noqa: E402

FAMILIES = shape.FAMILIES
MARK = re.compile(rb"^L_([0-9a-f]{8}): ;$", re.M)
NAME = re.compile(r"[A-Za-z0-9._-]+$")


class Bad(Exception):
    """a pack file that is not well formed: the gate is red with this reason"""


def read_rows(path, ncol):
    out = []
    with open(path) as fh:
        for n, ln in enumerate(fh, 1):
            ln = ln.rstrip("\n")
            if not ln or ln[0] == "#":
                continue
            r = ln.split("\t")
            if len(r) != ncol:
                raise Bad("%s:%d: %d columns, need %d" % (os.path.basename(path), n, len(r), ncol))
            out.append(r)
    return out


def load_instrs(path):
    """-> [(image, va, len, bytes)] in file order; Bad unless sorted by (image, va) and every field well formed"""
    out, last = [], None
    for r in read_rows(path, 4):
        if not (NAME.match(r[0]) and re.fullmatch("[0-9a-f]{8}", r[1]) and r[2].isdigit() and 1 <= int(r[2]) <= 15 and re.fullmatch("([0-9a-f]{2})+", r[3])
                and len(r[3]) == 2 * int(r[2])):
            raise Bad("instrs.tsv: bad row %r" % "\t".join(r)[:100])
        k = (r[0], int(r[1], 16))
        if last is not None and k < last:
            raise Bad("instrs.tsv: not sorted by (image, va) at %s %s" % (r[0], r[1]))
        last = k
        out.append((r[0], int(r[1], 16), int(r[2]), bytes.fromhex(r[3])))
    return out


def load_shapes(path):
    """-> {key: (family, count)}; Bad unless rows are `key family count`, sorted, unique"""
    out, last = {}, None
    for r in read_rows(path, 3):
        if r[1] not in FAMILIES or not r[2].isdigit():
            raise Bad("shapes.tsv: bad row %r" % "\t".join(r)[:100])
        if last is not None and r[0] <= last:
            raise Bad("shapes.tsv: not sorted by key, or a key twice, at %r" % r[0][:100])
        last = r[0]
        out[r[0]] = (r[1], int(r[2]))
    return out


def markers(units):
    """-> Counter {va: number of marker lines} over the files of UNITS (u/*.cpp)"""
    c = collections.Counter()
    for p in units:
        with open(p, "rb") as fh:
            for m in MARK.findall(fh.read()):
                c[int(m, 16)] += 1
    return c


def owner(images, va):
    for name, img in images.items():
        for _, a, vs, _, _ in img.secs:
            if a <= va < a + vs:
                return name
    return None


def overlaps(images):
    """[(name, name)] of declared images whose sections share an address"""
    spans = [(a, a + vs, n) for n, img in sorted(images.items()) for _, a, vs, _, _ in img.secs]
    spans.sort()
    return [(spans[i][2], spans[i + 1][2]) for i in range(len(spans) - 1) if spans[i + 1][0] < spans[i][1] and spans[i][2] != spans[i + 1][2]]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def judge(instrs, shapes, mark, images):
    """-> [{"code", "image", "va", "detail"}] sorted; INSTRS from load_instrs, SHAPES from load_shapes, MARK a Counter of marker addresses, IMAGES {name: image with
    .bytes(va, n) and .secs} (fnvlift.Image as the loader maps it). Empty = the code rule holds."""
    out = []

    def f(code, image, va, detail):
        out.append({"code": code, "image": image, "va": "%08x" % va if va is not None else "", "detail": detail})
    for a, b in overlaps(images):
        f("code-image", a, None, "images %s and %s overlap: a va would name two images" % (a, b))
    cnt, fam, site, perva = collections.Counter(), {}, {}, collections.Counter()
    md = shape.decoder()
    for name, va, ln, raw in instrs:
        img = images.get(name)
        if img is None:
            f("code-image", name, va, "image %s is not declared" % name)
            continue
        got = img.bytes(va, ln)
        if got != raw:
            f("code-bytes", name, va, "row %s, image %s" % (raw.hex(), got.hex()))
            continue
        perva[va] += 1
        ins = list(md.disasm(got, va, 2))
        if len(ins) != 1 or ins[0].size != ln:
            f("code-decode", name, va, "bytes %s are not one instruction of length %d" % (got.hex(), ln))
            continue
        try:
            k, fm = shape.shape_key(ins[0]), shape.family(ins[0])
        except shape.ShapeError as e:
            f("code-decode", name, va, "no shape key: %s" % e)
            continue
        cnt[k] += 1
        fam[k] = fm
        site.setdefault(k, (name, va))
    for k in sorted(set(cnt) - set(shapes)):
        f("code-key-missing", site[k][0], site[k][1], "key %s (%d instructions) is not in shapes.tsv" % (k, cnt[k]))
    for k in sorted(set(shapes) - set(cnt)):
        f("code-key-extra", "", None, "key %s is in shapes.tsv and no instruction has it" % k)
    for k in sorted(set(cnt) & set(shapes)):
        if shapes[k] != (fam[k], cnt[k]):
            f("code-count", site[k][0], site[k][1], "key %s: instructions give %s %d, shapes.tsv %s %d" % (k, fam[k], cnt[k], shapes[k][0], shapes[k][1]))
    for va in sorted(set(perva) | set(mark)):
        if perva[va] and not mark[va]:
            f("code-marker-missing", owner(images, va) or "", va, "an instruction of instrs.tsv has no marker in u/*.cpp")
        elif mark[va] and not perva[va]:
            f("code-marker-extra", owner(images, va) or "", va, "u/*.cpp has %d marker(s) for an address that instrs.tsv lacks" % mark[va])
        elif perva[va] != mark[va]:
            f("code-marker-count", owner(images, va) or "", va, "instrs.tsv has %d rows, u/*.cpp %d markers" % (perva[va], mark[va]))
    out.sort(key=lambda d: (d["code"], d["image"], d["va"], d["detail"]))
    return out


def judge_pack(pack, images):
    """the findings of the pack directory PACK (instrs.tsv, shapes.tsv, u/*.cpp) against IMAGES; Bad for a malformed file"""
    units = sorted(glob.glob(os.path.join(pack, "u", "*.cpp")))
    if not units:
        raise Bad("the pack has no u/*.cpp")
    return judge(load_instrs(os.path.join(pack, "instrs.tsv")), load_shapes(os.path.join(pack, "shapes.tsv")), markers(units), images)
