# AgiBot X2 Auto-Greeter — Design Spec

**Date:** 2026-09-02
**Status:** Approved for planning
**SDK:** AimDK `v1.0.0-ga424add` (aarch64 artifacts), ROS 2 Humble, Python 3.10

---

## 1. Purpose

When the X2's head camera sees a person standing in front of it, the robot greets
them: it speaks a greeting and performs a greeting gesture, with no operator input.

The greeting text and the choice of gesture come from a cloud LLM that looks at the
actual camera frame. When the cloud is unreachable, the robot still greets — with a
pre-written phrase and a randomly chosen gesture.

## 2. What the SDK already provides

| Capability | Interface |
|---|---|
| Head RGB-D camera | `/aima/hal/sensor/rgbd_head_front/rgb_image`, `.../depth_image` |
| Speech synthesis | `/aimdk_5Fmsgs/srv/PlayTts` |
| Audio file playback | `/aimdk_5Fmsgs/srv/PlayAudioFile` |
| Gestures | `/aimdk_5Fmsgs/srv/SetMcPresetMotion` (25 preset motions) |
| Robot mode query | `/aimdk_5Fmsgs/srv/GetMcAction` |

**Not provided:** person detection. The docs list the Perception/Vision module as
*"Coming Soon"* (§5.5.1). We implement detection ourselves against the raw image topic.

## 3. Non-goals

Out of scope for v1 — each deliberately deferred rather than forgotten:

- Face recognition, or greeting people by name.
- Head/gaze tracking to follow the person.
- Multi-person handling beyond "greet the most central person".
- `PlayEmoji` face-screen reactions and `SetPmuLed` lighting.
- Conversation. This is a greeting, not a dialogue; the microphone is untouched.
- Locomotion. The robot never walks toward anyone.

## 4. Architecture

### 4.1 Workspace layout

An **overlay colcon workspace**, `x2_greeter_ws`, separate from the vendor SDK. It
sources the AimDK install and builds on top of it.

The SDK quick start suggests adding packages to the SDK's own `src/`. We do not,
because the SDK README states that everything under `$HOME/aimdk*` is *"reserved and
maintained by the system"* and is erased on firmware upgrade. Our source lives in its
own git repository and overlays the SDK, so it survives a reflash.

```
source /opt/ros/humble/setup.bash
source ~/aimdk/install/local_setup.bash    # provides aimdk_msgs
colcon build                               # in x2_greeter_ws
```

### 4.2 The layering rule

**All behavioural logic is plain Python with no `rclpy` import.** Only a thin adapter
shell (`ros/`) touches ROS.

This is not decoration. It is what makes the system developable and testable on a
Windows workstation with no ROS installation, and it keeps the interesting logic in
files that can be reasoned about and unit-tested in isolation.

```
x2_greeter/
  core/                     pure Python — no I/O, no ROS
    types.py                Detection, SceneContext, Verdict, GreetEvent, Gesture
    detection.py            PersonDetector protocol, OpenCvDnnDetector, gating
    presence.py             PresenceTracker — the state machine
    gestures.py             gesture catalogue + selection policy
  cognition/                pure Python + outbound network
    port.py                 GreetingBackend protocol
    claude.py               ClaudeBackend (anthropic SDK)
    canned.py               CannedBackend (offline)
    policy.py               cloud-with-fallback orchestration
  ros/                      the only rclpy code
    greeting_node.py        wiring, executor, callback groups
    frame_source.py         rgb+depth approximate sync -> numpy
    speech.py               PlayTts + PlayAudioFile clients, tier demotion
    gesture.py              SetMcPresetMotion client
    mode_guard.py           GetMcAction — is gesturing safe right now?
  sim/
    fake_robot.py           synthetic frames + fake service servers
tools/
  make_greeting_audio.py    generate conformant 16k/16-bit/mono WAVs
  deploy_audio.sh           copy assets to PC3
```

## 5. Data flow

```
rgb_image   ─┐
             ├─► FrameSource ─► PersonDetector ─► gating ─► PresenceTracker
depth_image ─┘   (approx sync)   (bbox, conf)   (dist,       (dwell / loss /
                                                 centre)      cooldown)
                                                                    │
                                                                    │ PersonArrived
                                                                    ▼
                                                            GreetingPolicy
                                                    (worker thread, 2.5 s budget)
                                              ┌──────────────┴──────────────┐
                                        ClaudeBackend                 CannedBackend
                                     1 JPEG @ 512 px                (error / timeout /
                                     -> person_present,              no API key)
                                        facing_robot,
                                        greeting, gesture
                                              └──────────────┬──────────────┘
                                                             ▼
                                                    ModeGuard (STAND_DEFAULT?)
                                              ┌──────────────┴──────────────┐
                                          Speech                        Gesture
                                    (tiered, see §10)            SetMcPresetMotion
                                              └─── dispatched concurrently ───┘
```

