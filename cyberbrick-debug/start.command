#!/bin/zsh
cd /Users/apple/Desktop/Cyberbrick
if ! pgrep -f /Users/apple/Desktop/Cyberbrick/server.py >/dev/null; then
  nohup /usr/bin/python3 /Users/apple/Desktop/Cyberbrick/server.py > /Users/apple/Desktop/Cyberbrick/server.log 2>&1 &
  sleep 0.6
fi
open http://127.0.0.1:8765
