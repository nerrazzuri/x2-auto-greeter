# Bringing the X2 auto-greeter up on real hardware — a guide for an agent

**Audience:** a coding agent (Claude Code or equivalent) working from an Ubuntu
laptop on the same network as an AgiBot X2. It assumes you can open a shell on
the laptop and SSH to the robot's compute units, and that you have not seen this
codebase before.

`docs/DEPLOYMENT.md` is the operator runbook — the commands, in order. **This
document is the part the runbook does not tell you:** what the system is, what
has never been tested, what will go wrong first, and which lines you must not
cross. Read this one first, then work from the runbook.

---

## The one thing to know before anything else

**This has now run against a real robot, once.** On 2026-09-03 an AgiBot X2
greeted a person out loud and waved at them, using the Claude backend, and the
safety interlocks refused the gesture correctly whenever the robot was not in
force-control stand. That session is the source of everything in *What the
first hardware session found* below.

Do not read that as "it works". One session on one robot found **four** separate
defects, and every one of them presented as a system that looked healthy: the
node ran at 30 Hz and 200% CPU, printed nothing, and greeted nobody. Assume the
next robot has a fifth. What the session really bought you is that the unknowns
below are now measurements rather than guesses, and that the failure modes have
names.

Every test is still offline, against `x2_greeter/sim/fake_robot.py` — 283 of
them now, and "the fake robot accepted our service call" is still not evidence
about the real controller.

---

## Hard rules — do not cross these

1. **Never build or run anything on PC1 (10.0.1.40).** The AgiBot SDK
   documentation states this is *"strictly prohibited to avoid safety risks."*
   PC1 is the motion-control unit. The greeter runs on **PC2 (10.0.1.41)**;
   audio assets live on **PC3 (10.0.1.42)**.
2. **Never write anything into `$HOME/aimdk*`.** The SDK README declares those
   paths *"reserved and maintained by the system"* — they are erased on firmware
   upgrade. Clone the repo elsewhere under `$HOME`.
3. **Never store an image, anywhere.** The user's requirement is absolute: no
   frame may be written to disk — not a cache, not a debug dump, not a log line.
   `test/test_privacy.py` enforces this with a tripwire that is itself tested. If
   you add a debug path that saves a frame "just to check the camera", you have
   broken the product's one non-negotiable constraint.
4. **Never weaken a safety interlock to make a symptom go away.** The 1.0 m
   arm's-reach floor, the `STAND_DEFAULT` requirement, and the stale-reading
   refusal are load-bearing, and each is pinned by a test that fails if you
   remove it. If one of them is blocking you, that is information — diagnose it,
   do not disable it. `config/greeter.yaml`'s values are checked by
   `test/test_shipped_config.py` for exactly this reason.
5. **A human must be within reach of the robot's stop control for every run that
   can produce motion.** The greeter issues preset motions to a standing robot.
6. **Never commit `ANTHROPIC_API_KEY`.** It is read from the environment only.

---

## What you are deploying

A single ROS 2 Humble Python node, `x2_greeter/greeting_node`, that runs on PC2:

```
head RGB-D camera ──► person detector ──► presence state machine ──► greeting
   (two topics,          (MobileNet-SSD      (dwell / cooldown /       │
    time-synced)          or HOG fallback)    confirm window)          │
                                                                      ▼
                                         ┌────────────── cloud verdict (Claude)
                                         │               confirms the person,
                                         │               writes the line,
                                         │               picks the gesture
                                         ▼
                              speech ────────────── gesture
                         (PlayTts, else pre-       (SetMcPresetMotion,
                          recorded WAV on PC3)      refused if unsafe)
```

The architecture is ports-and-adapters. `core/` and `cognition/` contain no
`rclpy` import at all — `test/test_layering.py` fails if one appears — so the
detection, presence, gesture-choice and greeting-composition logic is all
testable without ROS. `ros/` is the only ROS-aware layer, and `sim/fake_robot.py`
is a stand-in that answers the four services the node calls.

