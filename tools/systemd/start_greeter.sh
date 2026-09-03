#!/usr/bin/env bash
# Start the X2 auto-greeter. Safe to re-run: it stops any previous instance.
#
# Prerequisite the script cannot do for you: bring the robot to force-control
# stand with the remote. The greeter never changes the motion mode -- outside
# force-control stand it speaks but does not gesture, by design, and says so
# in the log.
# Deliberately no `set -u`: the robot's own basic_env.sh dereferences
# AGIBOT_HOME, which is unset, and would abort this script before it ever
# reaches the launch.
LOG=/home/run/greeter.log
PARAMS=/home/run/greeter_site.yaml

echo "== stopping any previous greeter"
pkill -f "greeter.launch" 2>/dev/null
pkill -f "x2_greeter/lib" 2>/dev/null
sleep 3

# basic_env.sh is the robot's own environment. Without the
# FASTRTPS_DEFAULT_PROFILES_FILE it sets, the camera topics still list but not
# one frame ever arrives -- the node looks healthy and greets nobody.
source /home/run/x2_greeter_env.sh >/dev/null 2>&1

echo "== starting greeter"
nohup ros2 launch x2_greeter greeter.launch.py params_file:="$PARAMS" > "$LOG" 2>&1 &
sleep 20

if pgrep -f "x2_greeter/lib" >/dev/null; then
  echo "== running. startup line:"
  grep -a "x2_greeter up:" "$LOG" | tail -1
  echo
  echo "   log:   tail -f $LOG"
  echo "   stop:  /home/run/stop_greeter.sh"
else
  echo "== FAILED to start. Last lines of $LOG:" >&2
  grep -av -E "HOSTID|compute_id|RTPS_|PARTICIPANT" "$LOG" | tail -15 >&2
  exit 1
fi
