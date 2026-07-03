#!/usr/bin/env python3
"""Small mpremote wrapper for controlling CyberBrick LEDs."""

from __future__ import annotations

import argparse
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path


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
    raise SystemExit(
        "找不到 mpremote。请先安装：python3 -m pip install mpremote"
    )


def normalize_int(value: str) -> int:
    value = value.strip().replace("_", "")
    if value.lower().startswith("0b"):
        return int(value, 2)
    if value.lower().startswith("0x"):
        return int(value, 16)
    return int(value, 10)


def normalize_rgb(value: str) -> int:
    value = value.strip()
    if value.startswith("#"):
        value = "0x" + value[1:]
    return normalize_int(value)


def run_mpremote(args: list[str]) -> subprocess.CompletedProcess[str]:
    mpremote = find_mpremote()
    return subprocess.run(
        [mpremote, *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def run_mpremote_repl_inject(port: str, code: str, timeout: float = 12.0) -> tuple[int, str]:
    """Run code through the friendly REPL, avoiding mpremote raw exec."""
    mpremote = find_mpremote()
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        [mpremote, "connect", port, "repl"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    output = bytearray()
    started_at = time.monotonic()
    stage = "connect"
    stage_started_at = started_at
    exit_sent = False

    try:
        while True:
            now = time.monotonic()
            if now - started_at > timeout:
                proc.terminate()
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
            if stage == "connect" and (
                "Use Ctrl-]" in text or "raw REPL" in text or ">>>" in text or ">" in text
            ):
                # Leave raw REPL if a previous failed command left the board there.
                os.write(master_fd, b"\x02")
                time.sleep(0.2)
                # Stop the currently running boot/app task so we get a prompt.
                os.write(master_fd, b"\x03")
                time.sleep(0.2)
                os.write(master_fd, b"\x03")
                stage = "waiting_prompt"
                stage_started_at = now

            if stage == "waiting_prompt" and (">>>" in text or now - stage_started_at > 1.2):
                # Paste mode handles multi-line code like a manual REPL paste.
                os.write(master_fd, b"\x05")
                time.sleep(0.15)
                os.write(master_fd, code.replace("\n", "\r\n").encode("utf-8"))
                os.write(master_fd, b"\r\n\x04")
                stage = "injected"
                stage_started_at = now

            if stage == "injected" and not exit_sent and time.monotonic() - stage_started_at > 2.0:
                os.write(master_fd, b"\x18")
                exit_sent = True

            if exit_sent and proc.poll() is not None:
                break

        if proc.poll() is None:
            proc.terminate()
        cleaned = clean_output(bytes(output))
        if "Traceback (most recent call last)" in cleaned or "Error:" in cleaned:
            return 1, cleaned
        return proc.returncode or 0, cleaned
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass


def clean_output(output: bytes) -> str:
    text = output.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(
        r"Traceback \(most recent call last\):.*?KeyboardInterrupt:\s*",
        "",
        text,
        flags=re.S,
    )
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        if line.startswith("Connected to MicroPython"):
            continue
        if line.startswith("Use Ctrl-]") or line.startswith("Use Ctrl-J"):
            continue
        if line.startswith("paste mode;"):
            continue
        if line.startswith("raw REPL;"):
            continue
        if line in {">>>", ">", "==="}:
            continue
        lines.append(line)
    return "\n".join(line for line in lines if line.strip()).strip()


def list_ports() -> int:
    result = run_mpremote(["connect", "list"])
    print(result.stdout.strip() or "没有检测到串口。")
    return result.returncode


def led_code(
    channel: str,
    mode: int,
    duration: int,
    repeat_count: int,
    led_index: int,
    rgb: int,
) -> str:
    return "\n".join(
        [
            "import sys",
            'sys.path.append("/app")',
            "from bbl.leds import LEDController",
            f'led = LEDController("{channel}")',
            (
                "led.set_led_effect("
                f"{mode}, {duration}, {repeat_count}, "
                f"{led_index}, 0x{rgb:06X})"
            ),
            "led.timing_proc()",
        ]
    )


def set_led(args: argparse.Namespace) -> int:
    if args.code_file:
        code = Path(args.code_file).read_text(encoding="utf-8")
        if not code.strip():
            raise SystemExit("代码文件是空的。")
        if args.method == "raw":
            port_args = ["connect", args.port] if args.port != "auto" else ["connect", "auto"]
            result = run_mpremote([*port_args, "exec", code])
            returncode = result.returncode
            output = clean_output(result.stdout.encode())
        else:
            returncode, output = run_mpremote_repl_inject(args.port, code)
        if output:
            print(output)
        if returncode == 0:
            print("完成。")
        return returncode

    if args.channel not in {"LED1", "LED2"}:
        raise SystemExit("LED 通道只能是 LED1 或 LED2。")
    mode_map = {"solid": 0, "blink": 1, "breath": 2}
    mode = mode_map[args.mode]
    code = led_code(
        channel=args.channel,
        mode=mode,
        duration=args.duration,
        repeat_count=args.repeat,
        led_index=normalize_int(args.led_index),
        rgb=normalize_rgb(args.color),
    )
    if args.method == "raw":
        port_args = ["connect", args.port] if args.port != "auto" else ["connect", "auto"]
        result = run_mpremote([*port_args, "exec", code])
        returncode = result.returncode
        output = clean_output(result.stdout.encode())
    else:
        returncode, output = run_mpremote_repl_inject(args.port, code)
    if output:
        print(output)
    if returncode == 0:
        print("完成。")
    return returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="CyberBrick LED mpremote helper")
    parser.add_argument("--list", action="store_true", help="list serial ports")
    parser.add_argument("--port", default="/dev/cu.usbmodem101")
    parser.add_argument("--channel", default="LED2", choices=("LED1", "LED2"))
    parser.add_argument(
        "--mode", default="solid", choices=("solid", "blink", "breath")
    )
    parser.add_argument("--duration", type=int, default=0)
    parser.add_argument("--repeat", type=int, default=255)
    parser.add_argument("--led-index", default="0b0001")
    parser.add_argument("--color", default="0xCB3F3F")
    parser.add_argument("--code-file", help="run a Python code file through REPL")
    parser.add_argument(
        "--method",
        default="repl",
        choices=("repl", "raw"),
        help="repl avoids raw REPL and matches manual mpremote repl usage",
    )
    args = parser.parse_args()

    if args.list:
        return list_ports()
    return set_led(args)


if __name__ == "__main__":
    raise SystemExit(main())
