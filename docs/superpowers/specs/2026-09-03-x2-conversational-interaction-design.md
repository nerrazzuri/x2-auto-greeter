# AgiBot X2 Conversational Interaction — Design Spec

**Date:** 2026-09-03
**Status:** Approved for planning
**SDK:** AimDK `v1.0.0-ga424add` (aarch64 artifacts), ROS 2 Humble, Python 3.10
**Builds on:** [`2026-09-02-x2-auto-greeter-design.md`](2026-09-02-x2-auto-greeter-design.md)

---

## 1. Purpose

Phase 1 greets. This phase holds a conversation.

When the X2 sees a person, it opens with a greeting that reflects where it is and
who it is looking at, then takes turns: the person speaks, the robot listens,
answers out loud, gestures, changes its face, and looks at whoever it is talking
to. The exchange continues until the person leaves, stops talking, or the session
hits a limit.

The robot's answers are grounded in three things, in this order of authority: a
**venue profile** written by whoever deployed it, a **wide environment frame**
taken once at the start of the session, and a **fresh camera frame every turn**.

## 2. What Phase 1 already provides, unchanged

`core/detection.py`, `core/detectors.py`, `core/presence.py`, `core/imaging.py`,
`ros/frame_source.py`, `ros/speech.py`, `ros/gesture.py`, `ros/input_source.py`,
`ros/interaction_guard.py`, `ros/mode_guard.py`, `ros/service_call.py`,
`sim/fake_robot.py`, and `cognition/canned.py` are reused as they stand. The
safety invariants in §14 are Phase 1's, carried forward verbatim.

`core/gestures.py` and `cognition/claude.py` are extended, not replaced.

## 3. What the SDK provides for this phase

| Capability | Interface | Verified? |
|---|---|---|
| Denoised, VAD-segmented speech audio | `/agent/process_audio_output` (`ProcessedAudioOutput`) | doc only |
| VAD state | `AudioVadStateType`: `NONE=0x00 BEGIN=0x01 PROCESSING=0x02 END=0x03` | enum pinned |
| Speech out | `/aimdk_5Fmsgs/srv/PlayTts` | **hardware (Phase 1)** |
| Face screen | `/aimdk_5Fmsgs/srv/PlayEmoji` (`uint8 emotion_id`, `uint8 mode`, `int32 priority`) | doc only |
| Face screen status | `/face_ui_proxy/status` (`FaceEmojiStatus`, RELIABLE, 1 Hz) | doc only |
| Head yaw | `/aima/hal/joint/head/command` (`JointCommandArray`) | doc only |
| Head joint feedback | `/aima/hal/joint/head/state` (`JointStateArray`, TRANSIENT_LOCAL) | doc only |
| Wide environment camera | `/aima/hal/sensor/stereo_head_front_left/rgb_image/compressed`, 10 Hz | doc only |
| Agent behaviour switches | `/aimdk_5Fmsgs/srv/SetAgentPropertiesRequest` (`AgentPropertyIdType`) | enum pinned |

**Not provided: speech recognition.** The SDK hands us segmented PCM and nothing
more. We transcribe it ourselves (§6).

### 3.1 Audio format

16 kHz / 16 bit / mono / PCM / S16LE. This is the only format the vendor
documents, on both the capture and the playback side.

### 3.2 `only_voice` is a deployment step, not a runtime call

Setting `RUN_MODE=only_voice` through `SetAgentPropertiesRequest` "updates the
local configuration file and disables the large model" and then requires
`aima em stop-app agent` + `aima em start-app agent` (FAQ §8.1). It persists
across reboots.

Our node therefore **never sets it**. It is a documented deployment step, with its
restore command, in `docs/DEPLOYMENT.md`. The node **verifies** it indirectly: if
`/interaction/tts_status` shows the built-in assistant answering a person, the
robot is still in `normal` mode — the node logs an error and refuses to enter
dialogue, because two assistants sharing one speaker is worse than none.

