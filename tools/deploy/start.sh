#!/usr/bin/env bash
# Start whichever node this robot is installed for. Safe to re-run: it stops
# any previous instance of *either* node first.
#
#   start.sh                 the node named in $ROOT/node (install.sh writes it)
#   start.sh greeting        the Phase 1 greeter, for this run only
#   start.sh conversation    the Phase 2 conversational node, for this run only
#
# Prerequisite this cannot do for you: bring the robot to force-control stand
# with the remote. Neither node ever changes the motion mode -- outside
# force-control stand they speak, refuse to gesture, and say so in the log.
X2_GREETER_ROOT=${X2_GREETER_ROOT:-/home/run/x2_greeter}
LOG="$X2_GREETER_ROOT/greeter.log"

NODE=${1:-$(cat "$X2_GREETER_ROOT/node" 2>/dev/null || echo greeting)}
case "$NODE" in
  greeting)     LAUNCH=greeter.launch.py;      PARAMS=site.yaml ;;
  conversation) LAUNCH=conversation.launch.py; PARAMS=site-conversation.yaml ;;
  *) echo "unknown node '$NODE' (want: greeting | conversation)" >&2; exit 2 ;;
esac

if [ ! -f "$X2_GREETER_ROOT/$PARAMS" ]; then
  echo "== no $PARAMS in $X2_GREETER_ROOT -- was install.sh run for '$NODE'?" >&2
  exit 1
fi

# Not politeness: the two nodes register an MC input source under the same
# name at the same priority and both hold audio focus, so the second one up
# can end up unable to gesture with nothing wrong-looking in its log.
echo "== stopping any previous node"
"$X2_GREETER_ROOT/bin/stop.sh" >/dev/null 2>&1

source "$X2_GREETER_ROOT/env.sh" >/dev/null 2>&1

echo "== starting $NODE"
nohup ros2 launch x2_greeter "$LAUNCH" \
      params_file:="$X2_GREETER_ROOT/$PARAMS" > "$LOG" 2>&1 &

# The conversation node loads a local Whisper model on the way up; the first
# start after an install also downloads it. 20 s is enough for the greeter and
# not for that.
[ "$NODE" = conversation ] && WAIT=60 || WAIT=20
sleep "$WAIT"

if pgrep -f "x2_greeter/lib" >/dev/null; then
  echo "== running. startup line:"
  grep -aE "x2_greeter up:|x2_conversation up:" "$LOG" | tail -1
  echo
  echo "   log:   tail -f $LOG"
  echo "   stop:  $X2_GREETER_ROOT/bin/stop.sh"
else
  echo "== FAILED to start. Last lines of $LOG:" >&2
  grep -av -E "HOSTID|compute_id|RTPS_|PARTICIPANT" "$LOG" | tail -15 >&2
  exit 1
fi
