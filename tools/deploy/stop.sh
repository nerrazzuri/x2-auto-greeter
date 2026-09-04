#!/usr/bin/env bash
# Stop the greeter. The robot's own software is untouched.
pkill -f "greeter.launch" 2>/dev/null
pkill -f "x2_greeter/lib" 2>/dev/null
sleep 2
if pgrep -f "x2_greeter/lib" >/dev/null; then
  echo "== still running; sending SIGKILL"
  pkill -9 -f "x2_greeter/lib" 2>/dev/null
  sleep 1
fi
pgrep -f "x2_greeter/lib" >/dev/null && { echo "== FAILED to stop" >&2; exit 1; }
echo "== greeter stopped"