## 4. Non-goals

Deferred deliberately, each with the reason:

- **Locomotion.** The robot never walks, turns its base, or rotates its waist.
  Head yaw only (§9.1).
- **Barge-in.** There is no documented echo cancellation in `only_voice` mode, so
  the robot would hear itself. VAD segments arriving while it is speaking (plus a
  0.5 s guard) are discarded. The fix is the raw-audio phase.
- **Raw audio, our own VAD, our own denoising.** Later phase. `AudioSource` is a
  port so that phase is a swap, not a rewrite.
- **Malay.** Vendor TTS is `zh`/`en` only. Malay needs cloud speech synthesis over
  `/aima/hal/audio/playback` with audio focus — a second vendor, per-character
  billing, and a new failure chain. English and Chinese this phase. `Synthesizer`
  is named as an extension point in §17, **not** built now as an empty abstraction
  with one implementation.
- **Face recognition, or remembering anyone between sessions.**
- **Wake-word customisation.** The wake word is vendor-fixed and undocumented.
- **Streaming dialogue.** Turn-based (§7).

## 5. Architecture

Phase 1's hexagonal split holds: no `rclpy` outside `ros/` and `sim/`, enforced by
`test/test_layering.py`.

```
core/          conversation.py   session state machine
               scene.py          scene snapshot + addressing-mode decision
               venue.py          venue profile: load, validate, render to prompt
               gaze.py           person bbox -> head yaw, pure arithmetic
               gestures.py       Phase 1, extended (§9.2)
               detection.py detectors.py presence.py imaging.py types.py   Phase 1

cognition/     transcriber.py    Transcriber.transcribe(pcm, rate) -> Utterance
               dialogue.py       DialogueBackend.respond(...) -> Turn
               claude.py canned.py policy.py    Phase 1: turn 0, and offline fallback

ros/           audio_source.py   subscribes ProcessedAudioOutput, assembles BEGIN..END
               agent_mode.py     verifies only_voice; never sets it
               face.py           PlayEmoji + /face_ui_proxy/status
               head.py           head yaw command + joint-state health
               env_camera.py     one wide stereo frame per session
               conversation_node.py
               speech.py gesture.py frame_source.py input_source.py
               interaction_guard.py mode_guard.py service_call.py         Phase 1

config/        conversation.yaml
               venues/clothing_store.yaml
               venues/mall_atrium.yaml
```

Every outward-facing capability is a port with a fake in `sim/`: `AudioSource`,
`Transcriber`, `DialogueBackend`, `Speech`, `Gesture`, `Face`, `Head`,
`FrameSource`, `EnvCamera`.

## 6. Hearing: from microphone to text

### 6.1 Segmentation

`ros/audio_source.py` subscribes `/agent/process_audio_output` and assembles one
utterance from the VAD stream:

- `BEGIN` — start a new buffer. The vendor emits a **burst of cached audio** at
  this point (the pre-roll that triggered the VAD), then settles to ~25 Hz.
- `PROCESSING` — append.
- `END` — close the buffer and emit it as one utterance.
- `NONE` — ignore.

The subscription QoS depth is large (default 100). The vendor's own best-practice
note says "the receive queue of VAD should be large enough", and the opening burst
is exactly what overruns a shallow queue — a dropped burst silently truncates the
first word of every sentence.

Guards:

- A buffer that exceeds `audio.max_utterance_s` (20.0) is closed and transcribed
  anyway; a missing `END` must not leak memory forever.
- A `BEGIN` arriving with a buffer already open closes the old one first.
- A segment whose `stream_id` differs from the open buffer's starts a new buffer.
- Everything arriving while the robot is speaking, and for `audio.self_guard_s`
  (0.5) afterwards, is **discarded** — that is the robot hearing itself.

### 6.2 Transcription

`Transcriber.transcribe(pcm: bytes, sample_rate: int) -> Utterance` where
`Utterance = (text: str, language: str, confidence: float)`.

