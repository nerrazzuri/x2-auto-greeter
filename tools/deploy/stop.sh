#!/usr/bin/env bash
# Stop whichever of our nodes is running. The robot's own software is
# untouched.
#
# Both launch files are matched, not only the one this robot is configured
# for: the Phase 1 greeter and the Phase 2 conversation node register an MC
# input source under the same name at the same priority and both hold audio
# focus, so leaving one of them up while starting the other gives a node that
# cannot gesture and nothing obviously wrong in its log. Stopping means
# stopping both.
pkill -f "greeter.launch" 2>/dev/null
pkill -f "conversation.launch" 2>/dev/null
pkill -f "x2_greeter/lib" 2>/dev/null
sleep 2
if pgrep -f "x2_greeter/lib" >/dev/null; then
  echo "== still running; sending SIGKILL"
  pkill -9 -f "x2_greeter/lib" 2>/dev/null
  sleep 1
fi
pgrep -f "x2_greeter/lib" >/dev/null && { echo "== FAILED to stop" >&2; exit 1; }
echo "== greeter stopped"
