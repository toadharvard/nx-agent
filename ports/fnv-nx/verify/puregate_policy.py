"""puregate policy tables: DATA ONLY (the rules that read them are in puregate.py, which documents the assumptions).

Rule classes: clock (wall/monotonic time, file times), inode (inode/device/link identity), entropy (random, id, hash),
unsorted-listing (directory order), unsorted-set (set iteration order), host (machine identity/load), completion-order
(results in finish order), dynamic (code or names the gate cannot see), import-unlisted (module outside the tables
below), member-unlisted (member of a restricted module outside its allow-list), stat-result (stat result used for
anything but .st_size/.st_mode), syntax / shell-unanalysable (not analysable).
"""
# (rule, exact qualified names, qualified-name prefixes)
POLICY = [
    ("clock", "datetime.datetime.now datetime.datetime.today datetime.datetime.utcnow datetime.date.today "
     "os.path.getmtime os.path.getatime os.path.getctime os.utime stat.ST_MTIME stat.ST_ATIME stat.ST_CTIME "
     "shutil.copy2 shutil.copystat shutil.copytree shutil.make_archive", "time."),
    ("inode", "os.path.samefile os.path.samestat os.path.sameopenfile stat.ST_INO stat.ST_DEV stat.ST_NLINK", ""),
    ("entropy", "uuid.uuid1 uuid.uuid4 os.urandom os.getrandom", "random. secrets. tempfile."),
    ("unsorted-listing", "os.listdir os.scandir glob.glob glob.iglob os.walk os.fwalk", ""),
    ("host", "os.cpu_count os.process_cpu_count multiprocessing.cpu_count os.sched_getaffinity os.uname os.getpid "
     "os.getppid os.getlogin os.getuid os.geteuid os.getgid os.times os.getloadavg os.statvfs threading.get_ident "
     "threading.get_native_id resource.getrusage shutil.disk_usage shutil.get_terminal_size", "platform. socket. getpass. pwd. grp."),
    ("completion-order", "concurrent.futures.as_completed concurrent.futures.wait queue.Queue queue.SimpleQueue "
     "multiprocessing.Queue multiprocessing.SimpleQueue", ""),
    ("dynamic", "sys.modules", "importlib. builtins."),
]
# attribute names that carry a rule on ANY expression
ATTR = {"imap_unordered": "completion-order", "iterdir": "unsorted-listing", "glob": "unsorted-listing",
        "rglob": "unsorted-listing", "st_ino": "inode", "st_dev": "inode", "st_nlink": "inode", "inode": "inode", "st_uid": "host", "st_gid": "host",
        "owner": "host", "group": "host",
        "native_id": "host", "stat": "stat-result", "lstat": "stat-result", "fstat": "stat-result",
        "now": "clock", "today": "clock", "utcnow": "clock", "__hash__": "entropy"}
ATTR.update({a: "clock" for a in "st_mtime st_atime st_ctime st_mtime_ns st_atime_ns st_ctime_ns st_birthtime".split()})
ATTR.update({a: "dynamic" for a in "__dict__ __getattribute__ __builtins__ __import__ __subclasses__ __globals__ __code__ "
             "__self__ __closure__ f_globals f_builtins f_locals f_back tb_frame gi_frame".split()})
CANON = {"posixpath": "os.path", "genericpath": "os.path", "ntpath": "os.path"}
BAD_BUILTIN = ("eval", "exec", "compile", "__import__", "globals", "locals", "__builtins__")
CALL_BUILTIN = ("getattr", "setattr", "delattr", "vars", "dir")
STATQ = ("os.stat", "os.lstat", "os.fstat")  # results usable only as os.stat(..).st_size / .st_mode
ST_OK = ("st_size", "st_mode")
# order-exposing consumers of a bare set expression
ORDERED = "list tuple enumerate iter map zip filter reversed".split()
ORDER_ATTRS = "join extend fromkeys".split()
SETOPS = "union intersection difference symmetric_difference copy".split()
# gzip/zipfile/tarfile members whose call is analysed for read/write mode (writing embeds mtime)
MODECALLS = "gzip.open gzip.GzipFile gzip.compress zipfile.ZipFile tarfile.open tarfile.TarFile".split()
# (a) PURE: stdlib modules whose documented behaviour is a function of their arguments (assumption A-stdlib)
PURE = set(("ast json hashlib re struct collections itertools functools dataclasses typing enum abc math bisect heapq "
            "array binascii base64 zlib io string textwrap shlex fnmatch posixpath copy contextlib types argparse errno "
            "stat csv difflib bz2 lzma codecs unicodedata decimal fractions traceback warnings keyword "
            "tokenize token __future__ ntpath genericpath").split())
# (b) RESTRICTED: module -> allowed members ("*" all, "P_*" prefix, dotted "a.b" entries); rules above apply first
RESTRICTED = {
    "os": "path sep linesep pathsep environ getenv makedirs mkdir remove unlink rename replace rmdir link symlink readlink "
          "open close read write fsync dup2 chmod getcwd chdir fspath fsencode fsdecode devnull EX_* O_* SEEK_* R_OK W_OK "
          "X_OK F_OK listdir scandir walk fwalk stat lstat fstat access isatty strerror pipe _exit PathLike curdir pardir "
          "error name utime",
    "os.path": "*",
    "sys": "argv stdin stdout stderr exit path version_info maxsize byteorder executable exc_info intern "
           "getrecursionlimit setrecursionlimit platform flags.hash_randomization",
    "time": "sleep",
    "datetime": "datetime date time timedelta timezone tzinfo MINYEAR MAXYEAR UTC",
    "glob": "glob iglob escape has_magic",
    "shutil": "copyfile copyfileobj copymode rmtree move which",
    "subprocess": "*",
    "pathlib": "Path PurePath PurePosixPath PosixPath",
    "concurrent.futures": "ThreadPoolExecutor ProcessPoolExecutor Future",
    "multiprocessing": "Pool",
    "threading": "Thread Lock RLock Event local",
    "queue": "", "resource": "",
    "uuid": "UUID uuid3 uuid5 NAMESPACE_*",
    "gzip": "open GzipFile compress decompress BadGzipFile",
    "zipfile": "ZipFile BadZipFile is_zipfile",
    "tarfile": "open TarFile TarError ReadError is_tarfile",
}
# modules every member of which is ruled: importing is harmless, any use is reported by its rule
BANNED = set("random secrets tempfile platform socket getpass pwd grp importlib builtins".split())
# (d) EXTERNAL-PURE: third-party modules assumed pure (assumption A-ext)
EXTERNAL = set("capstone elftools unicorn mako z3".split())   # z3: the z3-solver wheel, pinned by sha256 in env.json (assumption ASM-009: pinned formal tools)
LISTED = PURE | set(RESTRICTED) | BANNED | EXTERNAL