**Failure is designed to degrade, not stop.** Cloud unreachable → canned phrase
list. `PlayTts` failing → pre-recorded WAV files on PC3. Not in `STAND_DEFAULT`,
or a person inside 1.0 m, or a stale distance reading → **the robot still
speaks, it just does not gesture.** Speech and gesture are deliberately
separable; if you find yourself "fixing" a case where it speaks without
gesturing, first check whether that is the interlock doing its job.

### The files worth reading before you touch anything

| Path | Why |
|---|---|
| `x2_greeter/ros/greeting_node.py` | the whole orchestration; start here |
| `x2_greeter/core/presence.py` | the state machine and every timing constant |
| `x2_greeter/core/gestures.py` | the motion-ID catalogue and how one is chosen |
| `config/greeter.yaml` | every tunable, with the unknowns commented |
| `docs/DEPLOYMENT.md` | the command-by-command runbook |

---

## Stage 0 — on the laptop, before you touch the robot

Prove the code is intact locally. Do not skip this: if something is wrong here,
you do not want to be discovering it while standing next to a humanoid.

```bash
git clone https://github.com/nerrazzuri/x2-auto-greeter.git   # private: needs gh auth
cd x2-auto-greeter
python3 -m pip install --user numpy opencv-python pyyaml "anthropic>=1.0" "pytest>=8"
python3 -m pytest -q
```

The `ros`-marked tests are deselected by default: they need `rclpy` plus the
vendor `aimdk_msgs`, which a laptop does not have. Everything else runs here.

If the two counts do not add up to the totals you see in a recent commit,
something is wrong with the checkout, not with the counts -- they grow.

**To run those 69 you need the vendor SDK archive, and it is not in this repo.**
`sdk/` is gitignored — the AimDK artifacts are AgiBot's redistributable, not
ours. `docker/run_tests.sh` expects to find
`sdk/aimdk-aarch64-a424add7-artifacts/src/aimdk_msgs` at the repo root. Copy the
SDK archive from the user's machine (or from the robot) into `sdk/` first, then:

```bash
docker build -f docker/Dockerfile.test -t x2-greeter-test .
docker run --rm -v "$PWD:/repo" -v x2-greeter-ws:/ws x2-greeter-test bash /repo/docker/run_tests.sh
```

### Running them on the robot instead

The robot has `aimdk_msgs`, so the `ros` tests can be run there — but **not on
the robot's own DDS domain**:

```bash
source /home/run/x2_greeter/env.sh
export ROS_DOMAIN_ID=77          # anything but 0
cd /home/run/x2_greeter/repo
python3 -m pytest -q -m ros -p no:anyio
```

`env.sh` leaves you on domain 0, which is the robot's live graph. These tests
stand up their own fake services and assert things like "the service is
absent" and "nobody else is publishing on this topic" — all false when the
real robot is answering. Measured: twelve tests fail on domain 0 and pass on a
private one, for that reason and no other. Chasing those twelve is a wasted
afternoon.

`-p no:anyio` is needed because `anyio`, pulled in by the `anthropic` and
`faster-whisper` installs under `$ROOT/deps`, registers a pytest plugin that
the robot's older pytest cannot load.

Also rebuild before running: the tests import the **installed** package from
`ws/install/`, not the source tree, so an `rsync` of the repo alone tests the
previous build.

```bash
cd /home/run/x2_greeter/ws && colcon build --packages-select x2_greeter
```

Expected: **69 passed / 181 deselected**, then **181 passed / 69 deselected**,
exit 0. The first build generates the vendor messages from source and takes
several minutes; it is cached in the named volume afterwards.

If you cannot get the SDK archive, the host suite alone is still a real gate —
it covers everything except the ROS service plumbing.

---

## Stage 1 — reach the robot

Get on the robot's network and confirm all three units respond:

```bash
for h in 10.0.1.40 10.0.1.41 10.0.1.42; do ping -c1 -W1 "$h" >/dev/null && echo "$h up" || echo "$h DOWN"; done
ssh agi@10.0.1.41       # PC2 — this is where you work
```

**The DDS configuration — do not improvise it.** Source the robot's own
environment and inherit whatever it uses:

