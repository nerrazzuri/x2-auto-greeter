"""The deployment engine, against a robot that only exists in this file.

The point of these is the order and the refusals. A deployment that installs
the service before the build has finished, or that reports success on a robot
whose startup line says `backend=claude`, is worse than one that fails: the
customer walks away believing it worked.

No ROS, no paramiko, no robot -- FakeRobot answers the same handful of calls.
"""
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'tools'))

from deployer.core import robot as R                        # noqa: E402
from deployer.core.deployment import (                      # noqa: E402
    Deployment, Plan, Status, _apply_site)

GOOD_LINE = ('x2_greeter up: detector=MobileNetSsdDetector camera=stereo '
             'backend=canned gestures=11 speech_tier=tts')

PHRASES = ['Welcome to the mall!', "Hi! I'm X2."]

SHIPPED_SITE = """\
/**:
  ros__parameters:
    backend:
      # which brain
      provider: claude
    camera:
      source: rgbd
      rotate_180: true
    speech:
      tier: auto
      phrases_file: ''
      wake_invitations:
        - Say Hi Lumi and I will be happy to chat.
"""


class FakeRobot:
    """Answers what the deployment asks, and remembers the order it asked."""

    def __init__(self, *, startup_line=GOOD_LINE, build_ok=True, sudo_ok=True):
        self.calls = []
        self.files = {f'{R.ROOT}/ws/install/x2_greeter/share/x2_greeter/config/'
                      'greeter.yaml': SHIPPED_SITE,
                      f'{R.ROOT}/repo/tools/deploy/x2-greeter.service': '[Unit]\n'}
        self.startup_line = startup_line
        self.build_ok = build_ok
        self.sudo_ok = sudo_ok
        self.mirrored = None

    def connect(self):
        self.calls.append('connect')

    def identify(self):
        self.calls.append('identify')
        return R.Identity(serial='1423225023973', mac='e8:97:44:8f:19:df',
                          hostname='agi')

    def mirror(self, local, remote, on_file=None):
        self.calls.append('mirror')
        self.mirrored = (str(local), remote)
        return 117

    def put(self, local, remote):
        self.calls.append(f'put {remote.rsplit("/", 1)[-1]}')
        self.files[remote] = '<binary>'

    def put_text(self, text, remote):
        self.calls.append(f'write {remote}')
        self.files[remote] = text

    def read_text(self, remote):
        if remote not in self.files:
            raise IOError(remote)
        # site.yaml is seeded from the shipped file by the `cp` in run()
        return self.files[remote]

    def exists(self, remote):
        return remote in self.files

    def run(self, command, timeout=None):
        self.calls.append(f'run {command.strip().splitlines()[0][:40]}')
        if 'colcon build' in command:
            self.calls[-1] = 'run colcon build'
            return R.Result(0 if self.build_ok else 1,
                            'Summary: 1 package finished' if self.build_ok
                            else 'CMake Error', '')
        if command.startswith(f'[ -f {R.ROOT}/site.yaml ]'):
            self.files.setdefault(f'{R.ROOT}/site.yaml', SHIPPED_SITE)
            return R.Result(0, '', '')
        if 'tail -200' in command:
            return R.Result(0, self.startup_line or '', '')
        return R.Result(0, '', '')

    def sudo(self, command, timeout=None):
        self.calls.append(f'sudo {command}')
        return R.Result(0 if self.sudo_ok else 1, '', ''
                        if self.sudo_ok else 'not in the sudoers file')


def deploy(fake, **plan_kwargs):
    events = []
    plan = Plan(package_dir=str(REPO), phrases=PHRASES,
                weights_dir=str(REPO), venue='Somewhere Mall', **plan_kwargs)
    # weights_dir points at the repo root, which has no .caffemodel in it, so
    # tests that get that far pass a real one via monkeypatch instead.
    return Deployment(fake, plan, events.append).run(), events


@pytest.fixture
def weights(tmp_path):
    for name in ('MobileNetSSD_deploy.prototxt', 'MobileNetSSD_deploy.caffemodel'):
        (tmp_path / name).write_text('x')
    return str(tmp_path)


def run_full(fake, weights_dir, **kwargs):
    events = []
    plan = Plan(package_dir=str(REPO / 'tools'), phrases=PHRASES,
                weights_dir=weights_dir, venue='Somewhere Mall', **kwargs)
    outcome = Deployment(fake, plan, events.append).run()
    return outcome, events


# -- the happy path ----------------------------------------------------------

def test_a_clean_deployment_reports_the_robot_it_reached(weights):
    outcome, events = run_full(FakeRobot(), weights)
    assert outcome.ok
    assert outcome.identity.serial == '1423225023973'
    assert outcome.startup_line == GOOD_LINE.split('x2_greeter up: ')[1]
    assert [e.step for e in events if e.status is Status.FAILED] == []


def test_the_service_is_installed_only_after_the_build_succeeds(weights):
    fake = FakeRobot()
    run_full(fake, weights)
    build = [i for i, c in enumerate(fake.calls) if 'colcon build' in c][0]
    enable = [i for i, c in enumerate(fake.calls) if c.startswith('sudo systemctl')]
    assert enable and min(enable) > build


