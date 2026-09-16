"""The deployment, as a sequence of named steps that report on themselves.

install.sh does this in bash for an engineer who will read the scrollback. The
customers this is for will read one line and a green tick, so every step here
carries a name they can act on, and the failures carry a sentence saying what
to do rather than an exit code.

The whole thing is driven through a callback, so the same code runs behind the
Qt window and in the tests, with no Qt imported anywhere in this package.

The last step is the one that matters. Everything up to it can succeed on a
robot that then greets nobody -- that is the failure this project produces over
and over: the node starts, logs nothing unusual, and behaves wrongly. So the
deployment is not finished when systemd says the service is active. It is
finished when the robot's own startup line comes back saying which camera and
which backend are actually in force, and those match what was asked for.
"""
from __future__ import annotations

import posixpath
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional, Sequence

from . import phrases as phrases_mod
from .i18n import t
from .robot import ROOT, SHARE, Identity, Robot, RobotError

LOG = f'{ROOT}/greeter.log'
REPO = f'{ROOT}/repo'
SITE = f'{ROOT}/site.yaml'
MODELS = f'{ROOT}/models'
UNIT = 'x2-greeter.service'

# What the robot has to say for itself before a deployment counts as done.
WANTED = {'camera': 'stereo', 'backend': 'canned', 'speech_tier': 'tts'}

STARTUP = re.compile(r'x2_greeter up: (.+)')

# How long to wait for that line. The unit sleeps 45 s before launching, to let
# the robot's own camera stack come up, so this is mostly that sleep.
PATIENCE_S = 120.0
POLL_S = 5.0


class Status(str, Enum):
    RUNNING = 'running'
    OK = 'ok'
    FAILED = 'failed'
    INFO = 'info'


@dataclass
class Event:
    step: str
    status: Status
    message: str = ''


Listener = Callable[[Event], None]


@dataclass
class Plan:
    """What the customer chose, before anything is touched."""

    package_dir: str                      # the x2_greeter package on this laptop
    phrases: Sequence[str]
    weights_dir: str
    venue: str = ''
    install_service: bool = True
    start_after: bool = True


@dataclass
class Outcome:
    ok: bool
    identity: Optional[Identity] = None
    startup_line: str = ''
    problems: List[str] = field(default_factory=list)


class Deployment:
    """Runs a Plan against one Robot, reporting every step as it goes."""

    def __init__(self, robot: Robot, plan: Plan, listen: Optional[Listener] = None):
        self.robot, self.plan = robot, plan
        self._listen = listen or (lambda event: None)

    # -- reporting ------------------------------------------------------------

    def _emit(self, step: str, status: Status, message: str = '') -> None:
        self._listen(Event(step, status, message))

    def _step(self, name: str, work: Callable[[], str]) -> bool:
        self._emit(name, Status.RUNNING)
        try:
            self._emit(name, Status.OK, work())
            return True
        except RobotError as exc:
            self._emit(name, Status.FAILED, str(exc))
        except Exception as exc:                        # noqa: BLE001
            self._emit(name, Status.FAILED, f'{type(exc).__name__}: {exc}')
        return False

    # -- the steps ------------------------------------------------------------

    def run(self) -> Outcome:
        outcome = Outcome(ok=False)

        problems = phrases_mod.check(self.plan.phrases)
        blocking = [str(p) for p in problems if not p.fixable]
        if blocking:
            self._emit(t('step.check'), Status.FAILED, blocking[0])
            return Outcome(ok=False, problems=blocking)

        def connect() -> str:
            self.robot.connect()
            outcome.identity = self.robot.identify()
            return str(outcome.identity)

        steps = [
            (t('step.connect'), connect),
            (t('step.upload'), self._upload_package),
            (t('step.weights'), self._upload_weights),
            (t('step.build'), self._build),
            (t('step.configure'), self._configure),
        ]
        if self.plan.install_service:
            steps.append((t('step.service'), self._install_service))
        if self.plan.start_after:
            steps.append((t('step.start'), self._start))
            steps.append((t('step.verify'), self._verify))

        for name, work in steps:
            if not self._step(name, work):
                return outcome

        outcome.ok = True
        outcome.startup_line = self._startup_line or ''
        return outcome

    # -- implementations ------------------------------------------------------

    def _upload_package(self) -> str:
        sent = self.robot.mirror(self.plan.package_dir, REPO)
        return t('step.files', count=sent)

    def _upload_weights(self) -> str:
        import os

        names = ['MobileNetSSD_deploy.prototxt', 'MobileNetSSD_deploy.caffemodel']
        missing = [n for n in names
                   if not os.path.isfile(os.path.join(self.plan.weights_dir, n))]
        if missing:
            raise RobotError(t('err.no_weights'))
        for name in names:
            self.robot.put(os.path.join(self.plan.weights_dir, name),
                           posixpath.join(MODELS, name))
        return t('step.files', count=len(names))

    def _build(self) -> str:
        # `source` then `;` not `&&`: an environment script's exit status
        # describes nothing useful, and chaining on it silently skips the build.
        result = self.robot.run(
            f'set -e\n'
            f'mkdir -p {ROOT}/ws/src\n'
            f'ln -sfn {REPO}/x2_greeter_ws/src/x2_greeter {ROOT}/ws/src/x2_greeter\n'
            f'source {ROOT}/env.sh >/dev/null 2>&1 || true\n'
            f'cd {ROOT}/ws && colcon build --packages-select x2_greeter 2>&1 | tail -3',
            timeout=600)
        if not result.ok or 'Summary: 1 package finished' not in result.out:
            raise RobotError(t('err.build',
                                detail=result.out.strip() or result.err.strip()))
        return t('step.build_done')

    def _configure(self) -> str:
        """Seed site.yaml if absent, then write the phrases and the settings.

        site.yaml is seeded once and never overwritten, so anything proven on
        this particular robot -- camera.rotate_180 above all -- survives being
        deployed to again.
        """
        shipped = f'{SHARE}/greeter.yaml'
        self.robot.run(
            f'[ -f {SITE} ] || {{ cp {shipped} {SITE} && '
            f'sed -i "s|^      model_dir: \'\'|      model_dir: {MODELS}|" {SITE}; }}')
        if not self.robot.exists(SITE):
            raise RobotError(t('err.no_site'))

        target = f'{ROOT}/phrases.yaml'
        self.robot.put_text(
            _phrases_yaml(self.plan.phrases, self.plan.venue), target)

        applied = _apply_site(self.robot.read_text(SITE), target)
        self.robot.put_text(applied, SITE)
        return t('step.phrase_count',
                 count=len(phrases_mod.apply_fixes(self.plan.phrases)))

    def _install_service(self) -> str:
        unit = self.robot.read_text(f'{REPO}/tools/deploy/{UNIT}')
        # touch first: the unit's StandardOutput=append: creates the log as
        # root when it is missing, and the node runs as `run` and then cannot
        # write to its own log for the rest of the deployment's life.
        self.robot.run(f'touch {LOG}')
        self.robot.put_text(unit, '/tmp/x2-greeter.service')
        for command in (f'cp /tmp/x2-greeter.service /etc/systemd/system/{UNIT}',
                        'systemctl daemon-reload',
                        f'systemctl enable {UNIT}'):
            result = self.robot.sudo(command)
            if not result.ok:
                raise RobotError(t('err.service', detail=result.err.strip()))
        return t('step.autostart_on')

    def _start(self) -> str:
        # restart, not start: `start` on a service that is already running does
        # nothing, and a redeploy would leave the old greeting list in place
        # while reporting success.
        result = self.robot.sudo(f'systemctl restart {UNIT}', timeout=60)
        if not result.ok:
            raise RobotError(t('err.start', detail=result.err.strip()))
        return t('step.started')

    _startup_line: Optional[str] = None

    def _verify(self) -> str:
        """Wait for the robot to say what it is actually running.

        The service sleeps 45 s first, letting the robot's own camera stack
        come up, so this is a wait rather than a check. What it is waiting for
        is not "did it start" -- systemd already answered that -- but the one
        line that names the camera and the backend in force.
        """
        deadline = time.monotonic() + PATIENCE_S
        while time.monotonic() < deadline:
            found = STARTUP.search(self.robot.run(f'tail -200 {LOG}').out)
            if found:
                line = found.group(1).strip()
                self._startup_line = line
                wrong = [t('err.wrong_field', key=key, want=want,
                           got=_field(line, key) or '?')
                         for key, want in WANTED.items()
                         if _field(line, key) != want]
                if wrong:
                    raise RobotError(
                        t('err.wrong_config', detail='; '.join(wrong)))
                return line
            self._emit(t('step.verify'), Status.INFO, t('step.waiting_camera'))
            time.sleep(POLL_S)
        raise RobotError(t('err.never_ready'))


