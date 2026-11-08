"""Measurement-only checks for emu_guest.py discovery boots (mixed into Emu). Neither changes what the guest does.

--alloc-check   allocator sanitizer. Code hooks at the entry of every allocator and free function (ALLOC_TABLE) and,
                for allocations, at the return addresses of the calls. A block is recorded at its outermost
                allocation (a family's entry may call another family, operator new -> the game MemoryManager -> libc
                malloc: the per-thread nesting depth keeps the inner calls from overwriting the record). A free (or
                the old block of a realloc) is checked at the outermost entry: known? allocated by the same family?
                already freed? inside the guest heap at all? Rows:
    alloc-check<TAB>kind<TAB>freeing family:function<TAB>allocating family:function<TAB>ptr<TAB>free stack<TAB>alloc stack
    kind: mismatch | mismatch-game (the pair is one of the game's own heaps, GAME_HEAPS, that the original mixes by
          design: operator new/delete, MemoryManager, NiMalloc) | double-free | unknown-block | non-heap
    alloc-check-summary<TAB>count<TAB>kind<TAB>free fn<TAB>alloc fn<TAB>first ptr<TAB>first alloc frame
    alloc-check-stats<TAB>allocs=..<TAB>frees=..<TAB>reallocs=..<TAB>live=..<TAB>freed=..

--image-check   in the invalid-memory handler: the fault address against the exe image (base to the end of the last
                section, from the PE headers). Rows:
    image-check<TAB>kind<TAB>addr<TAB>section<TAB>pc symbol<TAB>lr symbol[<TAB>native=SYMBOL]
    kind: code-read | code-write   an exe .text address used as data (.text is not mapped as data)
          no-section               an exe address in no section (headers, a gap, beyond the last section)
          fetch-text               a fetch at an exe .text address: an unrelocated code pointer used as a call target
                                   (native= the addrmap's native function at that address, what the pointer should
                                   have been rewritten to)
          rva-as-address           a fetch below the image base where addr + base is in .text (an RVA used as an address)
          data-unmapped            a read/write/fetch in a non-code exe section (the data window is not mapped yet)
    image-check-summary<TAB>count<TAB>kind<TAB>section<TAB>pc function<TAB>lr function<TAB>first addr[<TAB>native=..]
"""
import bisect
import collections
import re
import struct

from unicorn import UC_HOOK_CODE, UcError
from unicorn.arm64_const import UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0, \
    UC_ARM64_REG_X29

M32 = 0xFFFFFFFF
MAX_ROWS = 50
GAME_HEAPS = {"new", "mm", "ni"}

# (family, role, label, symbols, a, b). Symbols are guest symbols, or an exe address 0x00XXXXXX (the guest's
# __fnv_00XXXXXX); the ones a guest lacks are skipped. Roles and argument registers (xN):
#   alloc    a = size                       (b: second factor, calloc)
#   free     a = pointer
#   realloc  a = old pointer, b = size
ALLOC_TABLE = [
    # the game's MemoryManager (thiscall: x0 = this)
    ("mm", "alloc", "MM::Allocate", ["0x00aa3e40"], 1, None),
    ("mm", "free", "MM::Deallocate", ["0x00aa4060"], 1, None),
    ("mm", "realloc", "MM::Reallocate", ["0x00aa4150"], 1, 2),
    # Gamebryo NiMalloc / NiFree and the aligned and realloc forms
    ("ni", "alloc", "NiMalloc", ["0x00aa1070"], 0, None),
    ("ni", "alloc", "NiMallocAligned", ["0x00aa10b0"], 0, None),
    ("ni", "alloc", "NiMallocAligned2", ["0x00aa1130"], 0, None),
    ("ni", "free", "NiFree", ["0x00aa1460"], 0, None),
    ("ni", "free", "NiFree2", ["0x00aa1490"], 0, None),
    ("ni", "free", "NiFreeAligned", ["0x00aa1170"], 0, None),
    ("ni", "realloc", "NiRealloc", ["0x00aa11b0"], 0, 1),
    ("ni", "realloc", "NiReallocAligned", ["0x00aa1250"], 0, 1),
    # newlib
    ("libc", "alloc", "malloc", ["malloc"], 0, None),
    ("libc", "alloc", "_malloc_r", ["_malloc_r"], 1, None),
    ("libc", "alloc", "calloc", ["calloc"], 0, 1),
    ("libc", "alloc", "_calloc_r", ["_calloc_r"], 1, 2),
    ("libc", "alloc", "memalign", ["memalign"], 1, None),
    ("libc", "alloc", "_memalign_r", ["_memalign_r"], 2, None),
    ("libc", "free", "free", ["free"], 0, None),
    ("libc", "free", "_free_r", ["_free_r"], 1, None),
    ("libc", "realloc", "realloc", ["realloc"], 0, 1),
    ("libc", "realloc", "_realloc_r", ["_realloc_r"], 1, 2),
    # C++ operators (ILP32: size_t mangles as m or j depending on the toolchain)
    *[("new", "alloc", n, [n], 0, None) for n in
      ("_Znwm", "_Znam", "_Znwj", "_Znaj", "_ZnwmRKSt9nothrow_t", "_ZnamRKSt9nothrow_t",
       "_ZnwjRKSt9nothrow_t", "_ZnajRKSt9nothrow_t")],
    *[("new", "free", n, [n], 0, None) for n in
      ("_ZdlPv", "_ZdaPv", "_ZdlPvm", "_ZdaPvm", "_ZdlPvj", "_ZdaPvj", "_ZdlPvRKSt9nothrow_t",
       "_ZdaPvRKSt9nothrow_t")],
    # the game's operator new/delete overloads with a heap argument (operator new(size_t, Heap&, size_t), the decomp's
    # S00cb39d0_S_00aa3e40: MemoryManager): a block made through them is otherwise an unknown block when freed
    ("new", "alloc", "new(heap)", ["_ZnwmR20S00cb39d0_S_00aa3e40m"], 0, None),
    ("new", "free", "delete(heap)", ["_ZdlPvR20S00cb39d0_S_00aa3e40m"], 0, None),
    # platform/win32 shims (VirtualAlloc/VirtualFree reserve the game heaps' regions, not blocks: not tracked)
    ("win32", "alloc", "HeapAlloc", ["HeapAlloc"], 2, None),
    ("win32", "realloc", "HeapReAlloc", ["HeapReAlloc"], 2, 3),
    ("win32", "free", "HeapFree", ["HeapFree"], 2, None),
    ("win32", "alloc", "LocalAlloc", ["LocalAlloc"], 1, None),
    ("win32", "free", "LocalFree", ["LocalFree"], 0, None),
    ("win32", "alloc", "GlobalAlloc", ["GlobalAlloc"], 1, None),
    ("win32", "free", "GlobalFree", ["GlobalFree"], 0, None),
]


