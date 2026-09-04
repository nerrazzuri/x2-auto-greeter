#!/usr/bin/env bash
# Install the greeter on a robot's PC2. Run from a laptop that can reach it.
#
#   ./install.sh run@10.0.1.41 [--conversation] [--models DIR] [--wheels DIR]
#                              [--service]
#
# --conversation installs the Phase 2 multi-turn node instead of the Phase 1
# greeter: it adds faster-whisper for local speech-to-text, seeds
# site-conversation.yaml, and records the choice in $ROOT/node so that
# start.sh and the systemd unit both launch the right thing. The two nodes
# must never run at the same time -- they register an MC input source under
# the same name and both hold audio focus -- so start.sh stops either before
# starting one.
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

TARGET=${1:?usage: install.sh user@host [--conversation] [--models DIR] [--wheels DIR] [--service]}
shift
ROOT=/home/run/x2_greeter
MODELS=""; WHEELS=""; WANT_SERVICE=0; NODE=greeting
while [ $# -gt 0 ]; do
  case "$1" in
    --models) MODELS=$2; shift 2 ;;
    --wheels) WHEELS=$2; shift 2 ;;
    --service) WANT_SERVICE=1; shift ;;
    --conversation) NODE=conversation; shift ;;
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

if [ "$NODE" = conversation ]; then
  # Needs the internet, which PC2 has and PC1 must never be used for. Into
  # $ROOT/deps, not ~/.local: `run` is the account every robot ROS node uses
  # and a --user install would shadow the system packages those nodes import.
  echo "== installing faster-whisper into $ROOT/deps (a few minutes)"
  ssh "$TARGET" "python3 -m pip install --target=$ROOT/deps --upgrade \
                 faster-whisper 2>&1 | tail -3"
fi

echo "== recording which node this robot runs ($NODE)"
ssh "$TARGET" "echo $NODE > $ROOT/node"

echo "== linking the package into the workspace and building"
# `source` is followed by `;` not `&&`: an environment script's exit status
# describes nothing useful, and chaining on it is how the build got skipped
# silently the first time this ran.
ssh "$TARGET" "set -e
               ln -sfn $ROOT/repo/x2_greeter_ws/src/x2_greeter $ROOT/ws/src/x2_greeter
               source $ROOT/env.sh >/dev/null 2>&1 || true
               cd $ROOT/ws
               colcon build --packages-select x2_greeter 2>&1 | tail -2"

if [ "$NODE" = conversation ]; then
  SITE=site-conversation.yaml; SHIPPED=conversation.yaml
else
  SITE=site.yaml; SHIPPED=greeter.yaml
fi
echo "== seeding $SITE from the shipped config, if absent"
# Absent, never overwritten: the site file is where a robot's own tuning lives
# -- camera.rotate_180, the venue, whichever bring-up flags have been proven on
# this machine -- and a redeploy that reset it would silently undo a day's work.
ssh "$TARGET" "[ -f $ROOT/$SITE ] || { \
    cp $ROOT/ws/install/x2_greeter/share/x2_greeter/config/$SHIPPED $ROOT/$SITE && \
    sed -i \"s|^      model_dir: ''|      model_dir: $ROOT/models|\" $ROOT/$SITE && \
    echo '   seeded (camera.rotate_180 left at its default -- verify it)'; }"

if [ "$NODE" = conversation ]; then
  echo "== pre-warming the Whisper model (~460 MB on a first install)"
  # Before the demo, not during it. HF_HOME is set by env.sh to $ROOT/models/hf
  # so uninstall.sh takes the cache with the rest of the deployment.
  ssh "$TARGET" "source $ROOT/env.sh >/dev/null 2>&1
                 python3 -c \"from faster_whisper import WhisperModel
WhisperModel('small', compute_type='int8')
print('   whisper small ready')\" 2>&1 | tail -2"
fi

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
if [ "$NODE" = conversation ]; then
echo "      it launches conversation.launch.py with $ROOT/site-conversation.yaml"
echo "      every hardware-gated feature ships off -- see docs/HARDWARE_BRINGUP.md"
echo "      for the order to turn them on in, one at a time"
fi
echo "   3. remove everything:  $ROOT/bin/uninstall.sh"
