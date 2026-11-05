"""Conservative local process sample; run inside the production container too."""

import base64
import ctypes
import json
import os
import platform

from app.web import app  # noqa: F401: load the same app modules as the worker


def resident_mb() -> float:
    if platform.system() == "Windows":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return counters.WorkingSetSize / 1024 / 1024
    import resource
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 if platform.system() == "Linux" else 1024 * 1024)


print(f"App import: {resident_mb():.1f} MB RSS")
raw = os.urandom(8 * 1024 * 1024)
encoded = base64.b64encode(raw).decode("ascii")
request = json.dumps({"input": [{"content": [{"type": "input_file",
    "filename": "sample.pdf", "file_data": "data:application/pdf;base64," + encoded}]}]})
print(f"With one 8 MB direct-PDF request payload: {resident_mb():.1f} MB RSS")
assert len(request) > len(raw)