def _field(line: str, key: str) -> str:
    found = re.search(rf'\b{re.escape(key)}=(\S+)', line)
    return found.group(1) if found else ''


def _phrases_yaml(items: Sequence[str], venue: str) -> str:
    import io

    buffer = io.StringIO()

    class _Sink:
        def write(self, text):
            buffer.write(text)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    cleaned = phrases_mod.apply_fixes(items)
    header = ['# Greeting phrases, written by the X2 deployer.']
    if venue:
        header.append(f'# Venue: {venue}')
    header += ['#', '# One is picked at random per greeting.', 'phrases:']
    body = ['  - "{}"'.format(p.replace('\\', '\\\\').replace('"', '\\"'))
            for p in cleaned]
    return '\n'.join(header + body) + '\n'


def _apply_site(text: str, phrases_path: str) -> str:
    """Set the four values this deployment needs, leaving the file otherwise
    untouched -- comments included, since site.yaml is where a robot's own
    bring-up history is written down."""
    settings = [('backend', 'provider', 'canned'),
                ('speech', 'tier', 'tts'),
                ('speech', 'phrases_file', phrases_path),
                ('camera', 'source', 'stereo')]
    lines = text.splitlines(keepends=True)
    for block, key, value in settings:
        lines = _set_in_block(lines, block, key, value)
    return ''.join(lines)


def _set_in_block(lines: List[str], block: str, key: str, value: str) -> List[str]:
    indent = lambda s: len(s) - len(s.lstrip(' '))      # noqa: E731
    for i, line in enumerate(lines):
        if not re.match(rf'^\s*{re.escape(block)}:\s*(#.*)?$', line):
            continue
        base, child, j = indent(line), None, i + 1
        while j < len(lines):
            current = lines[j]
            if current.strip() and not current.lstrip().startswith('#'):
                if indent(current) <= base:
                    break
                child = indent(current) if child is None else child
                found = re.match(rf'^(\s*){re.escape(key)}:(\s*)(.*)$', current)
                if found and indent(current) == child:
                    lines[j] = f'{found.group(1)}{key}: {value}\n'
                    return lines
            j += 1
        pad = ' ' * (child if child is not None else base + 2)
        lines.insert(i + 1, f'{pad}{key}: {value}\n')
        return lines
    raise RobotError(t('err.no_block', block=block))
