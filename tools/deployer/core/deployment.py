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
UNIT_FILE = f'/etc/systemd/system/{UNIT}'
# What the robot itself runs, copied out of the uploaded repository: the unit
# runs service.sh, "Deploy once" runs start.sh, and uninstalling runs stop.sh.
SCRIPTS = ('start.sh', 'stop.sh', 'service.sh', 'uninstall.sh')

# Process patterns for `pgrep -f` / `pkill -f` sent over SSH. Every command
# there runs as `bash -c "<the whole command>"`, so a pattern written out
# literally matches the bash running it: the uninstall check used to find its
# own shell and report it as a greeter left behind, on every robot, every
# time. The brackets match the same process names without the command
# containing them. (bin/stop.sh does not need this: it runs as a file, and its
# command line is just its path.)
GREETER_PROCESS = 'x2_greeter/li[b]'
LAUNCH_PROCESS = 'greeter[.]launch'

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
        self._install_scripts()
        return t('step.files', count=sent)

    def _install_scripts(self) -> None:
        """env.sh and bin/, which the rest of the deployment depends on.

        tools/deploy/install.sh has always copied these. This deployment never
        did, and it went unnoticed because every robot it had been pointed at
        had been through install.sh first and still had them: the build
        sourced an env.sh that happened to be there, and the unit ran a
        service.sh that happened to be there. On a robot fresh from the
        factory, both would have failed.
        """
        sources = ' '.join(f'{REPO}/tools/deploy/{name}' for name in SCRIPTS)
        result = self.robot.run(
            f'set -e\n'
            f'mkdir -p {ROOT}/bin {MODELS}\n'
            f'cp {REPO}/tools/deploy/env.sh {ROOT}/env.sh\n'
            f'cp {sources} {ROOT}/bin/\n'
            f'chmod +x {ROOT}/bin/*.sh')
        if not result.ok:
            raise RobotError(t('err.scripts',
                                detail=result.err.strip() or result.out.strip()))

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
        """Start the greeter the way the customer chose to deploy it.

        There are two ways a greeter can be running on a robot -- under
        systemd, or by hand from bin/start.sh -- and whichever one is not
        chosen has to be put out of the way first, or the robot ends up with
        two greeters talking over each other.
        """
        if self.plan.install_service:
            return self._start_under_systemd()
        return self._start_by_hand()

    def _start_under_systemd(self) -> str:
        # A greeter left running by an earlier "Deploy once" is not systemd's,
        # so restarting the service would start a second one beside it.
        stopped = self.robot.run(f'{ROOT}/bin/stop.sh', timeout=60)
        if not stopped.ok:
            raise RobotError(t('err.start', detail=_tail(stopped)))
        # restart, not start: `start` on a service that is already running does
        # nothing, and a redeploy would leave the old greeting list in place
        # while reporting success.
        result = self.robot.sudo(f'systemctl restart {UNIT}', timeout=60)
        if not result.ok:
            raise RobotError(t('err.start', detail=result.err.strip()))
        return t('step.started')

    def _start_by_hand(self) -> str:
        """"Deploy once": running now, and not after the next power cycle.

        This used to ask systemd to restart a unit that "Deploy once" had
        deliberately not installed, which on a robot that had never been
        deployed permanently failed with "Unit x2-greeter.service not found".

        A unit left enabled by an earlier permanent deploy is switched off
        first. Left alone it would break the promise this option makes, and
        if it was running, systemd would restart it thirty seconds after
        start.sh killed it.
        """
        was_permanent = self.robot.exists(UNIT_FILE)
        if was_permanent:
            result = self.robot.sudo(f'systemctl disable --now {UNIT}', timeout=60)
            if not result.ok:
                raise RobotError(t('err.start', detail=result.err.strip()))

        # stdin from /dev/null, so the backgrounded greeter does not inherit
        # the SSH channel and hold this command open until it exits.
        result = self.robot.run(f'bash {ROOT}/bin/start.sh < /dev/null', timeout=120)
        if not result.ok:
            raise RobotError(t('err.start', detail=_tail(result)))
        return t('step.started_not_on_boot' if was_permanent else 'step.started')

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


