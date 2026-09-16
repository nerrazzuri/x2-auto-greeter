#!/usr/bin/env bash
# Install the greeter on a robot's PC2. Run from a laptop that can reach it.
#
#   ./install.sh run@10.0.1.41 [--models DIR] [--wheels DIR] [--site NAME]
#                              [--service] [--start]
#
# A whole deployment in one command:
#
#   ./install.sh run@10.0.1.41 --models ~/x2-models --site klgw --service --start
#
# --site NAME applies tools/deploy/sites/NAME.yaml to the robot's site.yaml --
# the handful of values that differ for one deployment (which phrase list,
# which camera, whether the cloud backend is used at all), version-controlled
# rather than typed into an editor on site. --service installs the systemd
# unit and enables it for boot; --start also starts it now. Both need the
# robot's sudo password, so they allocate a terminal and will prompt.
#
# Everything lands in one directory so tools/deploy/uninstall.sh can remove the
# whole deployment. Nothing under /agibot is written, ever.
#
# It deliberately does NOT write site.yaml's camera settings for you: whether
# the head module is mounted upside down is a property of the individual robot,
# and guessing it produces a node that runs perfectly and greets nobody. Run
# tools/check_camera_orientation.py afterwards and set camera.rotate_180 from
# what it reports.
set -e

TARGET=${1:?usage: install.sh user@host [--models DIR] [--wheels DIR] [--site NAME] [--service] [--start]}
shift
ROOT=/home/run/x2_greeter
SHARE=$ROOT/ws/install/x2_greeter/share/x2_greeter/config
MODELS=""; WHEELS=""; SITE=""; WANT_SERVICE=0; WANT_START=0
while [ $# -gt 0 ]; do
  case "$1" in
    --models) MODELS=$2; shift 2 ;;
    --wheels) WHEELS=$2; shift 2 ;;
    --site) SITE=$2; shift 2 ;;
    --service) WANT_SERVICE=1; shift ;;
    --start) WANT_START=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

HERE=$(cd "$(dirname "$0")" && pwd)

# Checked before anything is copied: a typo in --site is worth two seconds
# here rather than a five-minute install that ends in a robot configured for
# nowhere in particular.
PROFILE=""
if [ -n "$SITE" ]; then
  PROFILE=$HERE/sites/$SITE.yaml
  [ -f "$PROFILE" ] || {
    echo "no such site profile: $PROFILE" >&2
    echo "available: $(ls "$HERE/sites" 2>/dev/null | sed 's/\.yaml$//' | tr '\n' ' ')" >&2
    exit 2
  }
fi

# Install our public key tagged, so uninstall.sh --keys has something it can
# match and can never remove a key somebody else put there. ssh-copy-id would
# carry whatever comment the local key happens to have, which is exactly how
# --keys came to be a promise the script could not keep.
KEY=${X2_DEPLOY_KEY:-$HOME/.ssh/id_ed25519.pub}
if [ -f "$KEY" ] && ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$TARGET" true 2>/dev/null; then
  echo "== installing the deploying key (tagged x2-greeter-deploy)"
  TAGGED=$(awk '{print $1, $2, "x2-greeter-deploy"}' "$KEY")
  ssh "$TARGET" "mkdir -p ~/.ssh && chmod 700 ~/.ssh && \
                 grep -qF '$TAGGED' ~/.ssh/authorized_keys 2>/dev/null || \
                 echo '$TAGGED' >> ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys"
fi
REPO=$(cd "$HERE/../.." && pwd)

echo "== creating $ROOT on $TARGET"
ssh "$TARGET" "mkdir -p $ROOT/bin $ROOT/models $ROOT/deps $ROOT/ws/src"

echo "== copying the repository"
rsync -a --delete --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
      --exclude 'sdk' "$REPO/" "$TARGET:$ROOT/repo/"

echo "== copying scripts"
rsync -a "$HERE/env.sh" "$TARGET:$ROOT/env.sh"
rsync -a "$HERE/start.sh" "$HERE/stop.sh" "$HERE/service.sh" "$HERE/uninstall.sh" \
      "$TARGET:$ROOT/bin/"
ssh "$TARGET" "chmod +x $ROOT/bin/*.sh"

