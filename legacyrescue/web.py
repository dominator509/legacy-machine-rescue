"""Tiny local web app: upload a disk image, get the CRT report. Standard library only.
Run: python -m legacyrescue.web [port]   then open http://127.0.0.1:8080"""
import html, json, os, sys, tempfile, shutil
from email.parser import BytesParser
from email import policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import inventory, runbook, report
from .report import CSS

MAX = 256 * 1024 * 1024
PAGE = """<!doctype html><meta charset=utf-8><title>Legacy Machine Rescue</title><style>%s</style>
<div class=wrap><div class=crt><h1>LEGACY MACHINE RESCUE</h1>
<div class=sub>Upload a disk image for inventory. Emulator boot is optional and requires local QEMU; inventory alone does not boot the image.</div>
<form method=post enctype=multipart/form-data>
<p><input type=file name=image required></p>
<p><label><input type=checkbox name=boot> Boot it in the emulator (about 30 seconds)</label></p>
<p><button>RUN</button></p></form>
<div class=warn>Emulator results do not prove real motion card, dongle, serial or CNC controller compatibility.</div>
<div class=sub>Your file is processed in a temporary folder and deleted afterwards.</div></div></div>""" % CSS


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, json.dumps({"status": "ok"}), "application/json")
        self._send(200, PAGE) if self.path == "/" else self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/":
            self.close_connection = True
            return self._send(404, "not found", "text/plain")
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return self._send(400, "transfer encoding is not supported", "text/plain")
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) > 1:
                raise ValueError("duplicate Content-Length")
            length = self.headers.get("Content-Length")
            if length is None:
                self.close_connection = True
                return self._send(411, "Content-Length required", "text/plain")
            n = int(length)
        except (ValueError, TypeError):
            self.close_connection = True
            return self._send(400, "invalid Content-Length", "text/plain")
        if n <= 0:
            self.close_connection = True
            return self._send(400, "empty or invalid upload length", "text/plain")
        if n > MAX:
            self.close_connection = True
            return self._send(413, "upload too large (request limit 256 MiB)", "text/plain")
        content_type = self.headers.get("Content-Type", "")
        try:
            header = ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode("ascii")
        except UnicodeEncodeError:
            self.close_connection = True
            return self._send(400, "invalid Content-Type", "text/plain")
        self.connection.settimeout(15)
        try:
            body = self.rfile.read(n)
        except OSError:
            self.close_connection = True
            return self._send(408, "upload timed out", "text/plain")
        if len(body) != n:
            self.close_connection = True
            return self._send(400, "truncated upload", "text/plain")
        message = BytesParser(policy=policy.default).parsebytes(header + body)
        if message.get_content_type() != "multipart/form-data" or not message.is_multipart() or message.defects:
            return self._send(400, "multipart/form-data upload required", "text/plain")
        parts = list(message.iter_parts())
        images = [part for part in parts if part.get_content_disposition() == "form-data"
                  and part.get_param("name", header="content-disposition") == "image"]
        if len(images) != 1 or not images[0].get_filename() or images[0].is_multipart() or images[0].defects:
            return self._send(400, "one image file required", "text/plain")
        image_data = images[0].get_payload(decode=True)
        if not image_data:
            return self._send(400, "empty image", "text/plain")
        name = images[0].get_filename().replace("\\", "/").rsplit("/", 1)[-1]
        wants_boot = any(part.get_param("name", header="content-disposition") == "boot"
                         and not part.is_multipart() for part in parts)
        tmp = tempfile.mkdtemp(prefix="lmr-web-")
        try:
            path = os.path.join(tmp, "upload.img")
            with open(path, "wb") as f:
                f.write(image_data)
            rep = inventory.inspect(path)
            rep["image"]["path"] = name
            rep["image"]["sha256"] = runbook.sha256(path)
            boot = None
            if wants_boot:
                from . import boot as bm
                boot = bm.run(path, os.path.join(tmp, "ev"), seconds=25)
                boot["emulator"] = boot["emulator"].split(" (")[0]
            md = runbook.build(rep, boot)
            out = os.path.join(tmp, "r.html")
            report.build(rep, boot, md, out)
            with open(out, encoding="utf-8") as f:
                self._send(200, f.read())
        except (ValueError, OSError, RuntimeError) as e:
            self._send(400, "<pre>error: %s</pre>" % html.escape(str(e)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8080"))
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    with ThreadingHTTPServer(("127.0.0.1", port), H) as server:
        print("http://127.0.0.1:%d" % server.server_port, flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
