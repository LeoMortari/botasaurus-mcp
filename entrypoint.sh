#!/bin/bash
set -e

cd /app

echo '--- Starting Xvfb (:99) for Chromium no headless...'
Xvfb :99 -screen 0 1280x800x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &
sleep 1
export DISPLAY=:99

if [ "$ENABLE_VNC" = "true" ]; then
  echo '--- ENABLE_VNC=true. Building graphical stack...'

  echo '--- Starting fluxbox...'
  fluxbox >/tmp/fluxbox.log 2>&1 &
  sleep 1

  echo '--- Starting x11vnc (5900)...'
  x11vnc -display :99 -nopw -forever -shared -rfbport 5900 -bg -o /tmp/x11vnc.log

  echo '--- Starting noVNC (6080)...'
  websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/novnc.log 2>&1 &
  sleep 1

  echo '--- Graphical stack ready! Access noVNC in port 6080'
fi

exec python /app/app.py
