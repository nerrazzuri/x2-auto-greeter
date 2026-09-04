#!/usr/bin/env bash
# Remove everything the greeter deployment put on this robot.
#
# The deployment is deliberately confined to two places so that this script can
# be short and complete:
#
#   $X2_GREETER_ROOT               everything we own -- code, workspace, model
#                                  weights, python deps, config, secrets, logs
#   /etc/systemd/system/x2-greeter.service    the only file outside it
#
# Nothing under /agibot is ever touched, by the deployment or by this.
#
#   ./uninstall.sh              remove the deployment
#   ./uninstall.sh --keys       also drop the deploying key from authorized_keys
X2_GREETER_ROOT=${X2_GREETER_ROOT:-/home/run/x2_greeter}
UNIT=/etc/systemd/system/x2-greeter.service

echo "== stopping and disabling the service"
sudo systemctl disable --now x2-greeter.service 2>/dev/null
# Belt and braces: a hand-started instance is not owned by systemd.
pkill -f "greeter.launch" 2>/dev/null
pkill -f "x2_greeter/lib" 2>/dev/null
sleep 2
pkill -9 -f "x2_greeter/lib" 2>/dev/null

if [ -f "$UNIT" ]; then
  echo "== removing $UNIT"
  sudo rm -f "$UNIT"
  sudo systemctl daemon-reload
  sudo systemctl reset-failed x2-greeter.service 2>/dev/null
fi

if [ -d "$X2_GREETER_ROOT" ]; then
  echo "== removing $X2_GREETER_ROOT"
  rm -rf "$X2_GREETER_ROOT"
fi

# Older layouts, before everything moved under one root. Harmless if absent.
for stale in /home/run/x2-auto-greeter /home/run/x2_greeter_ws /home/run/models \
             /home/run/wheels /home/run/x2_greeter_env.sh /home/run/greeter_site.yaml \
             /home/run/.x2_greeter_secrets /home/run/start_greeter.sh \
             /home/run/stop_greeter.sh /home/run/greeter_service.sh \
             /home/run/greeter.log; do
  [ -e "$stale" ] && { echo "== removing legacy $stale"; rm -rf "$stale"; }
done

if [ "${1:-}" = "--keys" ]; then
  KEYS=$HOME/.ssh/authorized_keys
  if [ -f "$KEYS" ]; then
    echo "== removing the deploying key from $KEYS"
    before=$(wc -l < "$KEYS")
    cp "$KEYS" "$KEYS.bak.$(date +%s)"
    grep -v 'x2-greeter-deploy' "$KEYS" > "$KEYS.tmp" && mv "$KEYS.tmp" "$KEYS"
    chmod 600 "$KEYS"
    removed=$(( before - $(wc -l < "$KEYS") ))
    if [ "$removed" -gt 0 ]; then
      echo "   removed $removed key(s) tagged x2-greeter-deploy; a backup is beside the file"
    else
      # Say so rather than implying success. A key added by ssh-copy-id carries
      # whatever comment the local key had, not our tag, and this must not
      # guess at which of somebody's keys to delete.
      echo "   no key tagged x2-greeter-deploy was present -- nothing removed."
      echo "   A key added by hand (ssh-copy-id) is not tagged; remove it yourself:"
      echo "     \$EDITOR ~/.ssh/authorized_keys"
    fi
  fi
fi

echo
echo "== what is left"
for p in "$X2_GREETER_ROOT" "$UNIT"; do
  [ -e "$p" ] && echo "  STILL PRESENT: $p" || echo "  gone: $p"
done
systemctl is-enabled x2-greeter.service 2>/dev/null | grep -q . \
  && echo "  STILL PRESENT: the systemd unit is still known" \
  || echo "  gone: the systemd unit"
pgrep -f "x2_greeter/lib" >/dev/null && echo "  STILL RUNNING: greeting_node" \
  || echo "  gone: no greeter process"
