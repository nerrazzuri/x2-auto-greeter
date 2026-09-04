#!/usr/bin/env bash
# Install the greeter on a robot's PC2. Run from a laptop that can reach it.
#
#   ./install.sh run@10.0.1.41 [--models DIR] [--wheels DIR] [--service]
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

TARGET=${1:?usage: install.sh user@host [--models DIR] [--wheels DIR] [--service]}
shift
ROOT=/home/run/x2_greeter
MODELS=""; WHEELS=""; WANT_SERVICE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --models) MODELS=$2; shift 2 ;;
    --wheels) WHEELS=$2; shift 2 ;;
    --service) WANT_SERVICE=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

HERE=$(cd "$(dirname "$0")" && pwd)

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

if [ "$WANT_SERVICE" = "1" ]; then
  echo "== installing the systemd unit"
  rsync -a "$HERE/x2-greeter.service" "$TARGET:/tmp/x2-greeter.service"
  ssh "$TARGET" "sudo cp /tmp/x2-greeter.service /etc/systemd/system/ && \
                 sudo systemctl daemon-reload && sudo systemctl enable x2-greeter.service 2>&1 | tail -1"
fi

echo
echo "== installed. Next, on the robot:"
echo "   1. verify the camera:  source $ROOT/env.sh && \\"
echo "        python3 $ROOT/repo/tools/check_camera_orientation.py --models $ROOT/models"
echo "      set camera.rotate_180 in $ROOT/site.yaml from what it says"
echo "   2. start:              $ROOT/bin/start.sh   (or: sudo systemctl start x2-greeter)"
echo "   3. remove everything:  $ROOT/bin/uninstall.sh"
