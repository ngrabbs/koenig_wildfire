"""Policy/source checks and stage observers. Nothing runs at module import."""
from pathlib import Path
import contextlib, hashlib, json, math, os, sys, time
from .errors import ContractViolation, ExecutionDisabled
from . import constants as K
ROOT = Path(__file__).resolve().parents[1]

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

def digest(data):
    return hashlib.sha256(data).hexdigest()

def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def _read_local(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))

def require_execution_policy():
    policy = _read_local("execution_policy.json")
    if (set(policy) != {"schema", "execution_enabled", "approval_scope", "generated_only", "notice"}
        or policy["schema"] != "J7B2_IMPLEMENTATION_ONLY_POLICY_V1"
        or policy["execution_enabled"] is not True
        or policy["generated_only"] is not True
        or policy["approval_scope"] != "SEPARATELY_APPROVED_B3_GENERATED_ONLY"):
        raise ExecutionDisabled("B2 is implementation-only; B3 execution is not authorized.")
    if not sys.dont_write_bytecode or os.environ.get("OPENBLAS_NUM_THREADS")!="1" or os.environ.get("OMP_NUM_THREADS")!="1":
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE","B3 requires Python -B and pre-import CPU thread environment")

def source_identity():
    manifest = _read_local("source_manifest.json")
    entries = manifest["files"]
    if manifest["schema"] != "J7B2_SOURCE_MANIFEST_V1" or not isinstance(entries, dict):
        raise ContractViolation("SOURCE_IDENTITY_CHANGED")
    if digest(canonical(entries)) != manifest["build_id"]:
        raise ContractViolation("SOURCE_IDENTITY_CHANGED", "manifest digest")
    for name, expected in entries.items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or file_digest(path) != expected:
            raise ContractViolation("SOURCE_IDENTITY_CHANGED", name)
    return manifest["build_id"]

def configure_runtime():
    require_execution_policy()
    build_id = source_identity()
    env = _read_local("environment_manifest.json")
    if os.name != "nt" or os.environ.get("COMPUTERNAME") != env["host"]:
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "unreviewed host/platform")
    if tuple(sys.version_info[:3]) != (3, 12, 14):
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "unreviewed Python")
    # Reference cryptography is not a dependency of this library.
    for item in env["native_files"]:
        if "cryptography" not in item["path"].lower():
            if file_digest(item["path"]) != item["sha256"]:
                raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "native identity")
    import numpy as np
    import cv2
    if np.__version__ != "2.3.5" or cv2.__version__ != "5.0.0":
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "runtime version")
    numpy_entry = next(x for x in env["python_loader_files"] if x["path"].replace("\\","/").endswith("numpy/__init__.py"))
    if Path(np.__file__).resolve()!=Path(numpy_entry["path"]).resolve() or file_digest(np.__file__)!=numpy_entry["sha256"]:
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE","loaded NumPy package identity")
    numpy_core = sys.modules.get("numpy._core._multiarray_umath")
    expected_core = next(x for x in env["native_files"] if "_multiarray_umath." in x["path"])
    if numpy_core is None or Path(numpy_core.__file__).resolve()!=Path(expected_core["path"]).resolve() or file_digest(numpy_core.__file__)!=expected_core["sha256"]:
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE","loaded NumPy core identity")
    cv_native = Path(cv2.__file__).resolve().parent / "cv2.pyd"
    if file_digest(cv_native) != K.CV_NATIVE_SHA256:
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "loaded OpenCV path")
    if digest(cv2.getBuildInformation().encode()) != K.CV_BUILD_SHA256:
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "OpenCV build")
    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)
    cv2.ocl.setUseOpenCL(False)
    if cv2.getNumThreads() != 1 or cv2.ocl.useOpenCL():
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "CPU/thread policy")
    return build_id

def process_memory():
    """Windows process counters; sampled RSS and lifetime high-water, not stage RSS."""
    if os.name != "nt":
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "memory guard unavailable")
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    value = Counters()
    value.cb = ctypes.sizeof(value)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
        raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "memory counter")
    return {"working_set": int(value.WorkingSetSize),
            "process_lifetime_peak_working_set": int(value.PeakWorkingSetSize)}

class Monitor:
    def __init__(self, session_start):
        self.session_start = session_start
        self.start = time.perf_counter()
        self.cpu_start = time.process_time()
        self.rows = []
        self.calls = {}
        self.live = {}
        self.peak_owned = 0
        self.input_bytes = 0
        self.active_stage = None
        self.memory_samples = []
    def count(self, name, n=1):
        self.calls[name] = self.calls.get(name, 0) + n
    def own(self, name, size):
        if type(size) is not int or size < 0 or name in self.live:
            raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "allocation ledger")
        self.live[name] = size
        self.peak_owned = max(self.peak_owned, sum(self.live.values()))
    def release(self, name):
        self.live.pop(name, None)
    def guard(self):
        now = time.perf_counter()
        if now-self.start > K.MAX_EVENT_SECONDS or now-self.session_start > K.MAX_SESSION_SECONDS:
            raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "cooperative elapsed cap")
        memory = process_memory()
        self.memory_samples.append(memory)
        if max(memory.values()) > K.MAX_WORKING_SET:
            raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "process memory cap")
    @contextlib.contextmanager
    def stage(self, name):
        if self.active_stage is not None:
            raise ContractViolation("RESOURCE_OR_LIBRARY_FAILURE", "nested exclusive stage")
        self.guard()
        self.active_stage = name
        wall, cpu, ok = time.perf_counter(), time.process_time(), False
        try:
            yield
            ok = True
        finally:
            self.rows.append({"name": name, "wall_seconds": time.perf_counter()-wall,
                              "cpu_seconds": time.process_time()-cpu, "success": ok})
            self.active_stage = None
            if ok:
                self.guard()
    def snapshot(self):
        memory = process_memory()
        return {"exclusive_stages": list(self.rows), "calls": dict(self.calls),
                "inclusive_event_wall_seconds": time.perf_counter()-self.start,
                "inclusive_event_cpu_seconds": time.process_time()-self.cpu_start,
                "input_bytes": self.input_bytes, "owned_bytes_live": sum(self.live.values()),
                "peak_ledger_owned_bytes": self.peak_owned, "process_memory": memory,
                "memory_scope": "SAMPLED_PROCESS_AND_LIFETIME_PEAK_NOT_STAGE_EXCLUSIVE",
                "untracked_native_scratch": True, "file_output_bytes": 0,
                "serialization_timing": "EXTERNAL_CALLER_NOT_INCLUDED",
                "hard_cap_enforcement": "EXTERNAL_B3_SUPERVISOR_REQUIRED"}
