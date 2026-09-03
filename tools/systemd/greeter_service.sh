#!/usr/bin/env bash
# Foreground entry point for the x2-greeter systemd unit.
#
# Deliberately no `set -u`: the robot's basic_env.sh dereferences AGIBOT_HOME,
# which is unset, and would abort before the launch ever runs.
exec 2>&1

# Without the FASTRTPS_DEFAULT_PROFILES_FILE this sets, the camera topics list
# but not one frame arrives, and the node looks healthy while greeting nobody.
source /home/run/x2_greeter_env.sh >/dev/null 2>&1

# The robot's own stack brings the camera up on its own schedule. DDS handles a
# late joiner, so this does not have to win the race -- it just should not spin
# the detector against nothing for the first minute of every boot.
sleep 45

exec ros2 launch x2_greeter greeter.launch.py params_file:=/home/run/greeter_site.yaml
