#!/usr/bin/env bash
# Environment for the X2 auto-greeter. Source before building or launching.
#
# Deliberately no `set -u`: the robot's basic_env.sh dereferences AGIBOT_HOME,
# which is unset, and would abort this before it reaches the launch.

X2_GREETER_ROOT=${X2_GREETER_ROOT:-/home/run/x2_greeter}

# The robot's own environment. It unsets ROS_DOMAIN_ID (so, domain 0) and sets
# FASTRTPS_DEFAULT_PROFILES_FILE. Without that profile a participant does not
# match the robot's: every topic still lists, and not one frame ever arrives.
source /agibot/software/entry/cfg/basic_env.sh

# aimdk_msgs. `~/aimdk/install/local_setup.bash` does not exist on a v1.0
# image, and the prebuilt tree inside ~/aimdk carries a reduced service subset.
# Several prefixes under /agibot/software ship the full build and *which* ones
# differ between robots, so find one rather than hard-coding a path.
_x2_find_aimdk() {
  local d p best=""
  for d in /agibot/software/*/local/lib/python3.10/dist-packages/aimdk_msgs; do
    [ -d "$d/srv" ] || continue
    p=${d%%/local/*}
    # 178 services is the full interface; the partial builds carry a handful.
    if [ "$(ls "$d"/srv/*.py 2>/dev/null | wc -l)" -ge 100 ]; then
      # Prefer 'common' when it is one of them, purely for reproducibility.
      case "$p" in */common) echo "$p"; return 0 ;; esac
      [ -n "$best" ] || best="$p"
    fi
  done
  [ -n "$best" ] && echo "$best"
}

_AIMDK=$(_x2_find_aimdk)
if [ -n "$_AIMDK" ]; then
  export AMENT_PREFIX_PATH="$_AIMDK:$AMENT_PREFIX_PATH"
  export PYTHONPATH="$_AIMDK/local/lib/python3.10/dist-packages:$PYTHONPATH"
  export LD_LIBRARY_PATH="$_AIMDK/lib:$LD_LIBRARY_PATH"
else
  echo "x2_greeter: no full aimdk_msgs build found under /agibot/software" >&2
fi

# Python dependencies live in our own tree, never in ~/.local: `run` is the
# account every robot ROS node uses, and a --user install would shadow the
# system packages those nodes import.
[ -d "$X2_GREETER_ROOT/deps" ] && \
  export PYTHONPATH="$X2_GREETER_ROOT/deps:$PYTHONPATH"

[ -f "$X2_GREETER_ROOT/ws/install/setup.bash" ] && \
  source "$X2_GREETER_ROOT/ws/install/setup.bash"

# Secrets (ANTHROPIC_API_KEY, ANTHROPIC_CUSTOM_HEADERS). Separate 0600 file so
# redeploying this script never clobbers the key, and no key is in the repo.
[ -f "$X2_GREETER_ROOT/secrets" ] && . "$X2_GREETER_ROOT/secrets"

# Always succeed. Every line above is a conditional, so without this the exit
# status is whichever test happened to be last -- and a sourced environment
# that "fails" because there is no secrets file breaks any `source env.sh &&`
# chain and any caller running under `set -e`. It broke this repo's own
# installer exactly once.
:
