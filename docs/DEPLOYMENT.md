# Deploying the X2 auto-greeter

**Never build or run on PC1 (10.0.1.40).** The SDK documentation states this is
"strictly prohibited to avoid safety risks". The greeter runs on **PC2**
(10.0.1.41); its audio assets live on **PC3** (10.0.1.42).

If you are an agent doing this for the first time, read
[`AGENT_BRINGUP_GUIDE.md`](AGENT_BRINGUP_GUIDE.md) first — it covers what this
runbook assumes: the architecture, the untested assumptions, and the known gaps.

## 1. Prerequisites on PC2

```bash
source /opt/ros/humble/setup.bash
source ~/aimdk/install/local_setup.bash    # provides aimdk_msgs
python3 -m pip install --user "anthropic>=1.0" pyyaml
sudo apt install ros-humble-cv-bridge python3-opencv
```

## 2. Build the overlay workspace

Clone this repository somewhere under `$HOME` **other than `$HOME/aimdk*`** —
the SDK README declares that path "reserved and maintained by the system", and
it is erased on firmware upgrade.

```bash
cd ~/x2_greeter_ws
colcon build --packages-select x2_greeter
source install/setup.bash
```

## 3. Optional: fetch the person-detection weights

Without them the node falls back to OpenCV's built-in HOG detector and logs a
warning. With them, detection is faster and more reliable.

```bash
python3 tools/fetch_model.py --dest ~/models
# then set detect.model_dir: /home/agi/models in config/greeter.yaml
```

## 4. Generate and deploy the greeting recordings

These are the tier-3 fallback, used only if `PlayTts` reports failure.

```bash
python3 tools/make_greeting_audio.py --dest assets/audio
tools/deploy_audio.sh assets/audio
```

The script creates `/var/tmp/x2_greeter_audio` on PC3, world-readable. Confirm
`speech.audio_dir` and `speech.audio_file_count` in `config/greeter.yaml` match.

**Every parent directory, not just the leaf, must be readable/traversable by
the account the audio service runs as.** The default is safe because
`/var/tmp` is world-traversable (`1777`) on a stock system. If you override
`DEST` — a home directory, a freshly created mount, anything outside
`/var/tmp` — check the whole chain, not just the directory `deploy_audio.sh`
`chmod`s:

```bash
namei -om /path/to/DEST
```

