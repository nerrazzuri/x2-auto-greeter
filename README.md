# X2 auto-greeter

A ROS 2 Humble node for the AgiBot X2 humanoid: when the head camera sees a
person, the robot greets them out loud and gestures.

Detection runs locally and gates a single cloud call, which confirms the person,
composes the line, and picks the gesture. Everything degrades — cloud unreachable
falls back to canned phrases, TTS failure falls back to pre-recorded audio, and
an unsafe pose or distance drops the gesture while still speaking.

## Start here

| If you are | Read |
|---|---|
| an agent bringing this up on hardware for the first time | [`docs/AGENT_BRINGUP_GUIDE.md`](docs/AGENT_BRINGUP_GUIDE.md) |
| running the deployment steps | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) |
| looking for why it is built this way | [`docs/superpowers/specs/2026-09-02-x2-auto-greeter-design.md`](docs/superpowers/specs/2026-09-02-x2-auto-greeter-design.md) |

## Safety and privacy constraints

- **This has run against a real robot once** (2026-09-03: an X2 greeted a person
  and waved). All 283 tests are still offline, against
  `x2_greeter/sim/fake_robot.py`. That session found four defects, each of which
  presented as a healthy system — see `docs/AGENT_BRINGUP_GUIDE.md`.
- Never build or run on **PC1 (10.0.1.40)** — prohibited by the SDK docs. The
  node runs on **PC2 (10.0.1.41)**; audio assets live on **PC3 (10.0.1.42)**.
- The node never changes the robot's motion mode and never issues locomotion
  commands. It refuses to gesture outside `STAND_DEFAULT`, within 1.0 m of a
  person, or on a stale distance reading — and still speaks in every refusal case.
- **No camera image is ever written to disk**, anywhere. Enforced by a tripwire in
  `test/test_privacy.py` that is itself tested.

## Tests

```bash
python3 -m pytest -q            # 182 passed, 101 deselected — no ROS needed
```

The 101 deselected tests need `rclpy` and the vendor `aimdk_msgs`. They run in the
Docker harness, which needs the AimDK SDK archive unpacked into `sdk/` (gitignored,
not redistributed here):

```bash
docker build -f docker/Dockerfile.test -t x2-greeter-test .
docker run --rm -v "$PWD:/repo" -v x2-greeter-ws:/ws x2-greeter-test bash /repo/docker/run_tests.sh
```

## Layout

```
x2_greeter_ws/src/x2_greeter/
  x2_greeter/core/        detection, presence state machine, gesture catalogue — no rclpy
  x2_greeter/cognition/   greeting backends: Claude, canned phrases, the fallback policy
  x2_greeter/ros/         the node and its service adapters — the only ROS-aware layer
  x2_greeter/sim/         fake robot for offline testing
  config/greeter.yaml     every tunable
tools/                    model fetch, greeting-audio generation, audio deployment
docker/                   test harness that builds the vendor messages from source
```

`test/test_layering.py` fails if `rclpy` ever appears in `core/` or `cognition/`.
