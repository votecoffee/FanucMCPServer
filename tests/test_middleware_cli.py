import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


LAUNCHER = Path(__file__).resolve().parents[1] / "RunMiddleware.cmd"
PROCESS_TIMEOUT = 60
LISTING = b"""/PROG TESTPOS
/MN
/POS
P[1]{
   GP1:
    UF : 0, UT : 1, CONFIG : 'N U T, 0, 0, 0',
    X = 1.0 mm, Y = 2.0 mm, Z = 3.0 mm,
    W = 4.0 deg, P = 5.0 deg, R = 6.0 deg
};
P[2]{
   GP1:
    UF : 0, UT : 1, CONFIG : 'N U T, 0, 0, 0',
    X = 7.0 mm, Y = 8.0 mm, Z = 9.0 mm,
    W = 4.0 deg, P = 5.0 deg, R = 6.0 deg
};
/END
"""
HOLDER = r"""
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys

kernel = ctypes.WinDLL("kernel32")
kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
kernel.CreateMutexW.restype = wintypes.HANDLE
kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel.WaitForSingleObject.restype = wintypes.DWORD
kernel.ReleaseMutex.argtypes = kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
handle = kernel.CreateMutexW(None, False, r"Local\OpenAIEngineeringMiddleware.Job.v1")
if not handle:
    sys.exit(2)
try:
    if kernel.WaitForSingleObject(handle, 0) != 0:
        sys.exit(3)
    try:
        Path(sys.argv[1]).touch()
        sys.stdin.buffer.read(1)
    finally:
        if not kernel.ReleaseMutex(handle):
            sys.exit(4)
finally:
    kernel.CloseHandle(handle)
"""


@unittest.skipUnless(os.name == "nt", "Windows session admission")
class MiddlewareCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [str(LAUNCHER), *map(str, args)],
            env={**os.environ, "FANUC_MIDDLEWARE_PYTHON": sys.executable},
            capture_output=True,
            text=True,
            timeout=PROCESS_TIMEOUT,
        )

    def test_shared_mutex_refuses_work_and_allows_help(self):
        with tempfile.TemporaryDirectory(prefix="fanuc middleware ") as temp:
            ready = Path(temp) / "ready"
            with subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", HOLDER, str(ready)],
                stdin=subprocess.PIPE,
            ) as holder:
                try:
                    deadline = time.monotonic() + PROCESS_TIMEOUT
                    while not ready.exists():
                        self.assertIsNone(holder.poll(), "holder failed to acquire the mutex")
                        self.assertLess(time.monotonic(), deadline, "holder failed to report readiness")
                        time.sleep(0.02)

                    help_result = self.run_cli("--help")
                    self.assertEqual(help_result.returncode, 0, help_result.stderr)
                    self.assertIn("positions", help_result.stdout)

                    missing = Path(temp) / "missing.LS"
                    busy = self.run_cli("positions", "--input", missing)
                    self.assertEqual(busy.returncode, 75, busy.stderr)
                    self.assertEqual(json.loads(busy.stdout)["error"]["code"], "busy")
                finally:
                    holder.stdin.close()
                    try:
                        holder.wait(timeout=PROCESS_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        holder.kill()
                        holder.wait(timeout=PROCESS_TIMEOUT)
                        raise

            self.assertEqual(holder.returncode, 0)
            unavailable = self.run_cli("positions", "--input", missing)
            self.assertEqual(unavailable.returncode, 66, unavailable.stderr)
            self.assertEqual(json.loads(unavailable.stdout)["error"]["code"], "input_unavailable")

    def test_reads_bounded_page_and_reports_exact_input(self):
        with tempfile.TemporaryDirectory(prefix="fanuc middleware ") as temp:
            path = Path(temp) / "saved listing.LS"
            path.write_bytes(LISTING)
            first = self.run_cli("positions", "--input", path, "--limit", "1")
            self.assertEqual(first.returncode, 0, first.stderr)
            data = json.loads(first.stdout)
            self.assertEqual(data["input"]["sha256"], hashlib.sha256(LISTING).hexdigest())
            self.assertEqual(data["result"]["total"], 2)
            self.assertEqual(data["result"]["next_offset"], 1)
            self.assertEqual(data["result"]["items"][0]["values"]["X"], 1.0)

            second = self.run_cli("positions", "--input", path, "--offset", "1")
            self.assertEqual(second.returncode, 0, second.stderr)
            data = json.loads(second.stdout)["result"]
            self.assertIsNone(data["next_offset"])
            self.assertEqual(data["items"][0]["values"]["X"], 7.0)
            self.assertEqual(path.read_bytes(), LISTING)

            with path.open("wb") as stream:
                stream.truncate(1024 * 1024 + 1)
            oversized = self.run_cli("positions", "--input", path)
            self.assertEqual(oversized.returncode, 65, oversized.stderr)
            self.assertEqual(json.loads(oversized.stdout)["error"]["code"], "input_too_large")