The perception loop never blocks. The cloud call runs on a single-worker thread pool;
the ROS executor keeps processing frames at full rate throughout.

## 6. Detection and gating

`OpenCvDnnDetector` runs a MobileNet-SSD person class over every synced frame via
`cv2.dnn`. A detection must pass all three gates to count:

| Gate | Default | Rationale |
|---|---|---|
| Confidence | `>= 0.5` | reject noise |
| Distance | `1.0 m – 3.0 m` | median depth within the bbox; further is a passer-by, closer is unsafe to gesture at |
| Centring | bbox centre within middle 50% of frame width | someone at the edge of frame is not "in front of" the robot |

If the depth frame is missing, or the median depth in the bbox is invalid or zero, the
detection is **rejected**, never guessed. When several people pass the gates, the one
nearest frame centre wins.

Depth and RGB may differ in resolution; bounding boxes are scaled by the width/height
ratio between the two images. Exact encodings are a hardware validation item (§17).

## 7. State machine (`PresenceTracker`)

Pure, clock-injected, no I/O — the most heavily unit-tested component.

| State | Transition condition | Next |
|---|---|---|
| `IDLE` | a gated detection appears | `CANDIDATE` |
| `CANDIDATE` | dwell `>= 1.0 s` | `CONFIRMING` |
| `CANDIDATE` | no detection for `0.5 s` | `IDLE` |
| `CONFIRMING` | verdict: person present | `GREETING` |
| `CONFIRMING` | verdict: not a person | `COOLDOWN(5 s)` |
| `CONFIRMING` | backend error or timeout, **and** local detector still sees a person | `GREETING` (canned) |
| `GREETING` | speech + gesture dispatched | `COOLDOWN(30 s)` |
| `COOLDOWN` | elapsed `>= cooldown` **and** frame person-free for `>= 3 s` | `IDLE` |

The dwell gate is what makes cloud-primary vision viable: "someone in front of the
robot" means someone who *stopped*, so a ~1 s wait costs nothing and filters out people
merely walking past.

The compound cooldown exit is the "don't greet the same person forever" rule without
face recognition — the cooldown will not lift while you are still standing there. Walk
away, come back, get greeted again.

## 8. Cognition

### 8.1 The cloud call

**One call per greeting event, never per frame.** A naive per-frame cloud detector at
2 fps would cost roughly $50/hour of idle standing and add 1–2 s of latency to every
frame. Instead the local detector decides *when* to ask, and a single call answers both
"is this really a person?" and "what should I say and do?".

- Frame downscaled to 512 px on the longest edge, JPEG-encoded, base64.
- Model `claude-opus-5`, effort `low`, adaptive thinking left on. (Disabling thinking
  on Opus 5 has documented failure modes; lowering effort is the correct latency lever.)
- Structured output, schema:

```json
{
  "person_present": "boolean",
  "facing_robot":   "boolean",
  "confidence":     "number",
  "greeting":       "string",
  "gesture":        "enum of the enabled gesture names",
  "reason":         "string"
}
```