```bash
source /agibot/software/entry/cfg/basic_env.sh
```

That script unsets `ROS_DOMAIN_ID` (so, domain 0), sets `ROS_LOCALHOST_ONLY=0`,
and — the part that matters — points `FASTRTPS_DEFAULT_PROFILES_FILE` at the
robot's own FastDDS profile, which restricts transports to SHM plus a
single-interface UDP whitelist.

**Getting the domain right and the profile wrong is the trap.** Without that
profile your participant does not match the robot's. `ros2 topic list` still
lists every topic, `ros2 topic hz` reports no error, and not one frame ever
arrives. It looks exactly like a camera that is powered but not streaming. This
cost most of a session before the orbbec driver's own log showed it publishing
happily to the very topics we could not receive.

`ros2 topic list` returning *nothing* is a domain mismatch. Topics that list
but never deliver is the profile.

---

## Stage 2 — get the code onto PC2

PC2 is the unit with internet access, so cloning directly is simplest. The repo
is private, so you need credentials on PC2 — a deploy key, a fine-grained PAT, or
`gh auth login`. Ask the user which they prefer; do not paste a token into a
shell history without saying so.

```bash
# on PC2, NOT in ~/aimdk*
mkdir -p ~/x2_greeter_ws/src && cd ~/x2_greeter_ws/src
git clone https://github.com/nerrazzuri/x2-auto-greeter.git
ln -s "$PWD/x2-auto-greeter/x2_greeter_ws/src/x2_greeter" x2_greeter
```

If PC2 has no GitHub access, push from the laptop instead:

```bash
rsync -av --exclude '.git' --exclude '__pycache__' \
      x2_greeter_ws/src/x2_greeter/ agi@10.0.1.41:~/x2_greeter_ws/src/x2_greeter/
```

---

## Stage 3 — dependencies and build

```bash
source /opt/ros/humble/setup.bash
# aimdk_msgs. NOT ~/aimdk/install/local_setup.bash — that install tree does not
# exist on a v1.0 firmware image; ~/aimdk holds SDK source and docs only, and
# the prebuilt_aarch64 tree inside it carries a reduced 28-service subset. The
# full 178-service build ships here, without colcon setup scripts, so put it on
# the paths by hand:
_AIMDK=/agibot/software/common
export AMENT_PREFIX_PATH="$_AIMDK:$AMENT_PREFIX_PATH"
export PYTHONPATH="$_AIMDK/local/lib/python3.10/dist-packages:$PYTHONPATH"
export LD_LIBRARY_PATH="$_AIMDK/lib:$LD_LIBRARY_PATH"
python3 -m pip install --user "anthropic>=1.0" pyyaml
sudo apt install ros-humble-cv-bridge python3-opencv python3-numpy

cd ~/x2_greeter_ws
colcon build --packages-select x2_greeter
source install/setup.bash
```

**A trap that will cost you an hour if you hit it blind:** editing
`config/greeter.yaml` in the *source* tree changes nothing at runtime. The
launch file loads the copy under `install/`. After every config edit, either
re-run `colcon build` or pass the file explicitly:

```bash
ros2 launch x2_greeter greeter.launch.py params_file:=/absolute/path/to/greeter.yaml
```

Also note that `package.xml` does not declare `python3-opencv` or
`python3-numpy` (a known gap). `rosdep` will not install them for you — the
`apt` line above is not optional.

---

## Stage 4 — assets and credentials

```bash
python3 tools/fetch_model.py --dest ~/models      # SSD weights; skip these and you get HOG
python3 tools/make_greeting_audio.py --dest assets/audio
tools/deploy_audio.sh assets/audio                # copies to PC3, world-readable
export ANTHROPIC_API_KEY=...                      # without it: canned phrases, with a warning
```

Then set `detect.model_dir: /home/agi/models` in the config. **Fetch the
weights.** The HOG fallback is slow, and its latency is the direct cause of the
most likely silent failure (see the symptom table). `tools/fetch_model.py` pins
no checksum — a known gap; eyeball the file sizes.