The shipped implementation is **`faster-whisper` running locally on PC2**. PC2 is
a Jetson Orin NX: 157 TOPS, 1024-core Ampere GPU with 32 Tensor Cores, 8-core
A78AE, 16 GB. The `small` model handles English and Chinese and returns a detected
language, which drives §10.

This choice means **zero new third-party vendors** for the whole loop, and speech
recognition that keeps working with the network down.

An empty or whitespace-only transcript is not a turn: the robot ignores it and
keeps listening, without spending a turn from the budget.

**No test loads the Whisper model.** Tests use a scripted `Transcriber`.

## 7. The conversation

### 7.1 State machine (`core/conversation.py`)

```
IDLE ──person confirmed──> GREETING ──> LISTENING ──utterance──> THINKING
                                            ^                       |
                                            |                       v
                                            +─────────────────── SPEAKING
                                                                    |
   CLOSING <──── limit hit / end:true / subject lost / silence ──────+
      |
      v
    IDLE  (cooldown)
```

Pure Python: no I/O, no clock of its own. It is driven by injected events and an
injected time source, so every path is testable offline.

- **GREETING** — turn 0. The environment base frame (§8.2) and the venue profile
  produce an opening line before the person has said anything. Phase 1's greeting
  path is the fallback when the cloud is unreachable.
- **LISTENING** — waiting for a VAD utterance.
- **THINKING** — the thinking emoji goes up at t≈0.05 s, then the cloud call runs.
- **SPEAKING** — `PlayTts`, with gesture and emoji in parallel; audio ignored (§6.1).
- **CLOSING** — say a closing line, return the head to centre, clear the face,
  enter cooldown.

### 7.2 Limits

| Parameter | Default | What it protects |
|---|---|---|
| `silence_timeout_s` | 15.0 | a person who walked off mid-sentence |
| `max_turns` | 8 | cost, and monopolising one visitor |
| `session_max_s` | 180.0 | a stuck session |
| `subject_lost_s` | 5.0 | talking to nobody |
| `reply_max_chars` | 200 | the 4-8 s latency (§13) — long answers make it worse |
| `cooldown_s` | 30.0 | the same person re-triggering immediately |

Every limit is a config parameter, and every one has a test that drives the state
machine across it.

### 7.3 Addressing mode

Decided **once**, at session start, and never revisited:

- If anyone is closer than `addressing.individual_max_m` (2.0), the mode is
  **INDIVIDUAL**, and the subject is the most central of those inside 2.0 m.
- If everyone is at or beyond 2.0 m, the mode is **GROUP**.

Locking it is deliberate. A robot that switches between "you" and "everyone"
mid-conversation because somebody drifted across a threshold reads as broken.

## 8. Seeing: three layers

Environment is not inferred from a photograph. It is **authored**, then confirmed.

### 8.1 Layer 0 — the venue profile (`core/venue.py`, `config/venues/*.yaml`)

Static, per deployment, selected by the `interaction.venue_profile` parameter.

```yaml
venue:
  kind: clothing retail store
  role: greet at the door and help people find things
  language_default: en
  opening: "Hi there! Welcome in - looking for anything in particular today?"
  facts:
    - the fitting rooms are at the back right of the store
    - this week's new arrivals are on the first rack to your right as you come in
    - exchanges are handled by a colleague at the counter
  topics_encouraged:
    - welcome
    - directions inside the store
    - a compliment on what they picked up
    - handing off to a colleague
  topics_forbidden:
    - specific prices or discounts
    - whether something is in stock
    - returns policy detail
    - promising anything
  deflect_to_human: "That one's better answered by my colleague at the counter - shall I point you over?"
```

Rules, enforced in the prompt and testable:

- **The `facts` list is the only thing the robot may assert about this place.**
  Anything outside it goes to `deflect_to_human`. An empty `facts` list — which is
  what the mall atrium profile ships with — means the robot asserts nothing at all.
