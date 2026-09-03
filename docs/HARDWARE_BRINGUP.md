# Phase 2 hardware bring-up

Everything below ships disabled: `face.enabled`, `head.enabled`,
`head.sweep_on_start`, `head.gaze_follow`, and `base_frame.use_env_camera`
are all `false` in the shipped `config/conversation.yaml`. Turn one flag on
at a time, in the order below, with a human within reach of the stop
control for every step that can produce motion. If a step misbehaves, turn
that one flag back off before moving on — the point of the order is that
you always know which change caused what.

The order is chosen by motion risk, not by feature importance:

1. `face.enabled` and `base_frame.use_env_camera` produce **no physical
   motion at all** — a wrong screen or a wrong image is the worst case, and
   both are safe to debug with nobody standing clear.
2. `head.enabled` is turned on next, deliberately with `sweep_on_start` and
   `gaze_follow` still `false`. That isolates the first physical motion to
   exactly one thing: the head commanding itself to centre at startup. If
   that one bounded motion is wrong, nothing later in the list has been
   touched yet.
3. `sweep_on_start` adds one more bounded, one-shot motion — a sweep that
   starts and ends in the same session.
4. `gaze_follow` is last because it is the only flag that produces
   continuous, unattended motion for the length of a whole session. It is
   turned on only once everything upstream of it (the head itself, its
   clamp, its centring) is already proven.

**Every topic and service name below is copied verbatim from
`config/conversation.yaml`**, which has been checked against the SDK. Do not
substitute a name you remember from Phase 1 or from the design spec — read
it from the config, or from the adapter source in `x2_greeter/ros/` if you
need one the config does not name.

## 0. Before anything moves

- **A human must be within reach of the stop control before any step below
  that can produce motion.** This applies starting at step 4 (head) and
  for every step after it; it is called out again there, but the rule
  starts here.
- Robot in `STAND_DEFAULT` (200). The node never changes the motion mode
  and refuses to gesture in any other one.