`deploy_audio.sh` puts the WAVs on PC3 at `/var/tmp/x2_greeter_audio`. If you
override `DEST`, run `namei -om <path>` and confirm every component from `/`
down is readable and traversable by the audio service's account. A permission
error here produces no error message anywhere — `PlayAudioFile` simply cannot
open the file, and it looks exactly like a missing recording.

---

## Stage 5 — verify the camera before you ever let the robot move

This is where the real unknowns live. Work through `docs/DEPLOYMENT.md` §8; the
two that matter most:

```bash
ros2 topic hz /aima/hal/sensor/rgbd_head_front/rgb_image
ros2 topic echo --field encoding /aima/hal/sensor/rgbd_head_front/depth_image --once
```

- **Check the orientation before anything else:** `python3
  tools/check_camera_orientation.py`, with somebody standing 2 m in front. On
  the first robot the head module was mounted upside down, and MobileNet-SSD
  does not recognise an inverted person while still recognising inverted
  furniture — so the node ran at full rate, scored `sofa=0.75` on every frame,
  and greeted nobody, silently. If the tool reports `rot-180` finding a person
  and `as-is` not, set `camera.rotate_180: true`.
- If the topics are wrong, fix `camera.rgb_topic` / `camera.depth_topic`. On a
  v1.0 image the RGB-D stream comes from the `orbbec_camera` service, which
  remaps onto exactly the topics this repo expects; the `rgbd_camera_module`
  block in `hal_sensor_orin`'s own config is commented out and is a red
  herring.
- **Depth encoding decides `camera.depth_scale`:** `16UC1` (millimetres) →
  `0.001`; `32FC1` (metres) → `1.0`. Measured on the first robot: `16UC1`,
  1280x720, ~30 Hz, so the shipped `0.001` is right — but measure, do not
  inherit this sentence. Getting this wrong by 1000× makes every
  distance gate meaningless — including the 1.0 m arm's-reach floor. Verify it
  by standing at a tape-measured 2 m and reading the logged distance, not by
  assuming.

---

## Stage 6 — bench run, no robot motion

```bash
ros2 launch x2_greeter greeter.launch.py fake_robot:=true
```

This starts the node alongside the simulated robot, so nothing physical moves.
Confirm the node starts, loads its parameters, and reports the camera topics.

---

## Stage 7 — first live run

Bring the robot to force-control stand **yourself, with a human present and feet
planted**. The greeter never changes the motion mode — by design — and it will
refuse to gesture rather than assume:

```bash
ros2 run py_examples set_mc_action PD   # passive
ros2 run py_examples set_mc_action JD   # position-control stand
ros2 run py_examples set_mc_action SD   # force-control stand — required for gestures
```

**Before the first live run, consider trimming `gestures.enabled`.** It ships
with eleven gestures, and two of them — `bow` (3001) and `cheer` (3011) — are
*whole-body* motions on a standing humanoid, not arm waves. For the very first
session, cut the list to arm-only motions and add the whole-body ones back once
you have watched the robot execute a few:

```yaml
gestures:
  enabled: [wave, salute, raise_hand, raise_both, blow_kiss, heart]
```

Then:

```bash
ros2 launch x2_greeter greeter.launch.py
```

Walk into frame at about 2 m, centred. Expect: the state machine advances, one
greeting is spoken, one gesture fires, then a 30 s cooldown before the same
person can trigger another.

---

## What the first hardware session found

Four defects, on one robot, in one afternoon. Every one of them looked like a
healthy system. Read this before you debug anything, because the shape they
share is more useful than the individual fixes: **on this platform, the
default-zero field and the silently-unmatched endpoint are the two ways things
break, and neither prints an error.**

| # | What was wrong | What it looked like | Fixed by |
|---|---|---|---|
| 1 | `FASTRTPS_DEFAULT_PROFILES_FILE` not set | every topic lists, `topic hz` is silent, zero frames | source the robot's `basic_env.sh` (Stage 1) |
| 2 | head camera mounted upside down | 30 Hz, 200% CPU, `sofa=0.75`, never a person, no log line | `camera.rotate_180` (Stage 5) |
| 3 | `backend.timeout_s: 2.5` unreachable (measured 3.1-6.0 s) | every cloud call timed out into a canned phrase | raised to 8.0 s, from measurement |
| 4 | `McAction.value` left at default zero by the controller | "robot is in motion mode 0", gesture refused forever, robot demonstrably standing | read `action_desc` when the id is unset |