`facing_robot` is **advisory, never a gate.** A person with their back turned still gets
greeted; the field only lets the backend adapt its wording and gesture choice (a bow or a
salute aimed at someone's back is odd; a spoken hello is not). Because greeting does not
depend on orientation, the cloud and offline paths behave identically here — the local
detector's inability to judge orientation costs nothing.

- Hard client timeout: **2.5 s**. Exceeded means fallback, not a stall.
- Error handling chains most-specific-first (`NotFoundError` → `RateLimitError` →
  `APIStatusError` → `APIConnectionError`); every branch falls through to the canned
  backend rather than raising into the node.
- The exact structured-output parameter shape is to be read from the Anthropic Python
  SDK reference at implementation time, not recalled from memory.

### 8.2 Swapping providers

`GreetingBackend` is a Protocol:

```python
def confirm_and_compose(self, frame: JpegFrame, ctx: SceneContext) -> Verdict
```

`ClaudeBackend` and `CannedBackend` ship in v1. Adding Grok / GPT / Gemini is one new
file implementing the same Protocol plus a `backend` parameter value. Each adapter uses
its own vendor's official SDK — we do not build a lowest-common-denominator shim, so
the Claude path stays idiomatic.

## 9. Gestures

The interface docs list 25 preset motions; the SDK Python example exposes only four.
Enabled greeting set:

| Name | motion | area | | Name | motion | area |
|---|---|---|---|---|---|---|
| `wave` | 1002 | 2 / 1 | | `high_five` | 1008 | 2 / 1 |
| `salute` | 1013 | 2 / 1 | | `wave_chest` | 1011 | 2 / 1 |
| `handshake` | 1003 | 2 / 1 | | `cheer` | 3011 | 11 |
| `raise_hand` | 1001 | 2 / 1 | | `blow_kiss` | 1004 | 2 / 1 |
| `raise_both` | 1010 | 3 | | `heart` | 1007 | 3 / 2 / 1 |
| `bow` | 3001 | 11 | | | | |

Area 1 = left, 2 = right, 3 = both, 11 = whole body.

Available but disabled by default (one config line to enable): `hug` (3008),
`wave_goodbye` (3031) — a farewell, not a greeting — `clap` (3017), `cross_arms` (3009),
`scratch_head` (3024), `grab_buttocks` (3025), `dynamic_light_wave` (3007).

**Selection:** the cloud backend picks a gesture to match what it sees and says. The
returned name is **validated against the enabled allowlist** — an unrecognised value
falls back to random selection. LLM output never indexes a motion ID directly. Offline,
selection is random excluding the immediately previous gesture.

`gestures.hand_preference` applies **only** to gestures that have left/right variants
(`wave`, `salute`, `handshake`, `raise_hand`, `high_five`, `wave_chest`, `blow_kiss`,
`heart`). Whole-body gestures (`bow`, `cheer`) and both-arm gestures (`raise_both`, and
`heart` when `hand_preference` is `both`) ignore it. Setting it to `random` picks a side
per greeting.

## 10. Speech: three tiers

The documentation does not state whether `PlayTts` synthesises onboard or in the cloud.
Until hardware answers that, the design hedges:

| Tier | Path | Requires |
|---|---|---|
| 1 | cloud LLM text → `PlayTts` | internet + working TTS |
| 2 | canned phrase → `PlayTts` | working TTS only |
| 3 | pre-recorded WAV → `PlayAudioFile` | nothing |

`PlayTtsResponse.is_success` signals tier-2 failure, at which point the node demotes
itself to tier 3 for the remainder of the session and logs it once. If onboard TTS turns
out to work offline, tier 3 is simply never reached.

`speech.tier` selects the starting tier. `auto` (the default) starts at tier 1 and demotes
on failure as described above; `tts` pins the node to tier 2 (never calls the cloud for
text, never uses audio files); `audio_file` pins it to tier 3. Demotion is one-way within
a session — the node does not retry a tier that has failed until it is restarted.

**Audio asset constraints** (interface docs §5.2.1):

- 16 kHz, 16-bit, mono. WAV or raw PCM only — MP3 is rejected.
- Files must be stored on **PC3, the interaction compute unit (10.0.1.42)** — *not* PC2,
  the development unit where our node runs.
- The directory and all parents must be world-readable; a subdirectory of `/var/tmp/`
  is recommended.

`tools/make_greeting_audio.py` generates conformant WAVs from a phrase list;
`tools/deploy_audio.sh` copies them to PC3.

TTS requests use `domain="x2_greeter"` and `priority_level=6` (`INTERACTION_L6`), the
level the docs designate for user interaction.

## 11. Safety

- **The node never changes the robot's motion mode.** Preset motions require
  `STAND_DEFAULT`; if `GetMcAction` reports anything else, the robot **speaks but does
  not gesture**, and warns once. Auto-transitioning a humanoid into force-control stand
  is how a robot falls over, and the docs require both feet planted first — that stays a
  human decision.
- Gesture and speech are dispatched concurrently but independently: if one service is
  unavailable, the other still fires.
- The robot never moves its base. No locomotion commands are issued, ever.
- Distance gating refuses to gesture at anyone closer than 1.0 m.

## 12. Privacy

- **No image is written to disk, anywhere, ever.** Not as a cache, not as a debug
  artefact, not in a log. Frame buffers are released after the call returns.
- Image bytes are never logged, at any log level.
- A test asserts the image pipeline performs no filesystem writes.
- Frames *are* transmitted to the configured LLM provider at the moment of a greeting —
  inherent to cloud-primary vision, and accepted knowingly. Nothing is transmitted when
  no one is being greeted.

## 13. Configuration

All ROS 2 parameters, with defaults:

| Parameter | Default |
|---|---|
| `camera.rgb_topic` | `/aima/hal/sensor/rgbd_head_front/rgb_image` |
| `camera.depth_topic` | `/aima/hal/sensor/rgbd_head_front/depth_image` |
| `detect.confidence_min` | `0.5` |
| `detect.distance_min_m` / `detect.distance_max_m` | `1.0` / `3.0` |
| `detect.center_tolerance` | `0.25` |
| `presence.dwell_s` | `1.0` |
| `presence.loss_grace_s` | `0.5` |
| `presence.clear_s` | `3.0` |
| `presence.cooldown_s` | `30.0` |
| `presence.reject_cooldown_s` | `5.0` |
| `backend` | `claude` |
| `backend.model` | `claude-opus-5` |
| `backend.effort` | `low` |
| `backend.timeout_s` | `2.5` |
| `speech.tier` | `auto` |
| `speech.domain` | `x2_greeter` |
| `speech.priority_level` | `6` |
| `speech.audio_dir` | `/var/tmp/x2_greeter_audio` |
| `gestures.enabled` | the 11 names in §9 |
| `gestures.hand_preference` | `right` |
| `safety.require_stand_default` | `true` |

## 14. Failure modes

| Condition | Behaviour |
|---|---|
| Cloud unreachable / slow / 429 | canned phrase + random gesture; greeting still happens; WARN |
| `ANTHROPIC_API_KEY` unset | node starts in canned mode and says so at startup; does not exit |
| LLM returns malformed or out-of-allowlist gesture | random gesture from allowlist |
| `PlayTts` reports failure | permanent demotion to tier 3 for this session |
| Audio files missing on PC3 | gesture still fires; speech skipped; ERROR once |
| `SetMcPresetMotion` unavailable | speech still fires |
| Robot not in `STAND_DEFAULT` | speech only, no gesture; warn once |
| Depth frame missing or invalid | detection rejected |
| Camera topic silent > 5 s | WARN heartbeat; no crash |

The cloud and offline paths trigger on the same condition — a person present, in range,
centred. Orientation never gates a greeting (§8.1), so losing the cloud costs only the
quality of the wording and the aptness of the gesture, never the behaviour itself.

## 15. Testing

Test-driven throughout: tests precede implementation.

| Layer | Method | Runs on |
|---|---|---|
| `PresenceTracker` | pytest, injected clock, no I/O | Windows, now |
| Gating maths | pytest, synthetic bboxes + depth arrays | Windows, now |
| Gesture selection | pytest — allowlist validation, no-repeat, bad LLM output | Windows, now |
| `ClaudeBackend` | stubbed transport: valid / malformed / timeout / 429 / no-key | Windows, now |
| `GreetingPolicy` | fallback ordering and tier demotion | Windows, now |
| Privacy guarantee | assert no filesystem writes in the image path | Windows, now |
| Full ROS integration | `ros:humble` container, `aimdk_msgs` built from source, `fake_robot` node; assert services called in the right order with the right payloads | Docker, now |
| Hardware smoke | scripted first-window checklist | on robot, later |

`aimdk_msgs` ships all 168 `.msg` and 54 `.srv` as source, and its `CMakeLists.txt` falls
back to full source generation when the aarch64 prebuilts do not apply — so the **real**
message package builds for x86_64 in Docker. Integration tests use genuine message types,
not mocks.

## 16. Deployment

1. Build `aimdk` on the development compute unit (PC2, `10.0.1.41`).
2. Clone and build `x2_greeter_ws` as an overlay on PC2.
3. Generate greeting WAVs; deploy them to PC3 (`10.0.1.42`) under
   `/var/tmp/x2_greeter_audio`, world-readable.
4. Set `ANTHROPIC_API_KEY` in the node's environment.
5. Bring the robot to `STAND_DEFAULT` (`PASSIVE → JD → SD`) with a human present.
6. `ros2 launch x2_greeter greeter.launch.py`.

## 17. Open questions — resolve on first hardware access

These cannot be answered from the documentation, and are recorded rather than guessed:

1. Does `PlayTts` synthesise onboard, or does it require internet? Determines whether
   tier 3 is ever needed.
2. `depth_image` encoding (`16UC1` millimetres vs `32FC1` metres) and its resolution
   relative to `rgb_image`.
3. `rgb_image` encoding (`rgb8` / `bgr8`), resolution, and frame rate.
4. Does `PlayAudioFile` require `RequestAudioFocus` first?
5. Does PC2 have GPU/NPU acceleration usable by `cv2.dnn`, or is CPU inference at camera
   rate sufficient?
6. Preset motion duration, and whether motions can be issued back-to-back — needed to keep
   gesture and speech from desynchronising.

**Resolved:** PC2 has outbound internet by default — the robot's internet connection is
via PC2. Cloud-primary operation is sound.
