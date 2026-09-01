# Deploying the X2 auto-greeter

**Never build or run on PC1 (10.0.1.40).** The SDK documentation states this is
"strictly prohibited to avoid safety risks". The greeter runs on **PC2**
(10.0.1.41); its audio assets live on **PC3** (10.0.1.42).

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