**The lesson worth carrying:** #3 and #4 both come from trusting a number the
vendor never populated. The codebase already knew this shape — `ResponseHeader.
code` is default-zero, which is why `gesture.py` reads `state` first — but the
same trap has at least three more instances: `McAction.value`, `task_id`
(populated on rejection, zero on success), and `McActionInfo.status`. When a
vendor field reads as zero, establish whether zero is a value in that enum
before believing it. For `McAction` it is not: the enum starts at 1.

### Things that turned out **not** to be prerequisites

Time was spent on both of these before hardware settled them:

- **`Develop_MC` system state.** Preset motions work with the system in
  `Business`. The state migration matters for other MC interfaces, not this one.
- **MC input source registration.** Gestures were confirmed working on hardware
  with `SetMcInputSource` failing on every attempt, and
  `SetMcPresetMotion.Request` carries no source field for the arbiter to read.
  The greeter registers anyway, best-effort, because the docs say unknown
  sources are discarded — but it is not what stands between you and a wave.

### Solved during the session: do not ship a one-gesture config

Gestures refused in strict alternation — accept, reject, accept, reject — with
`state=FAILURE` back in ~9 ms (a refusal, not a timeout) while the mode read
`STAND_DEFAULT`/`RUNNING` throughout. It looked like arbitration.

It was the gesture list. `GestureSelector._random_name` deliberately avoids
repeating its previous choice:

```python
pool = [name for name in self._enabled if name != self._last]
if not pool:
    pool = list(self._enabled)
```

With `gestures.enabled: [wave]` the pool is empty every time, so the fallback
sends the same motion id on every greeting — and the controller refuses a
preset motion repeated back to back. Widening the list to the full eleven made
the refusals stop completely, because the selector then never repeats.

So: **a single-entry `gestures.enabled` refuses roughly every other gesture.**
The trim-the-list advice for a first live run is still good, but trim to a
handful of arm-only motions, not to one.

Established: the alternation, the selector's behaviour, and that it stopped
when the list widened. Inferred, not isolated by experiment: that the
controller's rule is specifically "no identical preset motion twice in a row".
If you need that precisely, ask for the same motion twice deliberately and
watch — do not reach for `interrupt=True` in `gesture.py`, which would paper
over a refusal that is telling you something true.

### Still unexplained

- **`aimdk_msgs` ships in several versions on one robot** (0.8.24 under
  `/agibot/software/common` and on PC1's `mc`; 1.2.2 bundled with
  `orbbec_camera`). Matching PC1's version is what matters; that it is even a
  question is worth knowing.

---

## When it does not work

