#!/usr/bin/env bash
# Foreground entry point for the x2-greeter systemd unit.
# No `set -u` -- see env.sh.
exec 2>&1
X2_GREETER_ROOT=${X2_GREETER_ROOT:-/home/run/x2_greeter}
source "$X2_GREETER_ROOT/env.sh" >/dev/null 2>&1

# The robot's own stack brings the camera up on its own schedule. DDS handles a
# late joiner, so this is not a race that has to be won; it just avoids
# spinning the detector against an empty topic for the first minute of a boot.
sleep 45

exec ros2 launch x2_greeter greeter.launch.py \
     params_file:="$X2_GREETER_ROOT/site.yaml"
