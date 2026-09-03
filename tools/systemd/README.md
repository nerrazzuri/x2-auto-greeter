# Running the greeter as a service

Two ways to start the greeter on a robot. Neither modifies anything under
`/agibot` — the robot's own software is untouched.

## By hand

```bash
scp start_greeter.sh stop_greeter.sh run@<PC2>:/home/run/
ssh run@<PC2> chmod +x /home/run/start_greeter.sh /home/run/stop_greeter.sh
ssh run@<PC2> /home/run/start_greeter.sh
```

`start_greeter.sh` stops any previous instance first, so it is safe to re-run.
It prints the node's own startup line on success, and the tail of the log with
a non-zero exit on failure — it does not report success it has not seen.

## At boot

```bash
scp greeter_service.sh run@<PC2>:/home/run/
ssh run@<PC2> chmod +x /home/run/greeter_service.sh
scp x2-greeter.service run@<PC2>:/tmp/
ssh run@<PC2> 'sudo cp /tmp/x2-greeter.service /etc/systemd/system/ \
    && sudo systemctl daemon-reload && sudo systemctl enable --now x2-greeter'
```

Then `systemctl status x2-greeter`, and `journalctl -u x2-greeter` alongside
`/home/run/greeter.log`.

To stop it starting at boot again: `sudo systemctl disable --now x2-greeter`.

## What starting at boot does and does not do

The greeter only speaks and issues **preset** motions. It never changes the
robot's motion mode, so a service that starts at boot cannot make the robot
stand up or walk. Until somebody brings the robot to force-control stand — with
the remote, deliberately — it greets people and refuses to gesture, and logs
the refusal each time.

It will, however, start greeting whoever walks past as soon as the camera comes
up. That is the point, but it is worth saying out loud before enabling it in a
space where the robot is unattended.

## Prerequisites the unit does not check

- `/home/run/x2_greeter_env.sh` exists and sources the robot's `basic_env.sh`.
  Without the FastDDS profile that sets, the camera topics list and no frame
  ever arrives.
- `/home/run/greeter_site.yaml` holds this robot's settings — notably
  `camera.rotate_180` and `backend.provider`.
- The workspace at `/home/run/x2_greeter_ws` is built.