- `topics_forbidden` is never discussed, even when asked directly.
- The profile is **authority**. If the environment frame contradicts it, the node
  logs a misconfiguration warning and **still follows the profile**. A robot that
  decides mid-session that it is somewhere else and starts improvising is more
  dangerous than one that is configured wrong but predictable.

`venue.py` validates on load and refuses to start on a malformed profile: a
missing `kind`, `role` or `opening`, a non-list `facts`, an unknown
`language_default`.

Two profiles ship: `clothing_store.yaml` and `mall_atrium.yaml`.

### 8.2 Layer 1 — the environment base frame

One frame, once per session, from
`/aima/hal/sensor/stereo_head_front_left/rgb_image/compressed`.

The reason is measured, not stylistic. The head RGB-D's colour FOV is
**94° x 68°**. A person at 1.5 m fills it; the room is out of frame. The front
stereo camera is **156° x 120°** — the only forward sensor wide enough to see a
venue rather than a torso, and it is on PC2 with a compressed topic.

If the stereo topic is unavailable, the session falls back to the head-sweep
frames (§9.1) and, failing those, to the profile alone.

### 8.3 Layer 2 — the per-turn observation

A fresh head-camera frame every turn, sent with the prompt. This is what makes the
robot respond to *now*: what the person is holding, whether they turned away,
whether a second person joined.

### 8.4 What the model is asked for

The prompt is **profile + base frame + per-turn frame + history + this utterance**.

The model is asked to observe, not to identify: how many people, a coarse age
band, posture, what they are holding or doing, notable nearby objects. Never a
name, never an identity, never a judgement about a person's appearance beyond what
is needed to speak naturally to them.

### 8.5 Children

The coarse age band comes from the model **and** a local physical cross-check:
bounding-box height at a known depth gives an approximate stature, so the
classification does not depend on the cloud alone. Child mode engages when both
agree.

Child mode changes behaviour by **rule, from the profile, not by model
judgement**:

- Shorter sentences, simpler words, playful gestures.
- **Never ask for personal information** — no name, no where they live, no where
  their parents are.
- **Never promise anything.**
- **Never tell them to come closer, to follow, or to reach out.** This one is
  physical: the arm's-reach interlock floor is 1.0 m, and a child who is invited
  closer walks straight through it.
- If no adult appears to be with them, shorten the session and close early.

## 9. Moving: head, gesture, face

### 9.1 Head yaw (`ros/head.py`, `core/gaze.py`)

`/aima/hal/joint/head/command`, `JointCommandArray`, **2 entries in the order
`[head_yaw, head_pitch]`**. Head pitch is mechanically fixed at 0° on the X2 Ultra
and the docs say "only yaw now, and pitch is unavailable" — we always send 0.0.

**The documented yaw range is ±20°** (`1.7.3 Head Motion Range`). We clamp to
**±15° (±0.262 rad)** in `core/gaze.py`. The docs warn that exceeding a joint
limit "may cause mechanical damage", and the margin costs nothing.

Two uses, one port:

- **Session-start sweep**, run *during* the opening line so it costs no latency:
  centre → −0.262 rad → hold → +0.262 rad → hold → centre. Interpolated at 20 Hz
  over ~1.0 s per leg; the docs' best practice is "use smooth trajectory planning
  to avoid sudden joint motions". Frames are captured only at the hold points —
  the camera runs at 10 Hz and anything grabbed mid-sweep is motion-blurred.
- **Per-turn gaze**: `gaze.yaw_for(bbox_centre_x, image_width)` maps the subject's
  horizontal offset to a yaw, clamped. In GROUP mode the head drifts slowly across
  the group instead of locking onto one person.

**The sweep is not a way to see more.** 94° plus 40° of sweep is 134°, which is
less than the 156° a single stereo frame already covers. The sweep exists because
a robot that visibly looks around before answering reads as considering the room,
and because it fills the 4-8 s response gap (§13). Its frames are a bonus and a
fallback, not the primary environment source.

