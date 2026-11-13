import http.client
from contextlib import closing
import json
import os
import socket
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from legacyrescue import web
from tests import fixtures


def upload(data, filename="synthetic.img", extra=b""):
    return (b"--testboundary\r\nContent-Disposition: form-data; name=\"image\"; filename=\"" +
            filename.encode("utf-8") + b"\"\r\nContent-Type: application/octet-stream\r\n\r\n" +
            data + b"\r\n" + extra + b"--testboundary--\r\n")


class WebTests(unittest.TestCase):
    def setUp(self):
        self.server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.H)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method, path, body=None, headers=None):
        with closing(http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)) as c:
            c.request(method, path, body, headers or {})
            r = c.getresponse()
            return r.status, r.read(), dict(r.getheaders())

    def post(self, body, **headers):
        return self.request("POST", "/", body, {"Content-Type": "multipart/form-data; boundary=testboundary", **headers})

    def test_health_and_optional_boot_form(self):
        status, body, headers = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})
        self.assertEqual(headers["Content-Type"], "application/json")
        status, body, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertNotIn(b"name=boot checked", body)
        self.assertIn(b"Emulator boot is optional", body)
        self.assertEqual(self.request("GET", "/absent")[0], 404)

    def test_real_upload_returns_inventory_without_starting_emulator(self):
        data = fixtures.fat12_image({"DNC.EXE": b"synthetic", "FANUC.CFG": b"synthetic"})
        with patch("legacyrescue.boot.run", side_effect=AssertionError("not requested")):
            status, body, _ = self.post(upload(data))
        self.assertEqual(status, 200)
        self.assertIn(b"NOT RUN", body)
        self.assertIn(b"FAT12", body)
        self.assertIn(b"Needs real hardware check", body)
        self.assertIn(b"Before you touch the machine", body)
        self.assertNotIn(b"BOOTED IN EMULATOR", body)

    def test_oversize_is_rejected_from_headers_before_reading_body(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(("POST / HTTP/1.0\r\nContent-Length: %d\r\n\r\n" % (web.MAX + 1)).encode())
            self.assertIn(b"413", s.recv(4096).split(b"\r\n", 1)[0])

    def test_limit_boundary_and_small_limit(self):
        body = upload(fixtures.fat12_image({"PART.NC": b"synthetic"}))
        with patch.object(web, "MAX", len(body)):
            self.assertEqual(self.post(body)[0], 200)
        with patch.object(web, "MAX", len(body) - 1):
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
                s.sendall(("POST / HTTP/1.0\r\nContent-Length: %d\r\n\r\n" % len(body)).encode())
                self.assertIn(b"413", s.recv(4096).split(b"\r\n", 1)[0])

    def test_malformed_lengths_and_missing_length(self):
        cases = [(b"", 411), (b"Content-Length: bad\r\n", 400),
                 (b"Content-Length: -1\r\n", 400),
                 (b"Content-Length: 1\r\nContent-Length: 1\r\n", 400),
                 (b"Transfer-Encoding: chunked\r\n", 400)]
        for headers, expected in cases:
            with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
                s.sendall(b"POST / HTTP/1.0\r\n" + headers + b"\r\n")
                self.assertIn(str(expected).encode(), s.recv(4096).split(b"\r\n", 1)[0])

    def test_malformed_multipart_and_duplicate_images_are_rejected(self):
        for body in [b"not multipart", upload(b""), b"--testboundary--\r\n"]:
            self.assertEqual(self.post(body)[0], 400)
        one = upload(b"x")
        duplicate = one.replace(b"--testboundary--\r\n", one)
        self.assertEqual(self.post(duplicate)[0], 400)
        self.assertEqual(self.request("POST", "/wrong", b"x")[0], 404)

    def test_seeded_malformed_uploads_return_errors_not_tracebacks(self):
        import random
        rng = random.Random(61)
        for _ in range(25):
            body = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 150)))
            self.assertEqual(self.post(body)[0], 400)

    def test_module_startup_uses_port_env_and_health_on_python_current(self):
        env = {**os.environ, "PORT": "0"}
        process = subprocess.Popen([sys.executable, "-m", "legacyrescue.web"], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = process.stdout.readline().strip()
            self.assertTrue(line.startswith("http://127.0.0.1:"), line)
            port = int(line.rsplit(":", 1)[1])
            self.assertGreater(port, 0)
            with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as c:
                c.request("GET", "/health")
                response = c.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {"status": "ok"})
        finally:
            process.terminate()
            process.communicate(timeout=5)