[ -n "$MODELS" ] && { echo "== copying detector weights"; rsync -a "$MODELS/" "$TARGET:$ROOT/models/"; }

if [ -n "$WHEELS" ]; then
  echo "== installing python deps into $ROOT/deps (never into ~/.local)"
  rsync -a "$WHEELS/" "$TARGET:$ROOT/wheelhouse/"
  ssh "$TARGET" "python3 -m pip install --no-index --find-links=$ROOT/wheelhouse \
                 --target=$ROOT/deps 'anthropic>=1.0' 2>&1 | tail -2"
fi

echo "== linking the package into the workspace and building"
# `source` is followed by `;` not `&&`: an environment script's exit status
# describes nothing useful, and chaining on it is how the build got skipped
# silently the first time this ran.
ssh "$TARGET" "set -e
               ln -sfn $ROOT/repo/x2_greeter_ws/src/x2_greeter $ROOT/ws/src/x2_greeter
               source $ROOT/env.sh >/dev/null 2>&1 || true
               cd $ROOT/ws
               colcon build --packages-select x2_greeter 2>&1 | tail -2"

echo "== seeding site.yaml from the shipped config, if absent"
ssh "$TARGET" "[ -f $ROOT/site.yaml ] || { \
    cp $ROOT/ws/install/x2_greeter/share/x2_greeter/config/greeter.yaml $ROOT/site.yaml && \
    sed -i \"s|^      model_dir: ''|      model_dir: $ROOT/models|\" $ROOT/site.yaml && \
    echo '   seeded (camera.rotate_180 left at its default -- verify it)'; }"

if [ -n "$PROFILE" ]; then
  echo "== applying the $SITE site profile to site.yaml"
  rsync -a "$PROFILE" "$TARGET:/tmp/x2-site-profile.yaml"
  rsync -a "$HERE/apply_site.py" "$TARGET:/tmp/x2-apply-site.py"
  ssh "$TARGET" "python3 /tmp/x2-apply-site.py $ROOT/site.yaml /tmp/x2-site-profile.yaml \
                   --share $SHARE --root $ROOT
                 rm -f /tmp/x2-site-profile.yaml /tmp/x2-apply-site.py"
fi

if [ "$WANT_SERVICE" = "1" ]; then
  echo "== installing the systemd unit (sudo on the robot will prompt)"
  rsync -a "$HERE/x2-greeter.service" "$TARGET:/tmp/x2-greeter.service"
  # -t: sudo has no way to ask for a password down a pipe, and without a
  # terminal this step failed with an askpass error instead of prompting.
  #
  # touch first: the unit's StandardOutput=append: creates the log as root if
  # it does not exist yet, and the node -- which runs as `run` -- then cannot
  # write to its own log for the rest of the deployment's life.
  ssh -t "$TARGET" "touch $ROOT/greeter.log
                    sudo cp /tmp/x2-greeter.service /etc/systemd/system/ &&
                    sudo systemctl daemon-reload &&
                    sudo systemctl enable x2-greeter.service 2>&1 | tail -1
                    rm -f /tmp/x2-greeter.service"
fi

if [ "$WANT_START" = "1" ]; then
  # Through systemd when the unit is installed: bin/start.sh kills whatever is
  # running first, and systemd would restart the service thirty seconds later,
  # leaving two greeters talking over each other.
  echo "== starting (the node waits 45 s for the robot's camera stack)"
  ssh -t "$TARGET" "if systemctl list-unit-files x2-greeter.service >/dev/null 2>&1; then
                      sudo systemctl start x2-greeter.service
                    else
                      $ROOT/bin/start.sh
                    fi"
fi

echo
echo "== installed. Next, on the robot:"
echo "   1. verify the camera:  source $ROOT/env.sh && \\"
echo "        python3 $ROOT/repo/tools/check_camera_orientation.py --models $ROOT/models"
echo "      set camera.rotate_180 in $ROOT/site.yaml from what it says"
if [ "$WANT_START" = "1" ]; then
echo "   2. watch it come up:   ssh $TARGET 'tail -f $ROOT/greeter.log'"
echo "      the startup line names the backend and camera actually in force"
else
echo "   2. start:              $ROOT/bin/start.sh   (or: sudo systemctl start x2-greeter)"
fi
echo "   3. remove everything:  $ROOT/bin/uninstall.sh"
