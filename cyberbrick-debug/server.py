#!/usr/bin/env python3
"""Local CyberBrick code editor and runner."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
CURRENT_CODE = ROOT / "current_code.py"
DEFAULT_CODE = ROOT / "default_code.py"
DEFAULT_PORT = "/dev/cu.usbmodem101"
SERIAL_LOCK_PATH = Path(tempfile.gettempdir()) / "cyberbrick-usb-serial.lock"


def detect_serial_port() -> str:
    ports = sorted(Path("/dev").glob("cu.usbmodem*"))
    return str(ports[0]) if ports else DEFAULT_PORT


def resolve_serial_port(port: str) -> str:
    port = (port or "").strip() or detect_serial_port()
    if Path(port).exists():
        return port
    if Path(port).name.startswith("cu.usbmodem"):
        return detect_serial_port()
    return port

sys.path.insert(0, str(SRC))
from cyberbrick_led import clean_output, run_mpremote_repl_inject  # noqa: E402


def run_serial_repl(port: str, code: str, timeout: float = 12.0, retries: int = 1) -> tuple[int, str]:
    port = resolve_serial_port(port)
    last_returncode = 1
    last_output = ""
    for attempt in range(retries + 1):
        with SERIAL_LOCK_PATH.open("w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                returncode, output = run_mpremote_repl_inject(port, code, timeout=timeout)
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
        last_returncode, last_output = returncode, output
        busy = "failed to access" in output or "in use by another program" in output
        if returncode == 0 or not busy or attempt >= retries:
            return returncode, output
        time.sleep(0.7)
    return last_returncode, last_output


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Cyberbrick Code</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #1e1e1e;
      --panel: #252526;
      --panel-2: #181818;
      --line: #3c3c3c;
      --text: #d4d4d4;
      --muted: #969696;
      --accent: #007acc;
      --accent-hover: #1687d9;
      --error: #f48771;
      --ok: #89d185;
      --shadow: rgba(0, 0, 0, .28);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      overflow: hidden;
    }
    .app {
      height: 100vh;
      display: grid;
      grid-template-columns: 220px 1fr;
      grid-template-rows: 42px 1fr 172px 24px;
    }
    .titlebar {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 0 14px;
      border-bottom: 1px solid var(--line);
      background: #2d2d30;
      box-shadow: 0 1px 8px var(--shadow);
    }
    .brand { font-weight: 650; letter-spacing: .2px; }
    .port {
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
    }
    input {
      width: 240px;
      height: 28px;
      border: 1px solid var(--line);
      background: #1b1b1b;
      color: var(--text);
      padding: 0 9px;
      outline: none;
      border-radius: 3px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    input:focus, textarea:focus { border-color: var(--accent); }
    button {
      height: 28px;
      border: 1px solid transparent;
      background: #333;
      color: var(--text);
      padding: 0 11px;
      border-radius: 3px;
      cursor: pointer;
      font: inherit;
      white-space: nowrap;
    }
    button:hover { background: #3f3f46; }
    button.primary { background: var(--accent); color: white; }
    button.primary:hover { background: var(--accent-hover); }
    button:disabled { opacity: .55; cursor: wait; }
    .sidebar {
      grid-row: 2 / 4;
      border-right: 1px solid var(--line);
      background: var(--panel);
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    .section-title {
      height: 34px;
      display: flex;
      align-items: center;
      padding: 0 14px;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .7px;
    }
    .file, .example {
      display: flex;
      align-items: center;
      gap: 8px;
      height: 30px;
      padding: 0 14px;
      color: var(--text);
      border-left: 2px solid transparent;
    }
    .file { background: #37373d; border-left-color: var(--accent); }
    .example { border: 0; text-align: left; width: 100%; background: transparent; }
    .example:hover { background: #2a2d2e; }
    .main {
      grid-row: 2;
      min-width: 0;
      display: grid;
      grid-template-rows: 34px 1fr;
      background: var(--bg);
    }
    .tab {
      display: flex;
      align-items: center;
      padding: 0 12px;
      width: fit-content;
      min-width: 170px;
      border-right: 1px solid var(--line);
      background: #1e1e1e;
      color: #fff;
    }
    .editor-wrap {
      min-height: 0;
      display: grid;
      grid-template-columns: 54px 1fr;
      border-top: 1px solid var(--line);
    }
    .gutter, textarea {
      font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .gutter {
      overflow: hidden;
      padding: 12px 10px 12px 0;
      text-align: right;
      color: #858585;
      background: #1e1e1e;
      user-select: none;
      white-space: pre;
    }
    textarea {
      width: 100%;
      height: 100%;
      resize: none;
      border: 0;
      outline: none;
      padding: 12px 18px;
      background: #1e1e1e;
      color: var(--text);
      tab-size: 4;
      white-space: pre;
      overflow: auto;
    }
    .output {
      grid-column: 2;
      border-top: 1px solid var(--line);
      background: var(--panel-2);
      display: grid;
      grid-template-rows: 34px 1fr;
      min-height: 0;
    }
    .output-head {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 12px;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
    }
    .spacer { flex: 1; }
    pre {
      margin: 0;
      padding: 10px 12px;
      overflow: auto;
      color: #c9d1d9;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: pre-wrap;
    }
    .status {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      padding: 0 10px;
      background: var(--accent);
      color: white;
      font-size: 12px;
    }
    .dirty { color: #e5c07b; }
    .success { color: var(--ok); }
    .failure { color: var(--error); }
    @media (max-width: 760px) {
      .app { grid-template-columns: 1fr; grid-template-rows: auto auto 1fr 172px 24px; }
      .sidebar { grid-row: 2; border-right: 0; border-bottom: 1px solid var(--line); }
      .main { grid-row: 3; }
      .output { grid-column: 1; grid-row: 4; }
      .titlebar { flex-wrap: wrap; height: auto; min-height: 76px; padding: 8px; }
      .port { margin-left: 0; width: 100%; }
      input { flex: 1; min-width: 0; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="titlebar">
      <div class="brand">Cyberbrick</div>
      <button id="run" class="primary">运行</button>
      <button id="save">保存</button>
      <button id="reset">恢复默认</button>
      <div class="port">
        <span>串口</span>
        <input id="port" spellcheck="false" />
      </div>
    </header>
    <aside class="sidebar">
      <div class="section-title">Explorer</div>
      <div class="file">current_code.py</div>
      <div class="section-title">Examples</div>
      <button class="example" id="loadDefault">LED2 红灯示例</button>
    </aside>
    <main class="main">
      <div class="tab">current_code.py <span id="dirty" class="dirty"></span></div>
      <div class="editor-wrap">
        <div id="gutter" class="gutter">1</div>
        <textarea id="code" spellcheck="false"></textarea>
      </div>
    </main>
    <section class="output">
      <div class="output-head">
        <span>输出</span>
        <span id="resultState"></span>
        <span class="spacer"></span>
        <button id="clear">清空</button>
      </div>
      <pre id="output">准备就绪。</pre>
    </section>
    <footer class="status" id="status">Ready</footer>
  </div>

  <script>
    const $ = (id) => document.getElementById(id);
    const code = $("code");
    const gutter = $("gutter");
    const output = $("output");
    const status = $("status");
    const resultState = $("resultState");
    const dirtyMark = $("dirty");
    let savedCode = "";

    function setStatus(text) { status.textContent = text; }
    function setDirty() {
      const dirty = code.value !== savedCode;
      dirtyMark.textContent = dirty ? " *" : "";
    }
    function updateGutter() {
      const count = Math.max(1, code.value.split("\\n").length);
      gutter.textContent = Array.from({ length: count }, (_, i) => i + 1).join("\\n");
    }
    function syncScroll() { gutter.scrollTop = code.scrollTop; }
    async function api(path, options = {}) {
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "请求失败");
      return data;
    }
    async function loadCode() {
      const data = await api("/api/code");
      code.value = data.code;
      savedCode = data.code;
      $("port").value = data.port;
      updateGutter();
      setDirty();
      setStatus("Ready");
    }
    async function saveCode() {
      await api("/api/code", { method: "POST", body: JSON.stringify({ code: code.value }) });
      savedCode = code.value;
      setDirty();
      setStatus("Saved");
    }
    async function runCode() {
      const buttons = document.querySelectorAll("button");
      buttons.forEach((button) => button.disabled = true);
      resultState.textContent = "";
      resultState.className = "";
      output.textContent = "运行中...";
      setStatus("Running");
      try {
        const data = await api("/api/run", {
          method: "POST",
          body: JSON.stringify({ code: code.value, port: $("port").value })
        });
        savedCode = code.value;
        setDirty();
        output.textContent = data.output || "完成。";
        resultState.textContent = data.ok ? "成功" : "失败";
        resultState.className = data.ok ? "success" : "failure";
        setStatus(data.ok ? "Done" : "Failed");
      } catch (err) {
        output.textContent = String(err.message || err);
        resultState.textContent = "失败";
        resultState.className = "failure";
        setStatus("Failed");
      } finally {
        buttons.forEach((button) => button.disabled = false);
      }
    }
    async function resetDefault() {
      const data = await api("/api/reset", { method: "POST" });
      code.value = data.code;
      savedCode = data.code;
      updateGutter();
      setDirty();
      output.textContent = "已恢复默认示例。";
      resultState.textContent = "";
      setStatus("Default restored");
    }
    async function loadDefaultExample() {
      const data = await api("/api/default");
      code.value = data.code;
      updateGutter();
      setDirty();
      setStatus("Example loaded");
    }

    code.addEventListener("input", () => { updateGutter(); setDirty(); });
    code.addEventListener("scroll", syncScroll);
    code.addEventListener("keydown", (event) => {
      if (event.key === "Tab") {
        event.preventDefault();
        const start = code.selectionStart;
        const end = code.selectionEnd;
        code.value = code.value.slice(0, start) + "    " + code.value.slice(end);
        code.selectionStart = code.selectionEnd = start + 4;
        updateGutter();
        setDirty();
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveCode();
      }
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        runCode();
      }
    });
    $("run").addEventListener("click", runCode);
    $("save").addEventListener("click", saveCode);
    $("reset").addEventListener("click", resetDefault);
    $("loadDefault").addEventListener("click", loadDefaultExample);
    $("clear").addEventListener("click", () => output.textContent = "");
    loadCode().catch((err) => output.textContent = String(err.message || err));
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/code":
            self.send_json({"code": CURRENT_CODE.read_text(encoding="utf-8"), "port": detect_serial_port()})
        elif path == "/api/default":
            self.send_json({"code": DEFAULT_CODE.read_text(encoding="utf-8")})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/code":
                data = self.read_json()
                CURRENT_CODE.write_text(data.get("code", ""), encoding="utf-8")
                self.send_json({"ok": True})
            elif path == "/api/reset":
                code = DEFAULT_CODE.read_text(encoding="utf-8")
                CURRENT_CODE.write_text(code, encoding="utf-8")
                self.send_json({"ok": True, "code": code})
            elif path == "/api/run":
                data = self.read_json()
                code = data.get("code", "")
                port = resolve_serial_port(data.get("port") or DEFAULT_PORT)
                CURRENT_CODE.write_text(code, encoding="utf-8")
                returncode, raw_output = run_serial_repl(port, code)
                output = clean_output(raw_output.encode("utf-8"))
                self.send_json(
                    {
                        "ok": returncode == 0,
                        "returncode": returncode,
                        "output": output or ("完成。" if returncode == 0 else "运行失败。"),
                    }
                )
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json(
                {"ok": False, "error": f"{exc}\n{traceback.format_exc()}"},
                500,
            )


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 8768), Handler)
    print("Cyberbrick editor: http://127.0.0.1:8768", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
