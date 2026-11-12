"""Single-file retro CRT report: real emulator frames + the runbook. No external assets."""
import base64, html, json, os, sys

CSS = """
:root{--g:#33ff66;--d:#0a1f10;--a:#ffb000}
*{box-sizing:border-box}body{margin:0;background:#050805;color:var(--g);font:16px/1.45 'Courier New',monospace}
.wrap{max-width:980px;margin:0 auto;padding:28px 18px}
.crt{position:relative;border:2px solid #1d5a2e;border-radius:18px;padding:22px;background:var(--d);box-shadow:0 0 40px #0f4a22 inset,0 0 30px #0b3d1b}
.crt:after{content:"";position:absolute;inset:0;border-radius:18px;pointer-events:none;background:repeating-linear-gradient(0deg,rgba(0,0,0,.22) 0 1px,transparent 1px 3px)}
h1{margin:0 0 4px;font-size:26px;letter-spacing:2px;text-shadow:0 0 8px var(--g)}h2{font-size:18px;color:var(--a);margin-top:28px;text-shadow:0 0 6px var(--a)}
.sub{opacity:.8;margin-bottom:18px}.badge{display:inline-block;border:1px solid var(--g);padding:1px 8px;margin-right:6px}
.warn{color:var(--a);border:1px solid var(--a);padding:8px 12px;margin:14px 0}
.frames{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
figure{margin:0}figure img{width:100%;image-rendering:pixelated;border:1px solid #1d5a2e;background:#000}
figcaption{font-size:13px;opacity:.85}pre{white-space:pre-wrap;background:#06140b;border:1px solid #1d5a2e;padding:12px;overflow:auto}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #1d5a2e;padding:5px 8px;text-align:left}
"""

def build(rep, boot, md, out):
    f = []
    for s in (boot or {}).get("screenshots", []):
        b = base64.b64encode(open(s["png"], "rb").read()).decode()
        f.append('<figure><img alt="emulator frame at %ds" src="data:image/png;base64,%s"><figcaption>Real emulator frame, t+%ds</figcaption></figure>' % (s["t"], b, s["t"]))
    v = rep["volumes"][0] if rep["volumes"] else None
    badge = "NOT RUN" if not boot else ("BOOTED IN EMULATOR" if boot["booted"] else "DID NOT BOOT")
    h = ["<!doctype html><meta charset=utf-8><title>Legacy Machine Rescue report</title><style>%s</style>" % CSS,
         '<div class=wrap><div class=crt><h1>LEGACY MACHINE RESCUE</h1><div class=sub>%s</div>' % html.escape(rep["image"]["path"].split("/")[-1]),
         '<span class=badge>%s</span><span class=badge>%s</span>' % (badge, html.escape(v["fs"] + ", " + str(v["files"]) + " files" if v else "no readable filesystem")),
         '<div class=warn>EMULATOR RESULT ONLY. This does not prove the real motion card, dongle, serial/DNC link or CNC controller will work.</div>']
    if boot:
        h.append("<h2>&gt; EMULATOR BOOT</h2><p>%s. %s</p><div class=frames>%s</div>" % (html.escape(boot["emulator"]), html.escape(boot["evidence"]), "".join(f)))
        last = boot["screenshots"][-1].get("text") if boot.get("screenshots") else ""
        h.append("<h2>&gt; SCREEN TEXT READ FROM THE GUEST</h2><pre>%s</pre>" % html.escape(last or ""))
    h.append("<h2>&gt; RECOVERY RUNBOOK</h2><pre>%s</pre></div></div>" % html.escape(md))
    open(out, "w").write("\n".join(h))