def _tail(result, lines: int = 6) -> str:
    """The end of what a script said, which is where it says why it failed."""
    text = (result.err.strip() + '\n' + result.out.strip()).strip()
    return '\n'.join(text.splitlines()[-lines:])


class Uninstall:
    """Take the greeter off a robot, leaving the robot as it was.

    Everything this deployment ever writes lives under /home/run/x2_greeter
    and in one systemd unit, which is what makes removal a short list rather
    than a hunt. Nothing under /agibot is touched -- the vendor's own software
    was never modified, so there is nothing there to put back.

    The vendor agent's run mode is not restored either, because Phase 1 never
    changes it. A robot with the greeter removed answers people exactly as it
    did before the greeter arrived.

    Each step tolerates the thing it removes being absent already: a half
    finished deployment is the most likely reason somebody is uninstalling.
    """

    def __init__(self, robot: Robot, listen: Optional[Listener] = None):
        self.robot = robot
        self._listen = listen or (lambda event: None)

    def _emit(self, step: str, status: Status, message: str = '') -> None:
        self._listen(Event(step, status, message))

    def run(self) -> Outcome:
        outcome = Outcome(ok=False)

        steps = [
            (t('step.connect'), self._connect),
            (t('uninstall.stop'), self._stop_service),
            (t('uninstall.remove_unit'), self._remove_unit),
            (t('uninstall.remove_files'), self._remove_files),
            (t('uninstall.verify'), self._verify),
        ]
        for name, work in steps:
            self._emit(name, Status.RUNNING)
            try:
                self._emit(name, Status.OK, work())
            except RobotError as exc:
                self._emit(name, Status.FAILED, str(exc))
                return outcome
            except Exception as exc:                    # noqa: BLE001
                self._emit(name, Status.FAILED, f'{type(exc).__name__}: {exc}')
                return outcome

        outcome.ok = True
        return outcome

    def _connect(self) -> str:
        self.robot.connect()
        return str(self.robot.identify())

    def _stop_service(self) -> str:
        """Disable before stopping, so a unit set to restart does not come
        back between the two commands.

        Then any greeter started by hand. "Deploy once" runs it from
        bin/start.sh, outside systemd, so disabling the unit leaves it
        running -- from a directory about to be deleted. Killed directly
        rather than through bin/stop.sh, because a half finished deployment
        is the likeliest reason to be here and may never have installed it.
        """
        self.robot.sudo(f'systemctl disable --now {UNIT}')
        still = self.robot.run(f'systemctl is-active {UNIT}').out.strip()
        if still == 'active':
            raise RobotError(t('uninstall.still_running'))

        survivor = self.robot.run(
            f'pkill -f "{LAUNCH_PROCESS}"; pkill -f "{GREETER_PROCESS}"; sleep 2; '
            f'pkill -9 -f "{GREETER_PROCESS}"; sleep 1; '
            f'pgrep -f "{GREETER_PROCESS}" | head -1', timeout=30).out.strip()
        if survivor:
            raise RobotError(t('uninstall.still_running'))
        return t('uninstall.stopped')

    def _remove_unit(self) -> str:
        self.robot.sudo(f'rm -f /etc/systemd/system/{UNIT}')
        self.robot.sudo('systemctl daemon-reload')
        return t('uninstall.unit_removed')

    def _remove_files(self) -> str:
        # Spelled out rather than interpolated: this is the one command in the
        # program that deletes a directory tree on a robot, and it should be
        # readable as exactly what it is.
        result = self.robot.run('rm -rf /home/run/x2_greeter')
        if not result.ok:
            raise RobotError(t('uninstall.files_failed', detail=result.err.strip()))
        return t('uninstall.files_removed')

    def _verify(self) -> str:
        """Ask the robot what is left, rather than trusting the commands.

        Every other failure in this project has looked like success at the
        point the work was done.
        """
        left = self.robot.run(
            f'ls -d {ROOT} 2>/dev/null; ls /etc/systemd/system/{UNIT} 2>/dev/null; '
            f'pgrep -f "{GREETER_PROCESS}" | head -1').out.strip()
        if left:
            raise RobotError(t('uninstall.leftovers', detail=left.replace('\n', ' ')))
        return t('uninstall.clean')


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
