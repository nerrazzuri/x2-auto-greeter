#!/usr/bin/env bash
# Start the greeter by hand. Safe to re-run: stops any previous instance first.
#
# Prerequisite this cannot do for you: bring the robot to force-control stand
# with the remote. The greeter never changes the motion mode -- outside
# force-control stand it speaks and refuses to gesture, by design, and logs it.
X2_GREETER_ROOT=${X2_GREETER_ROOT:-/home/run/x2_greeter}
LOG="$X2_GREETER_ROOT/greeter.log"

echo "== stopping any previous greeter"
"$X2_GREETER_ROOT/bin/stop.sh" >/dev/null 2>&1

source "$X2_GREETER_ROOT/env.sh" >/dev/null 2>&1

echo "== starting greeter"
nohup ros2 launch x2_greeter greeter.launch.py \
      params_file:="$X2_GREETER_ROOT/site.yaml" > "$LOG" 2>&1 &
sleep 20

if pgrep -f "x2_greeter/lib" >/dev/null; then
  echo "== running. startup line:"
  grep -a "x2_greeter up:" "$LOG" | tail -1
  echo
  echo "   log:   tail -f $LOG"
  echo "   stop:  $X2_GREETER_ROOT/bin/stop.sh"
else
  echo "== FAILED to start. Last lines of $LOG:" >&2
  grep -av -E "HOSTID|compute_id|RTPS_|PARTICIPANT" "$LOG" | tail -15 >&2
  exit 1
fi
