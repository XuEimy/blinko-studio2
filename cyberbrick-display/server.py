#!/usr/bin/env python3
"""CyberBrick saved-file uploader."""

from __future__ import annotations

import json
import os
import pty
import random
import re
import select
import signal
import shutil
import subprocess
import tempfile
import textwrap
import threading
import time
import traceback
import uuid
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
FILES_DIR = ROOT / "files"
PROGRAMS_DIR = ROOT / "py"
ASSETS_DIR = ROOT / "assets"
WEB_ASSETS_DIR = ASSETS_DIR / "web"
SLOTS_FILE = ROOT / "slots.json"
SLOT_COUNT = 6
WORK_MODE_PROGRAMS = ("fin.py", "swing2.py", "noding.py")
WORK_MODE_WEIGHTS = {"fin.py": 0.3, "swing2.py": 0.5, "noding.py": 0.2}
DEFAULT_PORT = "/dev/cu.usbmodem101"
SERIAL_LOCK_PATH = Path(tempfile.gettempdir()) / "cyberbrick-usb-serial.lock"
UPLOAD_JOBS: dict[str, dict] = {}
UPLOAD_LOCK = threading.Lock()

KNOWN_MPREMOTE_PATHS = (
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/mpremote",
    "/opt/homebrew/bin/mpremote",
    "/usr/local/bin/mpremote",
)


def find_mpremote() -> str:
    found = shutil.which("mpremote")
    if found:
        return found
    for path in KNOWN_MPREMOTE_PATHS:
        if Path(path).exists():
            return path
    raise RuntimeError("找不到 mpremote。请先安装：python3 -m pip install mpremote")


def clean_output(output: bytes) -> str:
    text = output.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"Traceback \(most recent call last\):.*?KeyboardInterrupt:\s*", "", text, flags=re.S)
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        if line.startswith("Connected to MicroPython"):
            continue
        if line.startswith("Use Ctrl-]") or line.startswith("Use Ctrl-J"):
            continue
        if line.startswith("paste mode;") or line.startswith("raw REPL;"):
            continue
        if line in {">>>", ">", "==="}:
            continue
        lines.append(line)
    return "\n".join(line for line in lines if line.strip()).strip()


def normalize_serial_port(port: str) -> str:
    port = (port or DEFAULT_PORT).strip()
    if port.startswith("/dev/tty."):
        cu_port = "/dev/cu." + port[len("/dev/tty."):]
        if Path(cu_port).exists():
            return cu_port
    if port.startswith("/dev/cu.usbmodem") and not Path(port).exists():
        return detect_serial_port()
    return port


def detect_serial_port() -> str:
    ports = sorted(Path("/dev").glob("cu.usbmodem*"))
    return str(ports[0]) if ports else DEFAULT_PORT


