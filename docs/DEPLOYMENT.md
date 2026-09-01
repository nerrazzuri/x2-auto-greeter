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
| `PlayTts` works offline | pull the network, then greet | if it fails, set `speech.tier: audio_file` |
| `PlayAudioFile` needs audio focus | force tier 3 and watch for a rejection | if so, call `RequestAudioFocus` first — currently not implemented |
| Gesture timing | watch a full greeting | if speech and gesture desynchronise, tune per-gesture duration |
| CPU headroom | `top` while the node runs | switch `detect.detector` to `hog`, or lower the frame rate |