Discipline:

- Return to 0.0 on every exit path: session end, timeout, exception, node
  shutdown.
- Refuse to move if `/aima/hal/joint/head/state` is stale, or its
  `DomainErrorState.value` is 2 (PowerOff), 3 (Disabled) or 4 (Communication
  Failed).
- `HEAD_FOLLOW` is set to `false` so the vendor agent and this node do not fight
  over the neck. Whether that takes effect live, or needs the agent restart that
  `RUN_MODE` needs, is §18.

Head motion does not violate the no-locomotion rule: the neck does not change the
support polygon, and the robot stays where it is.

### 9.2 Gestures (`core/gestures.py`, extended)

Phase 1's catalogue, plus one correction and the change it forces.

The vendor's two sources **disagree**, and neither is a superset of the other:

- `McPresetMotion.msg` (the enum) omits `1007`, `1010`, `1011`, `3017`, `3024`,
  `3025`, `3031`.
- The preset-motion doc's "currently supported combinations" table omits
  `4001`/`4002` and control area `HEAD=4` entirely.

They are not always in conflict. `INTERACTION_SWEATHEART = 3004` is annotated
"头顶比心" — a heart *above the head* — while the doc table's `1007` at area 3 is
the two-handed heart at the chest. **Both are real; each source listed half of
them.**

Corrections to the catalogue:

| name | was | is | area | source |
|---|---|---|---|---|
| `heart` | 3004, area 11 | **1007** | **3 (both hands)** | doc table |
| `heart_overhead` | — (new) | 3004 | 11 | enum |

`heart` is `handed=False` with `areas=(AREA_BOTH,)`, so a `hand_preference` of
`right` cannot silently downgrade a two-handed heart to a one-handed one.

`heart_overhead` ships **disabled and unverified**. The enum names the motion but
says nothing about its control area; area 11 is our inference from the other
3xxx whole-body motions, and an inference is not a source.

`test_catalogue_ids_are_real_vendor_motions` currently pins every ID against the
enum, which `1007` would fail. It widens to: **every `(motion_id, area_id)` pair
must have a named vendor source** — the `McPresetMotion` enum, or the doc's
supported-combinations table transcribed into the test — and each `GestureSpec`
records which one vouches for it. It still catches an invented ID, which is the
only reason it exists.

A `hw_verified` flag records what has actually been seen on hardware. So far that
is `wave` (1002, area 2) and nothing else; `heart` at 1007/3 is scheduled for the
next hardware window.

Other catalogue entries where the two sources differ — `wave_chest` (3010 vs
1011), `raise_both` (1001/3 vs 1010/3), `clap` (3015 vs 3017) — are **not changed
by this spec**. They are a Phase 1 bench-check item (§18, item 10).

### 9.3 Face (`ros/face.py`)

`/aimdk_5Fmsgs/srv/PlayEmoji` with `uint8 emotion_id`, `uint8 mode` (1 once,
2 loop), `int32 priority`. Status arrives on `/face_ui_proxy/status`
(`FaceEmojiStatus`, RELIABLE, 1 Hz), states 1/3/4 edge-triggered.

**`emotion_id` is a bare `uint8` with no enum message anywhere in the SDK.** The
23 IDs exist only in a table in the docs (1 Blink, 60 Bored, 90 Happy, 100-101
Ecstatic, 110 Sad, 120 Sympathy, 130 Confused, 140 Shocked, 150 Acting cute,
160 Serious, 170 Thinking, 180 Angry, 200 Adoration, …). There is nothing to pin
against, so it is handled the way an unpinnable value has to be:

- Each catalogue entry carries a `verified: bool`.
- **Every emoji ships `verified: false` and disabled**, and the face stays blank
  until someone confirms an ID on hardware.
- `test_shipped_config.py` asserts that the shipped config enables only verified
  emoji — so enabling one without verifying it fails the suite.

The model supplies an emoji **name**, validated against the enabled allowlist, and
never a numeric ID. Same rule as gestures, same reason.

