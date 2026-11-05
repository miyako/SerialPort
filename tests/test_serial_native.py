"""Portable pipe protocol tests and real macOS PTY serial integration."""
import base64
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE = None
CONFIG = dict(baudRate=115200, dataBits=8, stopBits="one", parity="none", flowControl="none")


class Session:
    def __init__(self, testcase):
        self.testcase = testcase
        self.proc = subprocess.Popen([str(EXECUTABLE), "serve"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines = queue.Queue()
        self.saved = []
        self.seq = 0
        self.output_bytes = 0
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()
        testcase.addCleanup(self.cleanup)

    def read(self):
        for line in self.proc.stdout:
            try:
                self.output_bytes += len(line)
                assert line.isascii() and line.endswith(b"\n")
                self.lines.put(json.loads(line))
            except Exception as error:
                self.lines.put(error)

    def send(self, op, **fields):
        self.seq += 1
        identifier = str(self.seq)
        self.raw(json.dumps(dict(protocol=1, id=identifier, op=op, **fields)).encode() + b"\n")
        return identifier

    def raw(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def until(self, predicate, timeout=8):
        for n, value in enumerate(self.saved):
            if predicate(value):
                return self.saved.pop(n)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = self.lines.get(timeout=max(.01, end - time.monotonic()))
            if isinstance(value, Exception):
                raise value
            if predicate(value):
                return value
            self.saved.append(value)
        raise AssertionError("missing response/event")

    def response(self, identifier):
        return self.until(lambda r: r.get("id") == identifier)

    def call(self, op, **fields):
        return self.response(self.send(op, **fields))

    def cleanup(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.thread.join(timeout=2)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            stream.close()


class PortableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global EXECUTABLE
        if os.environ.get("TEST_CLI_LANGUAGE") == "none":
            raise unittest.SkipTest("CLI explicitly disabled")
        if EXECUTABLE is None:
            candidates = [ROOT / "build/cli-test/serial-port",
                          ROOT / "build/cli-test/Release/serial-port.exe",
                          ROOT / "build/cli-test/serial-port.exe"]
            EXECUTABLE = next((p for p in candidates if p.exists()), None)
        if EXECUTABLE is None:
            raise RuntimeError("build CLI first or supply --executable")

    def test_fragmented_coalesced_and_shutdown(self):
        s = Session(self)
        frame = json.dumps(dict(protocol=1, id="世界", op="list"), ensure_ascii=False).encode() + b"\n"
        for byte in frame:
            s.raw(bytes([byte]))
        self.assertTrue(s.response("世界")["ok"])
        s.raw(b'{"protocol":1,"id":"a","op":"list"}\n{"protocol":1,"id":"b","op":"list"}\n')
        self.assertTrue(s.response("a")["ok"])
        self.assertTrue(s.response("b")["ok"])
        self.assertTrue(s.call("shutdown")["ok"])
        self.assertEqual(s.proc.wait(timeout=5), 0)

    def test_invalid_and_eof(self):
        s = Session(self)
        for raw in [b"null\n", b"\xff\n", b"{}\n",
                    b'{"protocol":true,"id":"bad","op":"list"}\n']:
            s.raw(raw)
            self.assertFalse(s.until(lambda r: "id" in r)["ok"])
        self.assertFalse(s.call("open", path="missing-serial-device", config=CONFIG)["ok"])
        self.assertEqual(s.call("cancel", requestId="missing")["error"]["code"], "request_not_found")
        s.proc.stdin.close()
        self.assertEqual(s.proc.wait(timeout=5), 0)

    def test_exact_host_sequence(self):
        s = Session(self)
        requests = [
            dict(protocol=1, id="1", op="list"),
            dict(protocol=1, id="2", op="open", path="", config={"baudRate": 9600}),
            dict(protocol=1, id="3", op="close", connection="not-a-connection"),
            dict(protocol=1, id="4", op="cancel", requestId="not-a-request"),
            dict(protocol=1, id="5", op="shutdown"),
        ]
        s.raw(b"".join(json.dumps(r).encode() + b"\n" for r in requests))
        for identifier, expected in [("1", True), ("2", False), ("3", False),
                                     ("4", False), ("5", True)]:
            response = s.response(identifier)
            self.assertEqual(response["ok"], expected)
            if expected:
                self.assertIsInstance(response["result"], dict)
        self.assertEqual(s.proc.wait(timeout=5), 0)

    def test_frame_limit(self):
        s = Session(self)
        s.raw(b"x" * 65537)
        self.assertEqual(s.response("")["error"]["code"], "frame_limit")
        self.assertEqual(s.proc.wait(timeout=5), 2)

    def test_depth_limit(self):
        s = Session(self)
        s.raw(b"[" * 65 + b"]" * 65 + b"\n")
        self.assertFalse(s.response("")["ok"])
        self.assertTrue(s.call("list")["ok"])

    def test_broken_stdout(self):
        proc = subprocess.Popen([str(EXECUTABLE), "serve"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.stdout.close()
        proc.stdin.write(b'{"protocol":1,"id":"x","op":"list"}\n')
        proc.stdin.flush()
        self.assertEqual(proc.wait(timeout=5), 3)
        self.assertEqual(json.loads(proc.stderr.read())["error"]["code"], "output_failed")
        proc.stdin.close()
        proc.stderr.close()

    def test_exact_frame_limit_and_partial_eof(self):
        s = Session(self)
        raw = b'{"protocol":1,"id":"large","op":"list","padding":"'
        raw += b"x" * (65536 - len(raw) - 2) + b'"}\n'
        s.raw(raw)
        self.assertTrue(s.response("large")["ok"])
        s.raw(b'{"protocol":1')
        s.proc.stdin.close()
        self.assertEqual(s.response("")["error"]["code"], "invalid_frame")
        self.assertEqual(s.proc.wait(timeout=5), 0)

    @unittest.skipUnless(os.name == "posix", "POSIX parent termination signal")
    def test_termination(self):
        s = Session(self)
        self.assertTrue(s.call("list")["ok"])
        s.proc.send_signal(signal.SIGTERM)
        self.assertEqual(s.proc.wait(timeout=5), 0)


@unittest.skipUnless(sys.platform == "darwin", "real native macOS PTY tests")
class PTYTests(PortableTests):
    def port(self, session):
        import pty
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        result = session.call("open", path=os.ttyname(slave), config=CONFIG)
        self.assertTrue(result["ok"], result)
        return master, result["result"]["connection"]

    def receive(self, s, connection, expected):
        data = b""
        while len(data) < len(expected):
            event = s.until(lambda r: r.get("event") == "data" and r["connection"] == connection)
            data += base64.b64decode(event["data"], validate=True)
        self.assertEqual(data, expected)

    def test_two_ports_binary_serialization(self):
        import select
        s = Session(self)
        m1, c1 = self.port(s)
        m2, c2 = self.port(s)
        self.assertNotEqual(c1, c2)
        data = bytes(range(256)) * 8
        os.write(m1, data)
        os.write(m2, data[::-1])
        self.receive(s, c1, data)
        self.receive(s, c2, data[::-1])
        ids = [s.send("write", connection=c1, data=base64.b64encode(chunk).decode(),
                      timeoutMs=5000) for chunk in (data, data[::-1])]
        received = b""
        while len(received) < len(data) * 2:
            self.assertTrue(select.select([m1], [], [], 5)[0])
            received += os.read(m1, 8192)
        self.assertEqual(received, data + data[::-1])
        for identifier in ids:
            self.assertEqual(s.response(identifier)["result"]["transferred"], len(data))
        self.assertTrue(s.call("configure", connection=c2, config=CONFIG)["ok"])
        self.assertTrue(s.call("configure", connection=c2, config={**CONFIG, "stopBits": 2})["ok"])
        self.assertTrue(s.call("configure", connection=c2, config={**CONFIG, "stopBits": "two"})["ok"])
        self.assertTrue(s.call("configure", connection=c2, config={**CONFIG, "stopBits": 1})["ok"])
        self.assertTrue(s.call("close", connection=c1)["ok"])
        self.assertFalse(s.call("write", connection=c1, data="", timeoutMs=1)["ok"])

    def test_invalid_config_base64_and_empty_write(self):
        s = Session(self)
        master, connection = self.port(s)
        for value in ["!!!=", "AB==", "a", base64.b64encode(b"a" * 32769).decode()]:
            self.assertFalse(s.call("write", connection=connection, data=value, timeoutMs=10)["ok"])
        self.assertEqual(s.call("write", connection=connection, data="", timeoutMs=100)["result"]["transferred"], 0)
        self.assertFalse(s.call("configure", connection=connection, config={**CONFIG, "dataBits": True})["ok"])
        self.assertEqual(s.call("close", connection=connection)["error"]["code"], "connection_not_found")

    def test_timeout_cancel_and_overflow(self):
        blob = base64.b64encode(b"z" * 32768).decode()
        for mode in ["timeout", "cancel", "overflow"]:
            with self.subTest(mode=mode):
                s = Session(self)
                master, connection = self.port(s)
                identifier = s.send("write", connection=connection, data=blob,
                                    timeoutMs=30 if mode == "timeout" else 10000)
                if mode == "cancel":
                    self.assertTrue(s.call("cancel", requestId=identifier)["ok"])
                if mode == "overflow":
                    identifiers = [s.send("write", connection=connection, data=blob, timeoutMs=10000)
                                   for _ in range(9)]
                    result = s.until(lambda r: r.get("error", {}).get("code") == "write_queue_limit")
                    self.assertFalse(result["ok"])
                result = s.response(identifier)
                self.assertFalse(result["ok"], result)
                self.assertIn("transferred", result["error"])
                self.assertLessEqual(result["error"]["transferred"], 32768)
                if mode != "overflow":
                    self.assertEqual(result["error"]["code"], "cancelled" if mode == "cancel" else "timeout")
                s.until(lambda r: r.get("event") == "disconnected" and r["connection"] == connection)
                self.assertTrue(s.call("shutdown")["ok"])
                self.assertEqual(s.proc.wait(timeout=5), 0)

    def test_queued_cancel_and_duplicate_id(self):
        s = Session(self)
        master, connection = self.port(s)
        data = base64.b64encode(b"x" * 32768).decode()
        first = s.send("write", connection=connection, data=data, timeoutMs=10000)
        second = s.send("write", connection=connection, data=data, timeoutMs=10000)
        duplicate = dict(protocol=1, id=first, op="list")
        s.raw(json.dumps(duplicate).encode() + b"\n")
        self.assertEqual(s.response(first)["error"]["code"], "duplicate_id")
        self.assertTrue(s.call("cancel", requestId=second)["ok"])
        result = s.response(second)
        self.assertEqual(result["error"]["code"], "cancelled")
        self.assertEqual(result["error"]["transferred"], 0)
        self.assertFalse(s.response(first)["ok"])
        self.assertTrue(s.call("shutdown")["ok"])

    def test_external_disconnect(self):
        import pty
        master, slave = pty.openpty()
        self.addCleanup(os.close, slave)
        s = Session(self)
        response = s.call("open", path=os.ttyname(slave), config=CONFIG)
        self.assertTrue(response["ok"])
        connection = response["result"]["connection"]
        os.close(master)
        s.until(lambda r: r.get("event") == "disconnected" and r["connection"] == connection)
        self.assertEqual(s.call("close", connection=connection)["error"]["code"], "connection_not_found")

    def test_parent_death_with_retained_stdin_releases_port(self):
        import pty
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        read_fd, write_fd = os.pipe()
        bridge_code = (
            "import subprocess,sys,time\n"
            "child=subprocess.Popen([sys.argv[1],'serve'],stdin=sys.stdin.buffer,"
            "stdout=sys.stdout.buffer,stderr=sys.stderr.buffer)\n"
            "print(child.pid,file=sys.stderr,flush=True)\n"
            "time.sleep(300)\n"
        )
        bridge = subprocess.Popen([sys.executable, "-c", bridge_code, str(EXECUTABLE)],
                                  stdin=read_fd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        os.close(read_fd)
        child_pid = int(bridge.stderr.readline())
        lines = queue.Queue()

        def consume():
            for line in bridge.stdout:
                lines.put(json.loads(line))
            lines.put(None)

        thread = threading.Thread(target=consume, daemon=True)
        thread.start()
        try:
            request = dict(protocol=1, id="parent-open", op="open",
                           path=os.ttyname(slave), config=CONFIG)
            os.write(write_fd, json.dumps(request).encode() + b"\n")
            result = lines.get(timeout=5)
            self.assertTrue(result["ok"], result)
            connection = result["result"]["connection"]
            os.write(master, b"parent-liveness")
            event = lines.get(timeout=5)
            self.assertEqual(event["connection"], connection)
            self.assertEqual(base64.b64decode(event["data"]), b"parent-liveness")
            bridge.kill()
            bridge.wait(timeout=5)
            # The outer process still owns write_fd: stdin EOF is impossible here.
            self.assertIsNone(lines.get(timeout=5))
            deadline = time.monotonic() + 5
            released = False
            while time.monotonic() < deadline:
                try:
                    try:
                        reaped, _ = os.waitpid(child_pid, os.WNOHANG)
                    except ChildProcessError:
                        reaped = 0
                    if reaped:
                        released = True
                        break
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    released = True
                    break
                time.sleep(.05)
            self.assertTrue(released, "helper remained alive after parent death")
            self.assertEqual(bridge.stderr.read(), b"")
        finally:
            os.close(write_fd)
            if bridge.poll() is None:
                bridge.kill()
                bridge.wait(timeout=5)
            try:
                os.kill(child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            thread.join(timeout=2)
            bridge.stdout.close()
            bridge.stderr.close()

    def test_eof_releases_open_port(self):
        s = Session(self)
        master, connection = self.port(s)
        s.proc.stdin.close()
        self.assertEqual(s.proc.wait(timeout=5), 0)

    def test_connection_and_pending_limits(self):
        s = Session(self)
        opened = [self.port(s) for _ in range(16)]
        self.assertEqual(s.call("open", path="unused", config=CONFIG)["error"]["code"], "port_limit")
        data = base64.b64encode(b"x" * 32768).decode()
        ids = [s.send("write", connection=c, data=data, timeoutMs=10000)
               for master, c in opened for _ in range(8)]
        result = s.call("write", connection=opened[0][1], data=data, timeoutMs=10000)
        self.assertEqual(result["error"]["code"], "pending_limit")
        self.assertTrue(s.call("cancel", requestId=ids[0])["ok"])
        self.assertEqual(s.response(ids[0])["error"]["code"], "cancelled")
        self.assertTrue(s.call("shutdown")["ok"])
        self.assertEqual(s.proc.wait(timeout=5), 0)

    def test_output_session_limit(self):
        s = Session(self)
        master, connection = self.port(s)
        os.set_blocking(master, False)
        end = time.monotonic() + 20
        while s.proc.poll() is None and time.monotonic() < end:
            try:
                os.write(master, bytes(range(256)) * 16)
            except BlockingIOError:
                pass
            time.sleep(.002)
        result = s.until(lambda r: r.get("event") == "session_limit")
        self.assertEqual(result["error"]["code"], "output_limit")
        self.assertEqual(s.proc.wait(timeout=5), 0)
        s.thread.join(timeout=2)
        self.assertLessEqual(s.output_bytes, 8 * 1024 * 1024)
        self.assertGreater(s.output_bytes, 8 * 1024 * 1024 - 8192)

    def test_stalled_stdout(self):
        import pty
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        proc = subprocess.Popen([str(EXECUTABLE), "serve"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        proc.stdin.write(json.dumps(dict(protocol=1, id="open", op="open",
                                        path=os.ttyname(slave), config=CONFIG)).encode() + b"\n")
        proc.stdin.flush()
        response = json.loads(proc.stdout.readline())
        self.assertTrue(response["ok"])
        os.set_blocking(master, False)
        end = time.monotonic() + 8
        while proc.poll() is None and time.monotonic() < end:
            try:
                os.write(master, b"x" * 4096)
            except BlockingIOError:
                pass
            time.sleep(.001)
        self.assertEqual(proc.wait(timeout=5), 3)
        self.assertEqual(json.loads(proc.stderr.read())["error"]["code"], "output_failed")
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()


if __name__ == "__main__":
    if "--executable" in sys.argv:
        at = sys.argv.index("--executable")
        EXECUTABLE = Path(sys.argv[at + 1])
        del sys.argv[at:at + 2]
    unittest.main()