- Remote controller powered on and in the operator's hands. It sits at
  priority 80 (per `config/conversation.yaml`'s comment on `mc_input`) and
  overrides the node's priority 30 at any time.
- `ANTHROPIC_API_KEY` is set in the launching shell's environment — never
  in `config/conversation.yaml`, never committed. Without it the node
  starts but the dialogue backend fails every turn.
- `ros2 topic hz /agent/process_audio_output` (the exact value of
  `hearing.topic` in `config/conversation.yaml`) shows traffic when
  somebody speaks. If it does not, nothing downstream will work and no
  amount of config will fix it — the vendor's `only_voice` pipeline is not
  segmenting audio, and that is a deployment problem (`docs/DEPLOYMENT.md`
  Phase 2, section 3 and 5), not something in this package.

## 1. Conversation with no motion (default config)

Launch as shipped:

```bash
ros2 launch x2_greeter conversation.launch.py venue:=clothing_store
```

Speak to the robot. You are checking three things:
- it answers, in the language you spoke;
- **no second voice answers** — if one does, `only_voice` did not take, and
  the startup log will say so. There is no service to read `only_voice`
  back later, so this spoken check is the only verification (see
  `docs/DEPLOYMENT.md` Phase 2, section 5);
- it stops. Walk away mid-conversation and confirm the session closes
  rather than continuing to talk to an empty floor.

## 2. Gestures

Already proven on hardware in Phase 1 and shipped enabled by default in
`config/conversation.yaml`'s `gestures.enabled` list — this is not a flag
you are turning on, but re-check `heart` specifically before relying on it
in a session: it is motion 1007 with area 3 (the two-handed chest heart,
`core/gestures.py`'s `AREA_BOTH`), and the Phase 1 refusal was observed at
1007 with area 2 (`hand_preference: right` sending a two-handed motion a
one-handed area). Stand beyond 1.0 m — inside that, the arm's-reach
interlock (`safety.gesture_min_distance_m`) refuses the gesture and the
robot speaks anyway, which is correct behaviour and looks like a bug if you
are not expecting it.

## 3. The face — `face.enabled: true`

No motion at all; safe to debug alone.

Start with the thinking expression alone: speak, and watch for it to appear
roughly a twentieth of a second later (`ros/face.py`'s
`show_thinking()`), well before the reply. If the face does nothing, check
the service path with

```bash
ros2 service list | grep -i emoji
```

against `face.service` (`/aimdk_5Fmsgs/srv/PlayEmoji`) in the config,
before assuming the ids are wrong.

The ids themselves are pinned against `aimdk_msgs/srv/PlayEmoji` by
`test/ros/test_face_catalogue_ids.py`, so a wrong id fails in the
container, not on the robot.

## 4. The wide base frame — `base_frame.use_env_camera: true`

No motion at all; safe to debug alone.

Find the real topic first — do not trust the config's default blindly:

```bash
ros2 topic list | grep -i -E 'stereo|front|fisheye'
ros2 topic hz <candidate>
```

`base_frame.rgb_topic` ships as `/aima/hal/sensor/stereo_head_front_left/rgb_image`.
Confirm that topic is actually live on this robot, put the confirmed value
in `base_frame.rgb_topic`, then flip the flag. The check is qualitative:
ask the robot about something behind you. With the head camera alone it
cannot see it; with the 156-degree frame it can.

## 5. The head — `head.enabled: true`

**This is the first step that produces motion. A human must be within
reach of the stop control from here on, for every remaining step.**

Yaw only, clamped to 0.262 rad in two places
(`x2_greeter/core/gaze.py` and again in `x2_greeter/ros/head.py`, the one
that actually reaches a motor). Before enabling it in a session with a
person present:

- `ros2 topic echo /aima/hal/joint/head/state --once` — confirm feedback on
  `head.state_topic` before commanding anything on `head.command_topic`
  (`/aima/hal/joint/head/command`).
- Enable `head.enabled` with `sweep_on_start` and `gaze_follow` still
  `false`. Confirm the head is commanded to centre at startup and does not
  drift.

**Observability warning: `x2_greeter/ros/head.py` emits no log lines at
all.** Yaw clamping and the disabled short-circuit are both silent — there
is nothing in the node log to watch. Verify head behaviour by watching the
physical head and by echoing `/aima/hal/joint/head/state`, not by reading
logs.

## 6. `head.sweep_on_start: true`

Watch one sweep with nobody in front. It must end centred.

If the head ends a session anywhere but centred, turn `head.enabled` back
off and say so — `sweep()` centres in a `finally` and `destroy()` centres
on shutdown, so an off-centre head means one of those paths is not
running, and that is worth finding before the next session rather than
after.

## 7. `head.gaze_follow: true`

The last flag, and the only one that drives continuous motion for the
length of a whole session. Confirm the head tracks the addressed person in
`AddressingMode.INDIVIDUAL` and drifts gently in `AddressingMode.GROUP`,
and that it still returns to centre when the session closes.

## 8. Venue tuning

`config/venues/*.yaml` (`clothing_store.yaml` and `mall_atrium.yaml` ship
today) is where the robot's knowledge of a place lives: facts it may
state, topics to encourage, topics never to touch, and the line it uses to
hand a question to a human. Editing these needs no rebuild with
`--symlink-install`, only a relaunch.

## Known issue: the docker/CI baseline

The Docker test harness (`docker/run_tests.sh`) has a pre-existing,
accepted baseline of **20 failed, 9 errors** — all in
`test_greeting_end_to_end.py` and `test_interaction_guard.py` — caused by a
Phase 1 defect in importing `TtsStatus`, unrelated to anything in Phase 2.
Every conversation-node test passes. If you need to confirm this on the
real robot rather than in the container:

```bash
ros2 interface list | grep -i tts
```

If a new failure appears anywhere outside those two files, treat it as
real; do not assume it is this known issue.