The **thinking emoji at t≈0.05 s** is the one exception worth its own note: it is
dispatched before the cloud call, not after, because it is the only thing standing
between the person and 4-8 s of silence.

## 10. Language

English is the default. Language is **explicit state on the session**, not
re-derived per turn:

- The session opens in the profile's `language_default`.
- If the transcriber reports a different language, with confidence above
  `language.switch_confidence` (0.7), for a whole utterance, the session switches
  and stays switched.
- The model is told the current language and must answer in it. The `language`
  field it returns is validated against `{en, zh}`; anything else is rejected and
  the session language is used instead.
- `PlayTts` is given the matching text. Vendor TTS covers `zh` and `en` only,
  which is exactly the set we allow.

A single stray word in another language must not flip the conversation, which is
what the confidence floor and the whole-utterance requirement are for.

## 11. The turn contract

`DialogueBackend.respond(base_frame, frame, scene, venue, history, utterance)
-> Turn`.

The model returns JSON:

```json
{
  "reply":    "...",
  "language": "en",
  "gesture":  "wave",
  "emoji":    "happy",
  "end":      false
}
```

Validation, before any of it reaches the robot:

- `reply` — non-empty, truncated to `reply_max_chars` (200) at a sentence
  boundary.
- `language` — must be `en` or `zh`; otherwise the session language is used.
- `gesture` — must be on the enabled allowlist; otherwise `None`. **Never a
  numeric ID.**
- `emoji` — must be on the enabled allowlist; otherwise `None`. **Never a numeric
  ID.** With no emoji verified, this is always `None` until §18 is done.
- `end` — anything non-boolean is treated as `false`.

Malformed JSON, a missing field, or a timeout is not a crash: it degrades per §12.

Model: `claude-opus-5`, `output_config={'effort': 'low'}`,
`thinking={'type': 'adaptive'}`. **No `budget_tokens`** — Opus 5 rejects it with a
400. `ANTHROPIC_API_KEY` comes from the environment only and is never committed.
No test makes a real API call.

## 12. Failure modes

Every one degrades; none stops the robot mid-sentence with a stack trace.

| What fails | What happens |
|---|---|
| Cloud unreachable or times out | Turn 0 falls back to Phase 1's canned greeting. On a later turn the robot speaks a canned bridging line and moves to CLOSING — it does not retry, and it does not stay in a conversation it can no longer hold up its half of. The robot never goes silent mid-conversation. |
| Malformed model JSON | Treated as a timeout. Logged with the raw text truncated — never the image. |
| `Transcriber` fails or returns empty | Not a turn. Keep listening. Does not spend the turn budget. |
| `/agent/process_audio_output` silent for `silence_timeout_s` | Close the session politely. |
| Still in `normal` mode (built-in assistant answering) | Log an error, refuse to enter dialogue (§3.2). |
| `PlayTts` fails | Phase 1's tier chain: the pre-recorded audio file. |
| `PlayEmoji` fails, or status reports failure | Log once per session, carry on. The face is decoration; losing it must not end a conversation. |
| Head joint state stale or in error | No head motion at all; the session continues. |
| Stereo topic unavailable | Sweep frames, then the profile alone (§8.2). |
| Subject lost for `subject_lost_s` | Close the session. |
| Not in `STAND_DEFAULT`, or a person inside 1.0 m, or a stale distance reading | **No gesture — but the robot still speaks.** Phase 1's rule, unchanged. |

## 13. Latency

Expected VAD `END` to first sound: **4-8 s** — transcription, the cloud call and
`PlayTts` in series. Two mitigations this phase, both cheap:

- The thinking emoji at t≈0.05 s (§9.3).
- The 200-character reply cap (§7.2) — a long answer makes the *next* gap worse,
  not just this one.

The head sweep covers the equivalent gap at session start. Genuinely fixing this
needs streaming, which is the deferred approach.

## 14. Safety