def test_the_log_exists_before_systemd_can_create_it_as_root(weights):
    """The unit appends to greeter.log as root if it is missing, after which
    the node -- running as `run` -- cannot write to its own log. Ordering is
    the whole assertion: touching it afterwards would be too late."""
    fake = FakeRobot()
    run_full(fake, weights)
    touch = [i for i, c in enumerate(fake.calls) if c.startswith('run touch')]
    enable = [i for i, c in enumerate(fake.calls)
              if c == 'sudo systemctl enable x2-greeter.service']
    assert touch, 'greeter.log is never created'
    assert enable, 'the unit is never enabled'
    assert min(touch) < min(enable)


def test_starting_uses_restart_so_a_redeploy_is_not_silently_ignored(weights):
    """`systemctl start` on a running service does nothing, so a redeploy
    would install the new greetings and leave the old ones being spoken."""
    fake = FakeRobot()
    run_full(fake, weights)
    assert 'sudo systemctl restart x2-greeter.service' in fake.calls
    assert not any(c.startswith('sudo systemctl start') for c in fake.calls)


# -- the refusals ------------------------------------------------------------

def test_a_greeting_the_robot_cannot_say_stops_it_before_the_robot_is_touched():
    fake = FakeRobot()
    events = []
    plan = Plan(package_dir=str(REPO), phrases=['Visit us at **UG**!'],
                weights_dir=str(REPO))
    outcome = Deployment(fake, plan, events.append).run()
    assert not outcome.ok
    assert fake.calls == [], '没有连机器人就应该先拦下来'
    assert '符号' in outcome.problems[0]


def test_missing_weights_are_explained_not_raised(weights, tmp_path):
    outcome, events = run_full(FakeRobot(), str(tmp_path / 'nothing-here'))
    assert not outcome.ok
    failure = [e for e in events if e.status is Status.FAILED][0]
    assert '识别模型' in failure.message and '下载' in failure.message


def test_a_failed_build_stops_the_deployment(weights):
    outcome, events = run_full(FakeRobot(build_ok=False), weights)
    assert not outcome.ok
    failed = [e for e in events if e.status is Status.FAILED][0]
    assert failed.step == '编译'


def test_a_robot_that_comes_up_with_the_wrong_backend_is_a_failure(weights):
    """The failure this project keeps producing: everything installs, systemd
    reports active, and the robot is running something else entirely."""
    wrong = GOOD_LINE.replace('backend=canned', 'backend=claude')
    outcome, events = run_full(FakeRobot(startup_line=wrong), weights)
    assert not outcome.ok
    failed = [e for e in events if e.status is Status.FAILED][0]
    assert 'backend' in failed.message and 'canned' in failed.message


def test_a_robot_that_never_reports_ready_is_a_failure(weights, monkeypatch):
    """systemd reporting `active` is not the robot being ready. Without the
    startup line there is nothing saying which camera or backend is in force,
    so the deployment must not claim success."""
    monkeypatch.setattr('deployer.core.deployment.PATIENCE_S', 0.05)
    monkeypatch.setattr('deployer.core.deployment.POLL_S', 0.0)
    outcome, events = run_full(FakeRobot(startup_line=''), weights)
    assert not outcome.ok
    failed = [e for e in events if e.status is Status.FAILED][0]
    assert failed.step == '确认机器人已就绪'
    assert '相机' in failed.message


# -- what lands in site.yaml -------------------------------------------------

def test_the_four_settings_are_written_and_the_comments_survive():
    applied = _apply_site(SHIPPED_SITE, '/home/run/x2_greeter/phrases.yaml')
    params = yaml.safe_load(applied)['/**']['ros__parameters']

    assert params['backend']['provider'] == 'canned'
    assert params['speech']['tier'] == 'tts'
    assert params['speech']['phrases_file'] == '/home/run/x2_greeter/phrases.yaml'
    assert params['camera']['source'] == 'stereo'

    assert '# which brain' in applied
    assert params['camera']['rotate_180'] is True, '这台机器自己的设置不能被动'
    assert len(params['speech']['wake_invitations']) == 1


def test_the_phrases_written_to_the_robot_are_the_ones_the_robot_loads(weights):
    from x2_greeter.cognition.canned import load_phrases

    fake = FakeRobot()
    run_full(fake, weights)
    written = fake.files[f'{R.ROOT}/phrases.yaml']

    import tempfile
    with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False,
                                     encoding='utf-8') as handle:
        handle.write(written)
    assert list(load_phrases(handle.name)) == PHRASES


def test_an_empty_greeting_list_never_reaches_a_robot():
    """The window substitutes the standard greetings before it gets here, so
    an empty list arriving means something upstream went wrong -- and a robot
    that sees people and says nothing is indistinguishable from a broken one.
    """
    fake = FakeRobot()
    plan = Plan(package_dir=str(REPO), phrases=[], weights_dir=str(REPO))
    outcome = Deployment(fake, plan).run()

    assert not outcome.ok
    assert fake.calls == [], '不该连机器人'