class SanitizeMixin:
    # ---------------------------------------------------------------- --alloc-check
    def alloc_check_targets(self, table=None):
        """runtime entry address -> (family, role, label, a, b) for the table's symbols this guest has"""
        syms = getattr(self, "code_syms", {})
        out = {}
        for fam, role, label, names, a, b in table or ALLOC_TABLE:
            for n in names:
                if re.fullmatch(r"0x[0-9a-fA-F]+", n):
                    n = f"__fnv_{int(n, 16):08x}"
                if n not in syms:
                    continue
                la = syms[n]
                rt = la - self.link_base + self.base if self.relocatable else la
                out.setdefault(rt, (fam, role, label, a, b))
        return out

    def install_alloc_check(self, table=None, heap=None):
        uc = self.uc
        targets = self.alloc_check_targets(table)
        heap_lo = self.heap if heap is None else heap[0]
        heap_hi = heap_lo + (0x40000000 if heap is None else heap[1])
        ac = self.ac = {"live": {}, "freed": {}, "rows": [], "groups": collections.Counter(), "first": {},
                        "frames": {}, "rets": set(), "allocs": 0, "frees": 0, "reallocs": 0, "targets": targets}

        def tid():
            return self.cur.tid if self.cur is not None else 0

        def stack(lr, fp):   # the call stack at the entry: lr, then the frame records at x29 (GCC keeps them)
            st = [lr & M32]
            for _ in range(2):
                if not fp or fp & 7:
                    break
                try:
                    nfp, ret = struct.unpack("<QQ", bytes(uc.mem_read(fp, 16)))
                except UcError:
                    break
                st.append(ret & M32)
                if nfp <= fp:
                    break
                fp = nfp
            return tuple(st)

        def finding(kind, ptr, fam, label, st, rec):
            if kind == "mismatch" and fam in GAME_HEAPS and rec[0] in GAME_HEAPS:
                kind = "mismatch-game"
            afn = f"{rec[0]}:{rec[1]}" if rec else "-"
            ffn = f"{fam}:{label}"
            key = (kind, ffn, afn)
            ac["groups"][key] += 1
            ac["first"].setdefault(key, (ptr, rec[3][0] if rec else 0))
            if len(ac["rows"]) < MAX_ROWS or ac["groups"][key] <= 2 and len(ac["rows"]) < 4 * MAX_ROWS:   # + the first two of each group
                ac["rows"].append((kind, ffn, afn, ptr, st, rec[3] if rec else (), rec[4] if rec and len(rec) > 4 else None,
                                    rec[5] if rec and len(rec) > 5 else ""))

        interior = {}

        def inside(ptr):
            """the live block that holds ptr, as a record with the family 'inside <family>' (a free of a pointer
            into a block: a string's own buffer, a member), or None"""
            if ptr not in interior:
                interior[ptr] = None
                for p, rec in ac["live"].items():
                    if p < ptr < p + max(rec[2], 1):
                        interior[ptr] = ("inside " + rec[0], rec[1], rec[2], rec[3], None, f"+{ptr - p:#x} of a {rec[2]}-byte block")
                        break
            return interior[ptr]

        def check_free(ptr, fam, label, st):
            if ptr == 0:
                return
            rec = ac["live"].get(ptr)
            if rec is not None:
                if rec[0] != fam:
                    finding("mismatch", ptr, fam, label, st, rec)
            elif ptr in ac["freed"]:
                finding("double-free", ptr, fam, label, st, ac["freed"][ptr])
            elif not heap_lo <= ptr < heap_hi:
                finding("non-heap", ptr, fam, label, st, None)
            else:
                finding("unknown-block", ptr, fam, label, st, inside(ptr))

        def release(ptr, fam, label, st):
            if ptr in ac["live"]:
                starts = ac.setdefault("starts", [])
                k = bisect.bisect_left(starts, ptr)
                if k < len(starts) and starts[k] == ptr:
                    starts.pop(k)
            rec = ac["live"].pop(ptr, None)
            if rec is not None:
                ac["freed"][ptr] = rec[:4] + ((fam, label, st),)

        def on_entry(uc, addr, size, _):
            if uc.reg_read(UC_ARM64_REG_PC) != addr:   # another hook on this entry redirected it
                return
            fam, role, label, a, b = targets[addr]
            t = tid()
            fr = ac["frames"].setdefault(t, [])
            sp, lr = uc.reg_read(UC_ARM64_REG_SP), uc.reg_read(UC_ARM64_REG_LR)
            if fr and fr[-1][0] == sp and fr[-1][1] == lr:   # a tail call out of the outermost function
                return
            while fr and fr[-1][0] <= sp:   # frames that an unwind left
                fr.pop()
            if fr:   # nested inside an outer allocator call: not a block of its own
                return
            args = lambda i: uc.reg_read(UC_ARM64_REG_X0 + i) & M32
            st = stack(lr, uc.reg_read(UC_ARM64_REG_X29))
            frame = [sp, lr, role, fam, label, st, 0, 0]   # + size, old pointer
            if role == "alloc":
                frame[6] = args(a) * (args(b) if b is not None else 1) & M32
            elif role == "free":
                check_free(args(a), fam, label, st)
                frame[7] = args(a)
            else:
                frame[7], frame[6] = args(a), args(b)
                check_free(frame[7], fam, label, st)
            fr.append(frame)
            if lr not in ac["rets"]:
                ac["rets"].add(lr)
                uc.hook_add(UC_HOOK_CODE, on_ret, begin=lr, end=lr)

        def on_ret(uc, addr, size, _):
            fr = ac["frames"].get(tid())
            sp = uc.reg_read(UC_ARM64_REG_SP)
            while fr and fr[-1][0] <= sp:
                f = fr.pop()
                if f[1] != addr:
                    continue   # a stale frame
                _sp, _lr, role, fam, label, st, n, old = f
                res = uc.reg_read(UC_ARM64_REG_X0) & M32
                if role == "free":
                    ac["frees"] += 1
                    release(old, fam, label, st)
                    break
                if role == "realloc":
                    ac["reallocs"] += 1
                    if old and (res != old and res or (not res and not n)):
                        release(old, fam, label, st)
                    if not res:
                        break
                elif not res:
                    break
                else:
                    ac["allocs"] += 1
                ac["freed"].pop(res, None)
                # overlap: the new block [res, res+n) must not intersect a live block (an allocator handing out
                # overlapping memory: a broken heap, e.g. a stand-in's pool or table that does not match the exe's)
                starts = ac.setdefault("starts", [])
                i = bisect.bisect_right(starts, res)
                for j in (i - 1, i):
                    if 0 <= j < len(starts) and starts[j] != res:
                        q = starts[j]
                        qrec = ac["live"].get(q)
                        # a block of 1 MiB or more is a heap's region (the VirtualAlloc fallback carves the game
                        # heaps from one memalign block), not a block: containment there is the design
                        if qrec and qrec[2] < (1 << 20) and n < (1 << 20) and \
                                q < res + max(n, 1) and res < q + max(qrec[2], 1):
                            finding("overlap", res, fam, label, st, qrec)
                            break
                if res not in ac["live"]:
                    bisect.insort(starts, res)
                ac["live"][res] = (fam, label, n, st)
                break

        for rt in targets:
            uc.hook_add(UC_HOOK_CODE, on_entry, begin=rt, end=rt)
        print(f"   alloc-check: {len(targets)} allocator entry points hooked")

    def alloc_lines(self):
        ac = getattr(self, "ac", None)
        if ac is None:
            return []
        sym = lambda a: re.sub(r"^0x[0-9a-f]+ ", "", self.sym(a))
        out = []
        for kind, ffn, afn, ptr, st, ast, freed_by, note in ac["rows"]:
            ast_s = (note + ": " if note else "") + " < ".join(sym(a) for a in ast)
            if freed_by:   # double free: the first free's family, function and stack
                ast_s += f" | first freed by {freed_by[0]}:{freed_by[1]} at " + " < ".join(sym(a) for a in freed_by[2])
            out.append("\t".join(["alloc-check", kind, ffn, afn, f"{ptr:#010x}", " < ".join(sym(a) for a in st), ast_s]))
        for (kind, ffn, afn), n in ac["groups"].most_common():
            ptr, first = ac["first"][(kind, ffn, afn)]
            out.append(f"alloc-check-summary\t{n}\t{kind}\t{ffn}\t{afn}\t{ptr:#010x}\t{sym(first) if first else '-'}")
        out.append(f"alloc-check-stats\tallocs={ac['allocs']}\tfrees={ac['frees']}\treallocs={ac['reallocs']}"
                   f"\tlive={len(ac['live'])}\tfreed={len(ac['freed'])}\tfindings={sum(ac['groups'].values())}")
        return out

    # ---------------------------------------------------------------- --image-check
    def install_image_check(self, exe_path, addrmap=None):
        import os
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, "..", "..", "loader", "tools"))
        import fnvnx_pe
        with open(exe_path, "rb") as f:
            pe = fnvnx_pe.PE(f.read())
        secs = [(s.name, pe.image_base + s.rva, pe.image_base + s.rva + s.vsize_eff, s.is_code) for s in pe.sections]
        native = {}
        if addrmap:
            for line in open(addrmap):
                f = line.rstrip("\n").split("\t")
                if len(f) > 3 and f[1] == "F":
                    native[int(f[0], 16)] = f[3]
        self.set_image_ranges(pe.image_base, pe.image_base + pe.size_of_image, secs, native)

    def set_image_ranges(self, base, end, secs, native=None):
        self.img = {"base": base, "end": end, "secs": secs, "native": native or {}, "rows": [], "seen": set(),
                    "groups": collections.Counter(), "first": {}}

    def classify_image(self, access, addr):
        """(kind, section, native) of a faulting access against the exe image, or None when it is not the image's.
        access: unicorn's UC_MEM_* (16 read, 17 write, 18 fetch, then the unmapped and protection variants)"""
        im = self.img
        what = "fetch" if access in (18, 21, 24) else "write" if access in (17, 20, 22) else "read"
        if im["base"] <= addr < im["end"]:
            sec = next((s for s in im["secs"] if s[1] <= addr < s[2]), None)
            if sec is None:
                return "no-section", "-", None
            if not sec[3]:
                return "data-unmapped", sec[0], None
            if what == "fetch":
                return "fetch-text", sec[0], im["native"].get(addr)
            return "code-" + what, sec[0], None
        if what == "fetch" and 0x1000 <= addr < im["base"]:
            sec = next((s for s in im["secs"] if s[3] and s[1] <= addr + im["base"] < s[2]), None)
            if sec is not None:
                return "rva-as-address", sec[0], im["native"].get(addr + im["base"])
        return None

    def image_fault(self, access, addr, pc, lr):
        im = getattr(self, "img", None)
        if im is None:
            return
        got = self.classify_image(access, addr)
        if got is None:
            return
        kind, sec, nat = got
        fn = lambda a: re.sub(r"\+0x[0-9a-f]+$", "", re.sub(r"^0x[0-9a-f]+ ", "", self.sym(a)))
        key = (kind, sec, fn(pc), fn(lr))
        im["groups"][key] += 1
        im["first"].setdefault(key, (addr, nat))
        if (kind, addr, pc) not in im["seen"] and len(im["rows"]) < MAX_ROWS:
            im["seen"].add((kind, addr, pc))
            row = ["image-check", kind, f"{addr:#010x}", sec, self.sym(pc), self.sym(lr)]
            if nat:
                row.append("native=" + nat)
            im["rows"].append("\t".join(row))

    def image_lines(self):
        im = getattr(self, "img", None)
        if im is None:
            return []
        out = list(im["rows"])
        for key, n in im["groups"].most_common():
            addr, nat = im["first"][key]
            out.append("\t".join(["image-check-summary", str(n), *key, f"{addr:#010x}"] + (["native=" + nat] if nat else [])))
        return out
