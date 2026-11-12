"""Tiny local web app: upload a disk image, get the CRT report. Standard library only.
Run: python -m legacyrescue.web [port]   then open http://127.0.0.1:8080"""
import cgi, json, os, sys, tempfile, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import inventory, runbook, report
from .report import CSS

MAX = 256 * 1024 * 1024
PAGE = """<!doctype html><meta charset=utf-8><title>Legacy Machine Rescue</title><style>%s</style>
<div class=wrap><div class=crt><h1>LEGACY MACHINE RESCUE</h1>
<div class=sub>Upload a disk image (raw floppy/hard disk, fixed VHD). Boot test runs in QEMU, offline, on a copy.</div>
<form method=post enctype=multipart/form-data>
<p><input type=file name=image required></p>
<p><label><input type=checkbox name=boot checked> Boot it in the emulator (about 30 seconds)</label></p>
<p><button>RUN</button></p></form>
<div class=warn>Emulator results do not prove real motion card, dongle, serial or CNC controller compatibility.</div>
<div class=sub>Your file is processed in a temporary folder and deleted afterwards.</div></div></div>""" % CSS


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        self._send(200, PAGE) if self.path == "/" else self._send(404, "not found", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX:
            return self._send(413, "image too large (limit 256 MB)", "text/plain")
        fs = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers["Content-Type"]})
        if "image" not in fs or not fs["image"].filename:
            return self._send(400, "no file", "text/plain")
        tmp = tempfile.mkdtemp(prefix="lmr-web-")
        try:
            path = os.path.join(tmp, "upload.img")
            with open(path, "wb") as f:
                shutil.copyfileobj(fs["image"].file, f)
            name = os.path.basename(fs["image"].filename)
            rep = inventory.inspect(path)
            rep["image"]["path"] = name
            rep["image"]["sha256"] = runbook.sha256(path)
            boot = None
            if "boot" in fs:
                from . import boot as bm
                boot = bm.run(path, os.path.join(tmp, "ev"), seconds=25)
                boot["emulator"] = boot["emulator"].split(" (")[0]
            md = runbook.build(rep, boot)
            out = os.path.join(tmp, "r.html")
            report.build(rep, boot, md, out)
            self._send(200, open(out).read())
        except (ValueError, OSError, RuntimeError) as e:
            self._send(400, "<pre>error: %s</pre>" % str(e).replace("<", "&lt;"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print("http://127.0.0.1:%d" % port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