def run_serial_repl(port: str, code: str, timeout: float, progress=None, retries: int = 1) -> tuple[int, str]:
    port = normalize_serial_port(port)
    last_returncode = 1
    last_output = ""
    for attempt in range(retries + 1):
        if progress and attempt:
            progress(4, "串口刚被占用，重试连接")
        with SERIAL_LOCK_PATH.open("w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                returncode, output = run_mpremote_repl_inject(port, code, timeout=timeout, progress=progress)
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
        last_returncode, last_output = returncode, output
        busy = "failed to access" in output or "in use by another program" in output
        if returncode == 0 or not busy or attempt >= retries:
            return returncode, output
        time.sleep(0.7)
    return last_returncode, last_output


def run_mpremote(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [find_mpremote(), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=timeout,
    )


def run_mpremote_repl_inject(port: str, code: str, timeout: float = 30.0, progress=None) -> tuple[int, str]:
    def report(percent: int, message: str) -> None:
        if progress:
            progress(max(0, min(100, percent)), message)

    mpremote = find_mpremote()
    report(2, "启动 mpremote")
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        [mpremote, "connect", port, "repl"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    def stop_proc() -> None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            proc.terminate()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass

    report(8, "连接串口")
    output = bytearray()
    started_at = time.monotonic()
    stage = "connect"
    stage_started_at = started_at
    exit_sent = False

    try:
        while True:
            now = time.monotonic()
            if now - started_at > timeout:
                stop_proc()
                return 124, clean_output(bytes(output)) + "\n超时：没有等到 CyberBrick 返回结果。"

            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if chunk:
                    output.extend(chunk)

            now = time.monotonic()
            text = output.decode("utf-8", errors="replace")
            if stage == "connect" and ("Use Ctrl-]" in text or "raw REPL" in text or ">>>" in text or ">" in text):
                os.write(master_fd, b"\x02")
                time.sleep(0.2)
                os.write(master_fd, b"\x03")
                time.sleep(0.2)
                os.write(master_fd, b"\x03")
                stage = "waiting_prompt"
                stage_started_at = now
                report(18, "已连接，停止板子当前程序")

            if stage == "waiting_prompt" and (">>>" in text or now - stage_started_at > 1.2):
                os.write(master_fd, b"\x05")
                time.sleep(0.15)
                payload = code.replace("\n", "\r\n").encode("utf-8")
                sent = 0
                total = max(1, len(payload))
                for offset in range(0, len(payload), 512):
                    part = payload[offset:offset + 512]
                    os.write(master_fd, part)
                    sent += len(part)
                    report(25 + int(sent / total * 55), f"发送代码 {sent}/{total} bytes")
                    time.sleep(0.015)
                os.write(master_fd, b"\r\n\x04")
                stage = "injected"
                stage_started_at = now
                report(82, "代码已发送，等待主板写入")

            if stage == "injected" and not exit_sent and time.monotonic() - stage_started_at > 3.0:
                os.write(master_fd, b"\x18")
                exit_sent = True
                report(94, "等待主板重启")

            if exit_sent and proc.poll() is not None:
                break

        if proc.poll() is None:
            stop_proc()
        cleaned = clean_output(bytes(output))
        if "Traceback (most recent call last)" in cleaned or "Error:" in cleaned:
            return 1, cleaned
        report(100, "完成")
        return proc.returncode or 0, cleaned
    finally:
        stop_proc()
        try:
            os.close(master_fd)
        except OSError:
            pass


def make_upload_script(code: str) -> str:
    boot_code = textwrap.dedent(
        """\
        # -*-coding:utf-8-*-
        # CYBERBRICK_USER_BOOT_WRAPPER
        import sys
        sys.path.append('/app')
        sys.path.append('/bbl')

        try:
            import cyberbrick_user
        except Exception as error:
            print('CYBERBRICK_USER_BOOT_ERROR:', repr(error))
            import rc_main
            rc_main.main()
        """
    )

    def append_write_file(lines: list[str], filename: str, content: str) -> None:
        chunks = [content[index:index + 1400] for index in range(0, len(content), 1400)]
        lines.append("f = open(%r, 'w')" % filename)
        for chunk in chunks:
            lines.append("f.write(%r)" % chunk)
        lines.append("f.close()")

    script_lines = ["import os"]
    append_write_file(script_lines, "cyberbrick_user.py", code)
    script_lines.extend(
        [
            "write_boot = True",
            "try:",
            "    write_boot = 'CYBERBRICK_USER_BOOT_WRAPPER' not in open('boot.py').read()",
            "except Exception:",
            "    pass",
            "if write_boot:",
            "    try:",
            "        os.stat('boot_original.py')",
            "    except Exception:",
            "        try:",
            "            src = open('boot.py').read()",
            "            dst = open('boot_original.py', 'w')",
            "            dst.write(src)",
            "            dst.close()",
            "        except Exception as backup_error:",
            "            print('BOOT_BACKUP_ERROR', repr(backup_error))",
        ]
    )
    boot_lines: list[str] = []
    append_write_file(boot_lines, "boot.py", boot_code)
    script_lines.extend("    " + line for line in boot_lines)
    script_lines.extend(["print('CYBERBRICK_UPLOAD_OK')", "import machine", "machine.reset()"])
    return "\n".join(script_lines)


def upload_code(port: str, code: str, progress=None) -> tuple[bool, str]:
    returncode, output = run_serial_repl(
        normalize_serial_port(port),
        make_upload_script(code),
        timeout=35,
        progress=progress,
        retries=1,
    )
    ok = returncode == 0 and "CYBERBRICK_UPLOAD_OK" in output
    if ok:
        return True, "已上传并重启 CyberBrick。\n\n" + output
    return False, output or "上传失败。"


def check_usb(port: str) -> tuple[bool, str]:
    returncode, output = run_serial_repl(
        normalize_serial_port(port),
        "print('CYBERBRICK_USB_OK')",
        timeout=12,
        retries=1,
    )
    ok = returncode == 0 and "CYBERBRICK_USB_OK" in output
    if ok:
        return True, "USB 串口连接正常。\n\n" + output
    return False, "USB 串口连接失败。\n\n" + (output or "没有返回内容。")


def safe_name(name: str) -> str:
    name = Path(name or "program.py").name.strip() or "program.py"
    name = re.sub(r"[^A-Za-z0-9_.\\-\\u4e00-\\u9fff]", "_", name)
    return name if name.endswith(".py") else f"{name}.py"


def list_files() -> list[dict]:
    FILES_DIR.mkdir(exist_ok=True)
    items = []
    for path in sorted(FILES_DIR.glob("*.py"), key=lambda p: p.name.lower()):
        stat = path.stat()
        items.append({"name": path.name, "bytes": stat.st_size, "mtime": stat.st_mtime})
    return items


def list_programs() -> list[dict]:
    PROGRAMS_DIR.mkdir(exist_ok=True)
    items = []
    for name in WORK_MODE_PROGRAMS:
        path = PROGRAMS_DIR / name
        if not path.exists():
            continue
        stat = path.stat()
        items.append({"name": path.name, "bytes": stat.st_size, "mtime": stat.st_mtime})
    return items


def gif_duration_ms(path: Path) -> int:
    data = path.read_bytes()
    total = 0
    if len(data) < 13 or data[:3] != b"GIF":
        return 1400

    index = 13
    packed = data[10]
    if packed & 0x80:
        index += 3 * (2 ** ((packed & 0x07) + 1))

    def skip_sub_blocks(pos: int) -> int:
        while pos < len(data):
            size = data[pos]
            pos += 1
            if size == 0:
                return pos
            pos += size
        return pos

    while index < len(data):
        block = data[index]
        index += 1
        if block == 0x3B:
            break
        if block == 0x21:
            if index >= len(data):
                break
            label = data[index]
            index += 1
            if label == 0xF9 and index < len(data):
                size = data[index]
                index += 1
                if size == 4 and index + 4 <= len(data):
                    delay = int.from_bytes(data[index + 1:index + 3], "little") * 10
                    total += delay or 100
                    index += 4
                    if index < len(data) and data[index] == 0:
                        index += 1
                else:
                    index += size
                    if index < len(data) and data[index] == 0:
                        index += 1
            else:
                index = skip_sub_blocks(index)
        elif block == 0x2C:
            if index + 9 > len(data):
                break
            descriptor = data[index:index + 9]
            index += 9
            if descriptor[8] & 0x80:
                index += 3 * (2 ** ((descriptor[8] & 0x07) + 1))
            if index >= len(data):
                break
            index += 1
            index = skip_sub_blocks(index)
        else:
            break
    return total or 1400


def list_faces() -> list[dict]:
    faces = []
    source_dir = WEB_ASSETS_DIR if WEB_ASSETS_DIR.exists() else ASSETS_DIR
    for path in sorted(source_dir.glob("face_*.gif"), key=lambda p: p.name):
        match = re.search(r"face_(\d+)\.gif$", path.name)
        if not match:
            continue
        slot = int(match.group(1))
        png = source_dir / f"face_{slot}.png"
        if png.exists():
            faces.append({"slot": slot, "durationMs": gif_duration_ms(path)})
    return faces


def load_slots_meta() -> dict:
    if not SLOTS_FILE.exists():
        return {}
    try:
        return json.loads(SLOTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_slots_meta(meta: dict) -> None:
    SLOTS_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def slot_path(slot: int) -> Path:
    if slot < 1 or slot > SLOT_COUNT:
        raise ValueError("行号不正确。")
    FILES_DIR.mkdir(exist_ok=True)
    return FILES_DIR / f"slot_{slot}.py"


def list_slots() -> list[dict]:
    meta = load_slots_meta()
    slots = []
    for slot in range(1, SLOT_COUNT + 1):
        path = slot_path(slot)
        item = {"slot": slot, "name": "", "bytes": 0, "exists": False}
        if path.exists():
            stat = path.stat()
            item.update(
                {
                    "name": meta.get(str(slot), {}).get("name") or path.name,
                    "bytes": stat.st_size,
                    "exists": True,
                }
            )
        slots.append(item)
    return slots


def set_upload_job(job_id: str, **updates) -> None:
    with UPLOAD_LOCK:
        UPLOAD_JOBS.setdefault(job_id, {}).update(updates)


def start_upload_job(port: str, filename: str | None = None, slot: int | None = None) -> str:
    if slot is not None:
        path = slot_path(slot)
    else:
        path = FILES_DIR / safe_name(filename or "")
    if not path.exists():
        raise FileNotFoundError("这一行还没有选择 .py 文件。")
    code = path.read_text(encoding="utf-8")
    job_id = uuid.uuid4().hex
    set_upload_job(job_id, ok=None, done=False, percent=0, message="准备上传", output="", filename=path.name)

    def progress(percent: int, message: str) -> None:
        set_upload_job(job_id, percent=percent, message=message)

    def worker() -> None:
        try:
            ok, output = upload_code(port, code, progress=progress)
            set_upload_job(
                job_id,
                ok=ok,
                done=True,
                percent=100 if ok else UPLOAD_JOBS.get(job_id, {}).get("percent", 0),
                message="上传完成" if ok else "上传失败",
                output=output,
            )
        except Exception as exc:
            set_upload_job(job_id, ok=False, done=True, message="上传失败", output=f"{exc}\n{traceback.format_exc()}")

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def start_program_upload_job(port: str, path: Path) -> str:
    if path.parent.resolve() != PROGRAMS_DIR.resolve() or path.suffix.lower() != ".py":
        raise ValueError("只能上传 py 文件夹里的 .py 文件。")
    if not path.exists():
        raise FileNotFoundError("没有找到这个 .py 文件。")
    code = path.read_text(encoding="utf-8")
    job_id = uuid.uuid4().hex
    set_upload_job(job_id, ok=None, done=False, percent=0, message="准备上传", output="", filename=path.name)

    def progress(percent: int, message: str) -> None:
        set_upload_job(job_id, percent=percent, message=message)

    def worker() -> None:
        try:
            ok, output = upload_code(port, code, progress=progress)
            set_upload_job(
                job_id,
                ok=ok,
                done=True,
                percent=100 if ok else UPLOAD_JOBS.get(job_id, {}).get("percent", 0),
                message="上传完成" if ok else "上传失败",
                output=output,
            )
        except Exception as exc:
            set_upload_job(job_id, ok=False, done=True, message="上传失败", output=f"{exc}\n{traceback.format_exc()}")

    threading.Thread(target=worker, daemon=True).start()
    return job_id


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>CyberBrick 展示上传</title>
  <style>
    :root {
      --bg: #f6efe3;
      --panel: rgba(255, 252, 246, .92);
      --line: #e4d4bd;
      --text: #171411;
      --muted: #786c5d;
      --accent: #dd8b33;
      --ok: #48a048;
      --bad: #c44d3d;
      --soft: #fbf2e5;
      --orange: #ee9656;
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; min-height: 100%; color: var(--text); font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body {
      background:
        radial-gradient(circle at 84% 10%, rgba(237, 150, 72, .26), transparent 28%),
        radial-gradient(circle at 10% 88%, rgba(80, 145, 255, .12), transparent 22%),
        linear-gradient(180deg, #f8f3ea 0%, #f4eadc 100%);
    }
    .app { width: min(1320px, calc(100vw - 24px)); margin: 18px auto; display: grid; gap: 14px; }
    header, section { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 8px 26px rgba(80, 54, 24, .08); backdrop-filter: blur(8px); }
    header { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; padding: 16px 22px; }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand-mark { width: 58px; height: 38px; border: 4px solid #111; border-radius: 14px; position: relative; background: #fffaf2; }
    .brand-mark::before { content: ""; position: absolute; left: 20px; top: 3px; bottom: 3px; width: 4px; background: #111; border-radius: 3px; }
    .brand-mark::after { content: ""; position: absolute; right: 10px; top: 12px; width: 7px; height: 7px; background: #111; border-radius: 50%; }
    .brand-eye { position: absolute; left: 9px; top: 13px; width: 10px; height: 7px; border-top: 3px solid #111; border-radius: 50%; }
    h1 { margin: 0; font-size: 34px; letter-spacing: 0; font-weight: 650; }
    .spacer { flex: 1; }
    .check-button { min-width: 132px; color: var(--bad); border-color: rgba(196, 77, 61, .45); background: rgba(255,255,255,.65); }
    .check-button.ok { color: var(--ok); border-color: rgba(72, 160, 72, .5); }
    .check-button.bad { color: var(--bad); border-color: rgba(196, 77, 61, .45); }
    section { display: grid; gap: 12px; }
    label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; font-weight: 700; }
    input { height: 36px; border: 1px solid var(--line); border-radius: 6px; padding: 0 10px; font: 13px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    input[type="file"] { padding: 6px 10px; font: 13px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    button { min-height: 36px; border: 1px solid var(--line); border-radius: 8px; background: white; color: var(--text); padding: 0 12px; font: inherit; font-weight: 750; cursor: pointer; }
    button:hover { background: #fff8ef; }
    button:disabled { opacity: .55; cursor: wait; }
    .slots-panel { padding: 12px; }
    .slots { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; position: relative; }
    .slots::before {
      content: "";
      position: absolute;
      left: 22px;
      top: 22px;
      width: 80px;
      height: 80px;
      background-image: radial-gradient(#e9a64c 2px, transparent 2px);
      background-size: 13px 13px;
      opacity: .55;
      pointer-events: none;
    }
    .slot {
      display: grid;
      padding: 0;
      border: 1px solid rgba(124, 94, 61, .32);
      border-radius: 8px;
      background:
        linear-gradient(180deg, rgba(255,255,255,.96), rgba(255,250,243,.94));
      min-width: 0;
      min-height: 0;
      box-shadow: 0 8px 24px rgba(91, 61, 28, .08);
      position: relative;
      overflow: hidden;
    }
    .slot::after {
      content: "";
      position: absolute;
      right: -24px;
      bottom: -24px;
      width: 92px;
      height: 92px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(238,150,86,.18), transparent 64%);
      pointer-events: none;
    }
    .gif-box {
      aspect-ratio: 896 / 635;
      border-radius: 24px;
      background: #fff8ec;
      display: grid;
      place-items: center;
      overflow: hidden;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.8);
    }
    .gif-box img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
      border-radius: inherit;
    }
    .pill {
      height: 34px;
      border: 1px dashed var(--orange);
      border-radius: 999px;
      background: rgba(255, 248, 239, .95);
      color: #e48748;
      display: grid;
      place-items: center;
      padding: 0 12px;
      font-weight: 800;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      box-shadow: 0 2px 8px rgba(195, 113, 52, .07);
    }
    .intro {
      position: absolute;
      left: 12px;
      top: 12px;
      z-index: 2;
      letter-spacing: .03em;
      width: auto;
      max-width: calc(100% - 24px);
      box-shadow: 0 8px 20px rgba(32, 20, 10, .12);
    }
    .intro.finish { color: #4f8f46; border-color: rgba(79,143,70,.55); background: rgba(241,248,237,.95); }
    .intro.standing { color: #6b77aa; border-color: rgba(107,119,170,.5); background: rgba(243,245,255,.95); }
    .intro.working { color: #d9822b; border-color: rgba(217,130,43,.55); background: rgba(255,246,234,.95); }
    .intro.error { color: #b84a3d; border-color: rgba(184,74,61,.55); background: rgba(255,240,237,.95); }
    .intro.pending { color: #8a6bb7; border-color: rgba(138,107,183,.55); background: rgba(247,241,255,.95); }
    .intro.sleeping { color: #597486; border-color: rgba(89,116,134,.55); background: rgba(240,247,250,.95); }
    .mode-panel {
      display: grid;
      place-items: center;
      position: relative;
      min-height: 92px;
      padding: 18px;
      overflow: hidden;
    }
    .mode-button {
      min-width: min(520px, 100%);
      min-height: 62px;
      border-color: rgba(221, 139, 51, .55);
      background: #fff4e5;
      color: #a95d18;
      padding: 0 30px;
      font-size: 22px;
      letter-spacing: 0;
      box-shadow: 0 12px 26px rgba(171, 98, 32, .13);
      transition: transform .16s ease, background .16s ease, box-shadow .16s ease;
    }
    .mode-label {
      background: linear-gradient(90deg, #ff4d6d, #ff9f1c, #ffd166, #2ec4b6, #3a86ff, #8338ec);
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
      -webkit-text-fill-color: transparent;
      font-weight: 900;
    }
    .mode-button:hover { background: #ffecd1; transform: translateY(-1px); }
    .mode-button:disabled { opacity: 1; cursor: wait; }
    .mode-button.is-counting {
      transform: scale(1.04);
      background: #ffe3bd;
      box-shadow: 0 16px 32px rgba(171, 98, 32, .18);
    }
    .mode-button.is-counting .mode-label {
      background: none;
      color: #a95d18;
      -webkit-text-fill-color: currentColor;
    }
    .fireworks {
      position: absolute;
      inset: 0;
      pointer-events: none;
      overflow: hidden;
    }
    .spark {
      position: absolute;
      left: 50%;
      top: 50%;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--spark-color, #ee9656);
      transform: translate(-50%, -50%);
      animation: spark .72s ease-out forwards;
    }
    @keyframes spark {
      0% { opacity: 1; transform: translate(-50%, -50%) scale(.65); }
      100% { opacity: 0; transform: translate(calc(-50% + var(--x)), calc(-50% + var(--y))) scale(.1); }
    }
    .codex-panel {
      display: grid;
      grid-template-rows: minmax(140px, 210px) auto;
      gap: 10px;
      padding: 14px;
    }
    .codex-history {
      min-height: 140px;
      max-height: 210px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, .74);
      padding: 12px 14px;
      color: var(--text);
    }
    .history-empty {
      color: #9c8d7b;
    }
    .message {
      padding: 7px 0;
      border-bottom: 1px solid rgba(228, 212, 189, .55);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .message:last-child { border-bottom: 0; }
    .message-meta {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 2px;
    }
    .codex-form {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 42px;
      align-items: center;
      gap: 10px;
      min-height: 54px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: rgba(255, 255, 255, .78);
      padding: 8px 8px 8px 14px;
    }
    .codex-input {
      width: 100%;
      min-width: 0;
      height: auto;
      border: 0;
      outline: 0;
      background: transparent;
      padding: 0;
      color: var(--text);
      font: 15px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .codex-input::placeholder { color: #9c8d7b; }
    .codex-send {
      width: 38px;
      height: 38px;
      min-height: 38px;
      border-color: rgba(221, 139, 51, .45);
      background: #fff5e8;
      display: grid;
      place-items: center;
      padding: 0;
    }
    .codex-send:hover { background: #ffefdc; transform: translateY(-1px); }
    .codex-send svg {
      width: 18px;
      height: 18px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2.2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .ok { color: var(--ok); }
    .bad { color: var(--bad); }
    @media (max-width: 980px) {
      .slots { grid-template-columns: 1fr; }
      button { width: 100%; }
      h1 { font-size: 28px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <div class="brand">
        <div class="brand-mark"><div class="brand-eye"></div></div>
        <h1>blinko</h1>
      </div>
      <span class="spacer"></span>
      <button id="checkConnection" class="check-button bad">未连接</button>
    </header>
    <section class="slots-panel">
      <div id="slots" class="slots"></div>
    </section>
    <section class="mode-panel">
      <button id="chooseMode" class="mode-button"><span class="mode-label">Choose Your Work Mode</span></button>
      <div id="fireworks" class="fireworks" aria-hidden="true"></div>
    </section>
    <section class="codex-panel" aria-label="Codex input">
      <div class="codex-history" id="codexHistory">
        <div class="history-empty">输入的文字会显示在这里。</div>
      </div>
      <form class="codex-form" id="codexForm">
        <input class="codex-input" id="codexInput" type="text" autocomplete="off" placeholder="Message Codex" />
        <button class="codex-send" type="submit" aria-label="Send">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 19V5"></path>
            <path d="m5 12 7-7 7 7"></path>
          </svg>
        </button>
      </form>
    </section>
  </div>
  <script>
    const $ = (id) => document.getElementById(id);
    let faces = [];
    let playingTimer = null;
    let resetTimer = null;
    let playbackGeneration = 0;
    let animationDurationMs = 1400;
    let restDelayMs = 0;
    let lastPlayedSlots = [];

    function setConnectionState(state) {
      const button = $("checkConnection");
      button.classList.remove("ok", "bad");
      if (state === "ok") {
        button.classList.add("ok");
        button.textContent = "连接 OK";
      } else if (state === "bad") {
        button.classList.add("bad");
        button.textContent = "未连接";
      } else if (state === "checking") {
        button.textContent = "检测中";
      } else {
        button.textContent = "检测连接";
      }
    }
    function log(text) {
      if (text) console.log(text);
    }
    function setButtons(disabled) { document.querySelectorAll("button").forEach((button) => button.disabled = disabled); }
    function setProgress(percent, text) {
      log(`${text || "上传中"} ${Math.round(Math.max(0, Math.min(100, Number(percent) || 0)))}%`);
    }
    function sleep(ms) {
      return new Promise((resolve) => window.setTimeout(resolve, ms));
    }
    function setModeButtonText(text) {
      const label = $("chooseMode").querySelector(".mode-label");
      label.textContent = text;
    }
    function launchFireworks() {
      const container = $("fireworks");
      const colors = ["#ee9656", "#dd8b33", "#4f8f46", "#6b77aa", "#d9822b", "#8a6bb7"];
      container.innerHTML = "";
      for (let index = 0; index < 34; index += 1) {
        const spark = document.createElement("span");
        const angle = (Math.PI * 2 * index) / 34;
        const radius = 42 + Math.random() * 70;
        spark.className = "spark";
        spark.style.setProperty("--x", `${Math.cos(angle) * radius}px`);
        spark.style.setProperty("--y", `${Math.sin(angle) * radius}px`);
        spark.style.setProperty("--spark-color", colors[index % colors.length]);
        spark.style.left = `${42 + Math.random() * 16}%`;
        spark.style.top = `${42 + Math.random() * 16}%`;
        container.appendChild(spark);
      }
      window.setTimeout(() => {
        container.innerHTML = "";
      }, 900);
    }
    async function runModeAnimation() {
      const button = $("chooseMode");
      button.classList.add("is-counting");
      for (const text of ["5", "4", "3", "2", "1"]) {
        setModeButtonText(text);
        await sleep(1000);
      }
      setModeButtonText("Choose Your Work Mode");
      launchFireworks();
      await sleep(820);
      button.classList.remove("is-counting");
    }
    function sendCodexMessage() {
      const input = $("codexInput");
      const message = input.value.trim();
      if (!message) {
        input.value = "";
        return;
      }
      const history = $("codexHistory");
      const empty = history.querySelector(".history-empty");
      if (empty) empty.remove();
      const item = document.createElement("div");
      item.className = "message";
      const meta = document.createElement("div");
      meta.className = "message-meta";
      meta.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      const body = document.createElement("div");
      body.textContent = message;
      item.append(meta, body);
      history.appendChild(item);
      history.scrollTop = history.scrollHeight;
      input.value = "";
      input.focus();
    }
    async function api(path, options = {}) {
      const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "请求失败");
      return data;
    }
    function renderSlots() {
      const labels = ["FINISH", "STANDING", "WORKING", "ERROR", "PENDING", "SLEEPING"];
      const classes = ["finish", "standing", "working", "error", "pending", "sleeping"];
      stopRandomPlayback();
      $("slots").innerHTML = faces.map((face) => `
        <div class="slot">
          <div class="pill intro ${classes[face.slot - 1] || ""}">${labels[face.slot - 1] || "INTRO"}</div>
          <div class="gif-box">
            <img class="face-img" src="/assets/web/face_${face.slot}.png?v=8" data-static="/assets/web/face_${face.slot}.png?v=8" data-gif="/assets/web/face_${face.slot}.gif?v=8" alt="face ${face.slot}" decoding="async" />
          </div>
        </div>
      `).join("");
      animationDurationMs = Math.max(...faces.map((face) => Number(face.durationMs) || 1400), 1400) + 120;
      startRandomPlayback();
    }
    function pickTwoFaces() {
      const previous = new Set(lastPlayedSlots);
      const candidates = faces.filter((face) => !previous.has(face.slot));
      const pool = candidates.length >= 2 ? candidates : faces;
      const selected = [...pool].sort(() => Math.random() - 0.5).slice(0, Math.min(2, pool.length));
      lastPlayedSlots = selected.map((face) => face.slot);
      return selected;
    }
    function resetFaces() {
      document.querySelectorAll(".face-img").forEach((img) => {
        if (img.getAttribute("src") !== img.dataset.static) {
          img.src = img.dataset.static;
        }
      });
    }
    function playRandomFaces(generation) {
      if (generation !== playbackGeneration) return;
      if (resetTimer) window.clearTimeout(resetTimer);
      resetFaces();
      window.requestAnimationFrame(() => {
        if (generation !== playbackGeneration) return;
        pickTwoFaces().forEach((face) => {
          const img = document.querySelector(`.face-img[alt="face ${face.slot}"]`);
          if (img) img.src = img.dataset.gif;
        });
        resetTimer = window.setTimeout(() => {
          if (generation === playbackGeneration) resetFaces();
        }, animationDurationMs);
      });
    }
    function stopRandomPlayback() {
      playbackGeneration += 1;
      if (playingTimer) window.clearTimeout(playingTimer);
      if (resetTimer) window.clearTimeout(resetTimer);
      playingTimer = null;
      resetTimer = null;
      resetFaces();
    }
    function startRandomPlayback() {
      stopRandomPlayback();
      if (!faces.length) return;
      const generation = playbackGeneration;
      const tick = () => {
        if (generation !== playbackGeneration) return;
        playRandomFaces(generation);
        playingTimer = window.setTimeout(tick, animationDurationMs + restDelayMs);
      };
      tick();
    }
    async function refreshFaces() {
      const data = await api("/api/faces");
      faces = data.faces || [];
      renderSlots();
    }
    async function chooseWorkMode() {
      setButtons(true);
      await runModeAnimation();
      try {
        const data = await api("/api/random-upload", { method: "POST", body: JSON.stringify({}) });
        log(`已选择 ${data.name}，正在上传...`);
        let finalJob = null;
        while (true) {
          await new Promise((resolve) => setTimeout(resolve, 300));
          const status = await api("/api/upload-status", { method: "POST", body: JSON.stringify({ jobId: data.jobId }) });
          const job = status.job || {};
          finalJob = job;
          setProgress(job.percent || 0, job.message || "上传中");
          if (job.output) log(job.output);
          if (job.done) break;
        }
        const ok = !!finalJob.ok;
        setProgress(ok ? 100 : (finalJob.percent || 0), finalJob.message || (ok ? "上传完成" : "上传失败"));
        log(ok ? `已上传 ${finalJob.filename || data.name}。` : (finalJob.output || "上传失败。"));
      } catch (err) {
        setProgress(0, "上传失败");
        log(String(err.message || err));
      } finally {
        setModeButtonText("Choose Your Work Mode");
        $("chooseMode").classList.remove("is-counting");
        setButtons(false);
      }
    }
    async function checkConnection() {
      setButtons(true);
      setConnectionState("checking");
      try {
        const data = await api("/api/check-usb", { method: "POST", body: JSON.stringify({}) });
        setConnectionState(data.ok ? "ok" : "bad");
        log(data.ok ? "USB 串口连接正常。" : (data.output || "USB 串口连接失败。"));
      } catch (err) {
        setConnectionState("bad");
        log(String(err.message || err));
      } finally {
        setButtons(false);
      }
    }
    $("checkConnection").addEventListener("click", checkConnection);
    $("chooseMode").addEventListener("click", chooseWorkMode);
    $("codexForm").addEventListener("submit", (event) => {
      event.preventDefault();
      sendCodexMessage();
    });
    $("codexInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.isComposing) {
        event.preventDefault();
        sendCodexMessage();
      }
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        stopRandomPlayback();
      } else {
        startRandomPlayback();
      }
    });
    refreshFaces().catch((err) => log(String(err.message || err)));
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif path.startswith("/assets/"):
                relative = Path(path.removeprefix("/assets/"))
                target = (ASSETS_DIR / relative).resolve()
                content_types = {".gif": "image/gif", ".png": "image/png"}
                content_type = content_types.get(target.suffix.lower())
                if ASSETS_DIR.resolve() not in target.parents or not target.exists() or content_type is None:
                    self.send_json({"error": "not found"}, 404)
                else:
                    self._send(
                        200,
                        target.read_bytes(),
                        content_type,
                        {"Cache-Control": "public, max-age=31536000, immutable"},
                    )
            elif path == "/api/slots":
                self.send_json({"slots": list_slots(), "port": detect_serial_port()})
            elif path == "/api/files":
                self.send_json({"files": list_files(), "port": detect_serial_port()})
            elif path == "/api/programs":
                self.send_json({"programs": list_programs(), "port": detect_serial_port()})
            elif path == "/api/faces":
                self.send_json({"faces": list_faces()})
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"{exc}\n{traceback.format_exc()}"}, 500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self.read_json()
            if path == "/api/save-slot":
                slot = int(data.get("slot", 0))
                name = safe_name(data.get("name", f"slot_{slot}.py"))
                content = data.get("content", "")
                if not content.strip():
                    raise ValueError("文件内容是空的。")
                compile(content, name, "exec")
                slot_path(slot).write_text(content, encoding="utf-8")
                meta = load_slots_meta()
                meta[str(slot)] = {"name": name, "updatedAt": time.time()}
                save_slots_meta(meta)
                self.send_json({"ok": True, "slot": slot, "slots": list_slots()})
            elif path == "/api/save-file":
                name = safe_name(data.get("name", "program.py"))
                content = data.get("content", "")
                if not content.strip():
                    raise ValueError("文件内容是空的。")
                compile(content, name, "exec")
                FILES_DIR.mkdir(exist_ok=True)
                (FILES_DIR / name).write_text(content, encoding="utf-8")
                self.send_json({"ok": True, "name": name, "files": list_files()})
            elif path == "/api/delete-file":
                name = safe_name(data.get("name", ""))
                target = FILES_DIR / name
                if target.exists():
                    target.unlink()
                self.send_json({"ok": True, "files": list_files()})
            elif path == "/api/upload":
                job_id = start_upload_job(data.get("port") or DEFAULT_PORT, data.get("name", ""))
                self.send_json({"ok": True, "jobId": job_id})
            elif path == "/api/upload-slot":
                job_id = start_upload_job(data.get("port") or DEFAULT_PORT, slot=int(data.get("slot", 0)))
                self.send_json({"ok": True, "jobId": job_id})
            elif path == "/api/random-upload":
                programs = [PROGRAMS_DIR / name for name in WORK_MODE_PROGRAMS if (PROGRAMS_DIR / name).exists()]
                if not programs:
                    raise FileNotFoundError("py 文件夹里还没有 fin.py、swing2.py 或 noding.py。")
                weights = [WORK_MODE_WEIGHTS.get(program.name, 1.0) for program in programs]
                selected = random.choices(programs, weights=weights, k=1)[0]
                job_id = start_program_upload_job(data.get("port") or DEFAULT_PORT, selected)
                self.send_json({"ok": True, "jobId": job_id, "name": selected.name})
            elif path == "/api/upload-status":
                with UPLOAD_LOCK:
                    job = dict(UPLOAD_JOBS.get(data.get("jobId", ""), {}))
                if not job:
                    self.send_json({"ok": False, "error": "上传任务不存在"}, 404)
                else:
                    self.send_json({"ok": True, "job": job})
            elif path == "/api/check-usb":
                ok, output = check_usb(data.get("port") or DEFAULT_PORT)
                self.send_json({"ok": ok, "output": output})
            elif path == "/api/ports":
                result = run_mpremote(["connect", "list"], timeout=12)
                self.send_json({"ok": result.returncode == 0, "output": result.stdout.strip(), "returncode": result.returncode})
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"{exc}\n{traceback.format_exc()}"}, 500)


def main() -> int:
    FILES_DIR.mkdir(exist_ok=True)
    PROGRAMS_DIR.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8767), Handler)
    print("CyberBrick display uploader: http://127.0.0.1:8767", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