Phase 1's invariants, carried forward without exception:

- The node **never** changes the robot's motion mode.
- The node **never** issues a locomotion command. Head yaw is not locomotion.
- No gesture outside `STAND_DEFAULT`, within 1.0 m of a person, or on a stale
  distance reading — **and the robot still speaks in every refusal case**.
- MC input source `x2_greeter`, priority 30, inside the documented SDK band 20-39.
  The remote controller stays at 80 and keeps its override.
- Never build or run on **PC1 (10.0.1.40)** — prohibited by the SDK docs.
- Nothing is ever written under `$HOME/aimdk*` — reserved by the system.
- A human stays within reach of the stop control for any run that can produce
  motion.

Phase 2 adds one: **head joint commands go to a HAL topic, not an MC topic, so
they may bypass MC arbitration entirely** — meaning the remote controller may not
be able to override them. This is an open question (§18, item 3) and, until it is
answered, `head.enabled` ships **`false`**.

## 15. Privacy

Phase 1's line, extended to audio.

- **No camera image is ever written to disk, anywhere.** Not a cache, not a debug
  artefact, not a log. Image bytes are never logged at any level.
- **No audio is ever written to disk by this node.** Same rule, same reasons.
- Neither is ever sent anywhere except the single cloud call that composes the
  reply, and no transcript is retained past the session that produced it.
- `test/test_privacy.py`'s tripwire is extended to cover audio buffers, and the
  tripwire remains itself tested — a tripwire nobody has seen fire is a comment.

Noted, not controlled: the vendor's own agent writes VAD audio under `stream_1/`.
That is the vendor's software on the vendor's disk. We neither touch it nor rely
on it, and the deployment guide says so plainly, so nobody believes our rule
covers it.

## 16. Configuration

`config/conversation.yaml`, merged over Phase 1's `greeter.yaml`:

```yaml
/**:
  ros__parameters:
    interaction:
      venue_profile: mall_atrium        # -> config/venues/mall_atrium.yaml

    audio:
      vad_topic: /agent/process_audio_output
      queue_depth: 100                  # the BEGIN burst overruns a shallow queue
      sample_rate: 16000                # vendor-fixed: 16 kHz / 16 bit / mono / S16LE
      max_utterance_s: 20.0
      self_guard_s: 0.5                 # keep discarding after we stop speaking

    transcribe:
      engine: faster_whisper            # faster_whisper | scripted
      model: small
      device: auto
      model_dir: ''

    conversation:
      silence_timeout_s: 15.0
      max_turns: 8
      session_max_s: 180.0
      subject_lost_s: 5.0
      reply_max_chars: 200
      cooldown_s: 30.0

    addressing:
      individual_max_m: 2.0

    language:
      default: en
      allowed: [en, zh]
      switch_confidence: 0.7

    head:
      enabled: false                    # until §18 items 1-3 are answered
      command_topic: /aima/hal/joint/head/command
      state_topic: /aima/hal/joint/head/state
      max_yaw_rad: 0.262                # 15 deg; the vendor limit is 20 deg
      sweep_on_session_start: true
      sweep_leg_s: 1.0
      command_rate_hz: 20.0

    env_camera:
      topic: /aima/hal/sensor/stereo_head_front_left/rgb_image/compressed
      enabled: true

    face:
      enabled: true
      status_topic: /face_ui_proxy/status
      thinking_delay_s: 0.05
      enabled_emoji: []                 # every id unverified; see §9.3
```

## 17. Extension points

Named so the next phase knows where to cut. **Not built now.**

- **`Synthesizer`** — cloud speech synthesis over `/aima/hal/audio/playback`,
  which accepts arbitrary 16 kHz S16LE PCM once `RequestAudioFocus` succeeds. This
  is the Malay path. It is written down here and nowhere else: an abstraction with
  a single implementation and no second caller is a liability, not a design.
- **`AudioSource`** — already a port, so the raw-audio phase swaps the adapter and
  changes nothing above it.