| Symptom | Most likely cause | What to do |
|---|---|---|
| Node starts, nothing ever happens, no errors, CPU near zero | `FASTRTPS_DEFAULT_PROFILES_FILE` not set — topics list, no frames arrive | source `basic_env.sh`; confirm with `tools/check_camera_orientation.py`, which says so explicitly when it receives nothing |
| Node starts, **CPU is high**, nothing ever happens, no errors | frames are arriving and the detector finds nobody — camera orientation | `python3 tools/check_camera_orientation.py` with a person in frame; set `camera.rotate_180` if it says so |
| Greetings are always canned though `backend.provider: claude` | every cloud call exceeding `backend.timeout_s` | measure the real latency before touching the timeout; 2.5 s was unreachable on hardware |
| "robot is in motion mode 0", but the robot is plainly standing | controller left `McAction.value` at its default zero | already handled — the guard reads `action_desc`. If you see this on a *new* firmware, check whether zero is a member of the enum before believing it |
| Node starts, nothing ever happens, no errors | wrong `camera.rgb_topic` | **This is the failure the watchdog cannot warn about** — see Known gaps. Check `ros2 topic hz` on the configured topic yourself; do not wait for a log line. |
| `ros2 topic list` is empty | DDS domain mismatch | Stage 1 — match `ROS_DOMAIN_ID`/RMW to the robot's own environment |
| **Speaks, but never gestures** | the interlocks doing their job | Check, in order: is the robot in `STAND_DEFAULT`? Is the person inside 1.0 m? Is the distance reading stale? All three are logged. The third is the sneaky one — next row. |
| Speaks but never gestures, and the log names an age | detection slower than `presence.loss_grace_s` (0.5 s) | `_on_frame` runs the detector on every synced frame with no throttling, so the reading refreshes at detector speed. HOG on real hardware may not keep up. **Fix by fetching the SSD weights, not by raising `loss_grace_s`** — that bound is what enforces the arm's-reach floor under the shipped config. |
| Greetings are generic, or a backend warning appears | no `ANTHROPIC_API_KEY`, or the 2.5 s budget expired | Check the key first. If the cloud is reachable but slow, the 2.5 s timeout is a guess made without measurement — revisit it deliberately, and record the real latency. |
| Silence on tier 3 with no error at all | audio file not readable by the audio service on PC3 | `namei -om` the whole path; check `speech.audio_file_count` matches the file count |
| Distances are absurd (metres vs millimetres) | `camera.depth_scale` | Stage 5 |

---

## Known gaps — read this before you debug anything

These were found by review and deliberately left open. They are not recorded in
the code's comments; this list is the only record.

1. **No perception observability — fix this first, before the first hardware
   session.** `docs/DEPLOYMENT.md` tells the operator to watch for log lines that
   are never actually printed, and `ros/frame_source.py`'s watchdog returns early
   when no RGB frame has *ever* arrived. So the single most likely first failure
   — a wrong camera topic — is the one case that produces no warning at all.
   Adding a periodic "frames received / detections / gate rejections" log line,
   and making the watchdog fire when nothing has arrived, is a small change that
   will save you the whole session. Do it on the laptop, with tests, before you
   go.
2. **The default detector's geometry is untested.** `detect.model_dir` ships
   empty, so HOG is what actually runs, and corrupting every bounding box still
   passes the suite. Do not assume the HOG path's coordinates are validated.
3. **The 1.0 m floor can be masked in a crowd.** The node gestures toward the
   most *central* gated detection, not the nearest. A second person further away
   can keep the reading fresh and far while somebody closer steps inside the
   floor. Fine for a controlled first session; not a guarantee in a crowd.
4. **`cognition/policy.py` counts cloud fallbacks and nothing reads the counter**
   — there is no aggregate signal for "the cloud has been down for an hour."
5. **`ros/service_call.py` retries up to 8 times at 0.25 s** and can leave
   duplicate motion requests in flight if the controller is merely slow rather
   than absent.
6. **`PlayAudioFile` may require `RequestAudioFocus` first.** Unimplemented,
   because it could not be tested. If tier 3 is rejected outright, this is the
   first thing to suspect.

---

## Working style for this codebase

- **Tests first, on the laptop.** Both suites are fast (about 2 s host, ~50 s
  container). Any change to `core/` or `cognition/` needs no robot at all.
- **Ground every vendor claim in the SDK source**, never in a description of it —
  `.msg`/`.srv` under
  `sdk/aimdk-aarch64-a424add7-artifacts/src/aimdk_msgs/interface/`, reference
  clients under `.../src/py_examples/py_examples/`. This codebase already shipped
  one Critical because a response contract was written from a description rather
  than the file: `ResponseHeader.code` is a **default-zero** field, so a
  controller that reports failure only through `status`/`state` reads as success
  to anything that checks the header. Check `status`/`state` first. The vendor
  also genuinely misspells one response field `reponse` — that is theirs, and the
  code matches it on purpose.
- **If a test passes when it should not, that is the finding.** The most valuable
  checks in this repo were found by deleting a line and seeing the suite stay
  green.
- **Report what actually happened**, including which checks you skipped. The user
  is doing this on a real humanoid; a confident "it works" that has not been
  observed is worse than an honest gap.
