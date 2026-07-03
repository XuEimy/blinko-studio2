#!/bin/zsh
cd /Users/apple/Desktop/cyberbrick展示
if ! pgrep -f /Users/apple/Desktop/cyberbrick展示/server.py >/dev/null; then
  screen -dmS cyberbrick_display /usr/bin/python3 /Users/apple/Desktop/cyberbrick展示/server.py
  sleep 1
fi
open http://127.0.0.1:8767
