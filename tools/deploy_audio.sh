#!/usr/bin/env bash
# Copy the greeting recordings to the interaction compute unit (PC3).
#
# They must live on PC3, NOT on PC2 where the greeter node runs, and the
# directory and every parent must be readable by all users. A subdirectory of
# /var/tmp is what the interface docs recommend.
set -euo pipefail

SRC=${1:-assets/audio}
PC3=${PC3:-10.0.1.42}
USER_NAME=${PC3_USER:-agi}
DEST=${DEST:-/var/tmp/x2_greeter_audio}

if [ ! -d "$SRC" ]; then
  echo "no such directory: $SRC" >&2
  echo "run tools/make_greeting_audio.py --dest $SRC first" >&2
  exit 1
fi

count=$(find "$SRC" -maxdepth 1 -name 'greeting_*.wav' | wc -l)
if [ "$count" -eq 0 ]; then
  echo "no greeting_NN.wav files in $SRC" >&2
  exit 1
fi

echo "== deploying $count file(s) from $SRC to ${USER_NAME}@${PC3}:${DEST}"

ssh "${USER_NAME}@${PC3}" "mkdir -p '${DEST}' && chmod 755 '${DEST}'"
scp "$SRC"/greeting_*.wav "${USER_NAME}@${PC3}:${DEST}/"
ssh "${USER_NAME}@${PC3}" "chmod 644 ${DEST}/greeting_*.wav && ls -l ${DEST}"

echo
echo "== done. Confirm speech.audio_dir is ${DEST} in config/greeter.yaml"
echo "== and speech.audio_file_count is ${count}"