Every component from `/` down must show `r` and `x` for "other" (or the
audio service's group). This is the trap when overriding `DEST`: a wrong
permission here does not show up as an error anywhere — `PlayAudioFile`
simply cannot open the file, and the failure looks identical to a missing or
misnamed recording.

## 5. Set the API key

```bash
export ANTHROPIC_API_KEY=...     # never commit this
```

Without it the node still starts, logs a warning, and greets from the canned
phrase list.

## 6. Bring the robot to STAND_DEFAULT — with a human present

The greeter **never** changes the motion mode. Gestures require force-control
stand, so put the robot there yourself, feet planted:

```bash
ros2 run py_examples set_mc_action PD    # passive
ros2 run py_examples set_mc_action JD    # position-control stand
ros2 run py_examples set_mc_action SD    # force-control stand
```

Outside `STAND_DEFAULT` the greeter speaks but does not gesture, and warns once.

## 7. Launch

```bash
ros2 launch x2_greeter greeter.launch.py
```

Bench test with no robot at all:

```bash
ros2 launch x2_greeter greeter.launch.py fake_robot:=true
```

## 8. First-window smoke checklist

Work through these on first hardware access; each resolves an open question in
the design spec, section 17.

| Check | How | If it is wrong |
|---|---|---|
| Camera topics are live | `ros2 topic hz /aima/hal/sensor/rgbd_head_front/rgb_image` | fix `camera.rgb_topic` |
| Depth encoding | `ros2 topic echo --field encoding .../depth_image --once` | `16UC1` → `camera.depth_scale: 0.001`; `32FC1` → `1.0` |
| Distance readings look right | stand at a tape-measured 2 m; the log prints the gated distance | adjust `camera.depth_scale` |
| A person is detected | walk into frame; the log shows the state machine advancing | lower `detect.confidence_min`, or fetch the SSD weights |
| Detection keeps up with `presence.loss_grace_s` (0.5 s shipped) | check this alongside `camera.depth_scale`: `_on_frame` runs `detector.detect()` on every synced frame with no throttling, so the distance-reading refresh cadence equals the detector's per-frame latency; watch the log for how often a fresh distance appears | if detection is slower than `loss_grace_s` — plausible for the HOG fallback, which is what runs whenever `detect.model_dir` is empty, as it ships — every reading is stale by the time a greeting reaches the gesture interlock, and **the robot speaks but never gestures, with no error anywhere**. It fails safe, and the log names both the age and the bound, so it is diagnosable once you know to look. Raising `loss_grace_s` makes the symptom go away but weakens the arm's-reach interlock; the real fix is faster detection (fetch the SSD weights, step 3) |
| `PlayTts` works offline | pull the network, then greet | if it fails, set `speech.tier: audio_file` |
| `PlayAudioFile` needs audio focus | force tier 3 and watch for a rejection | if so, call `RequestAudioFocus` first — currently not implemented |
| Gesture timing | watch a full greeting | if speech and gesture desynchronise, tune per-gesture duration |
| CPU headroom | `top` while the node runs | switch `detect.detector` to `hog`, or lower the frame rate |

## 9. Safety notes

- **The 1.0 m arm's-reach floor is not an absolute guarantee in a crowd.**
  The node gestures toward the most central gated detection, not the
  nearest one. If a second person stays gated further away while the
  greeted person steps inside 1.0 m, the closer person can be masked: the
  latest distance reading stays fresh and far, and the gesture fires with
  somebody closer than the floor. Do not rely on the floor alone in a
  crowded scene.

---

## Phase 2: the conversational node

This section covers what changes for `x2_conversation`, the multi-turn node.
Everything in sections 1–9 above (PC1 off limits, build location, the
`ANTHROPIC_API_KEY` rule, `STAND_DEFAULT`, a human at the stop control) still
applies unchanged. Read `docs/HARDWARE_BRINGUP.md` before turning on anything
this section marks as shipped-disabled — it gives the order and the reason
for each step.

### 1. New Python dependency, on PC2 only

`faster-whisper` and its `ctranslate2` backend do local speech-to-text.
PC2 is the only host with internet, and PC1 is off limits for builds of any
kind:

```bash
python3 -m pip install --user faster-whisper
```

The `small` model downloads on first use (~460 MB) into the HuggingFace
cache. **Never point that cache under `$HOME/aimdk*`** — the SDK README
reserves those paths for the system and they are erased on firmware
upgrade. Pre-warm the cache before the demo, not during it:

```bash
python3 -c "from faster_whisper import WhisperModel; WhisperModel('small', compute_type='int8')"
```

### 2. `ANTHROPIC_API_KEY`

Same rule as Phase 1: set it in the environment of the launching shell.
Never in `config/conversation.yaml`, never committed.

```bash
export ANTHROPIC_API_KEY=...
```

### 3. Launching

```bash
ros2 launch x2_greeter conversation.launch.py
```

The venue comes from `venue.profile` in `config/conversation.yaml`. Pass
`venue:=mall_atrium` only to override the file for a single launch.

**The Phase 1 greeter and this node must not run at the same time.** Both
register as an MC input source *under the same name*, `x2_greeter`, at
priority 30 — so this is a name collision, not only priority contention:
the vendor can reject the second registration outright, leaving the node
that started second unable to gesture with nothing obviously wrong in its
log. Both also try to gesture and both hold audio focus. Check with
`ros2 node list | grep -E 'x2_greeter|x2_conversation'` before launching.

### 4. What ships off

`face.enabled`, `head.enabled`, `head.sweep_on_start`, `head.gaze_follow`,
and `base_frame.use_env_camera` all ship `false` in `config/conversation.yaml`.
See `docs/HARDWARE_BRINGUP.md` for the order to turn them on in — the order
is not arbitrary, and skipping it makes a real fault look like the wrong
change caused it.

### 5. Verifying `only_voice`

**This check is a human one; there is no service to read the mode back.**
The node calls `SetAgentProperties` at startup and logs whether the vendor
agent accepted `only_voice`, but that call cannot be verified later by
polling — `GetAgentProperties` does not exist in the SDK. Say something to
the robot and listen: if a second voice answers, the vendor agent is still
running its own dialogue and `only_voice` did not take.