- **`Transcriber`** — the same shape if a different engine ever wins.

For the record, the audio-focus protocol the `Synthesizer` phase will need, since
establishing it took real work: `RequestAudioFocus` with `FocusRequester.pkg_name`
and priority 6, subscribe `/aima/hal/audio/focus_response` and stop on loss, and
the vendor's response field is misspelled **`reponse`** in `PlayAudioFile`,
`RequestAudioFocus` and `AbandonAudioFocus`. Cross-host services are unreliable;
the vendor's own examples retry 8 x 0.25 s.

## 18. Open questions — resolve on first hardware access

| # | Question | Until answered |
|---|---|---|
| 1 | Does `/aima/hal/joint/head/command` exist on this unit? The docs say "requires supported head hardware". | `head.enabled: false` |
| 2 | **Is +yaw left or right?** Measure it; do not assume. Phase 1 shipped against a camera that turned out to be mounted upside down. | `head.enabled: false` |
| 3 | Does head joint control obey MC input-source arbitration — can the operator override it with the remote? | `head.enabled: false` (§14) |
| 4 | Does `HEAD_FOLLOW=false` take effect live, or does it need the agent restart that `RUN_MODE` needs? | assume a restart; document it as a deployment step |
| 5 | What `stiffness`/`damping` does the head controller expect, and what happens at 0? | send the values a vendor example uses, if one exists |
| 6 | Which `emotion_id` values are real? Confirm each before enabling it. | `enabled_emoji: []` |
| 7 | Is the front stereo camera mounted the same way up as the head RGB-D? Phase 1 needed `rotate_180` for the latter. | check before trusting the base frame |
| 8 | Measured VAD `END` -> first sound, on the real robot. | §13's 4-8 s is an estimate |
| 9 | What is the wake word, and what language is it in? Vendor-fixed and undocumented; awkward for an English-default deployment. | note it in the deployment guide |
| 10 | **Phase 1 carry-over:** bench-check the default-enabled gestures. `heart` (1007/3) is the correction in §9.2 and goes first; `wave_chest` (3010 vs 1011), `raise_both` (1001/3 vs 1010/3) and `clap` (3015 vs 3017) also differ between the enum and the doc table. Only `wave` has ever been seen to work. | ~10 minutes of the next window |

## 19. Testing

Everything offline, against `sim/fake_robot.py`, as Phase 1 is. The suite must run
with no robot, no network, no `rclpy`, and no Whisper model.

- **`core/conversation.py`** — every state transition and every limit in §7.2,
  driven by an injected clock. This includes the paths that are hard to reach on
  hardware: silence timeout, turn budget, subject lost, session cap.
- **`core/venue.py`** — profile validation; a fact outside `facts` is not
  assertable; an empty `facts` list asserts nothing; a malformed profile refuses
  to start.
- **`core/gaze.py`** — pure arithmetic, including that **no input can produce a
  yaw outside ±0.262 rad**, the property that protects the hardware.
- **`core/gestures.py`** — the widened source pin (§9.2), and that `heart`
  resolves to area 3 under every `hand_preference`.
- **`ros/audio_source.py`** — segment assembly, the `BEGIN` burst, a missing
  `END`, a `stream_id` change, and self-hearing suppression during and after
  speech.
- **The turn contract** — every field of §11 rejected in isolation; malformed
  JSON; a numeric ID where a name belongs.
- **`test_privacy.py`** — the tripwire, extended to audio, and still self-tested.
- **`test_layering.py`** — no `rclpy` in `core/` or `cognition/`.
- **`test_shipped_config.py`** — the shipped config enables only verified emoji,
  and `head.enabled` is false while §18 items 1-3 are open.

**Mutation testing** on `core/conversation.py` and `core/gaze.py`: flip a
comparison, break a clamp, confirm a test fails. Phase 1's lesson was that tests
and code written from the same wrong source agree with each other happily. A test
that survives its own mutation was decoration.
