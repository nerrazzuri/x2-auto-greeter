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

    def __init__(self, *, startup_line=GOOD_LINE, build_ok=True, sudo_ok=True,
                 unit_installed=False, start_ok=True, scripts_ok=True):
        self.calls = []
        self.commands = []            # in full, where `calls` is abbreviated
        self.files = {f'{R.ROOT}/ws/install/x2_greeter/share/x2_greeter/config/'
                      'greeter.yaml': SHIPPED_SITE,
                      f'{R.ROOT}/repo/tools/deploy/x2-greeter.service': '[Unit]\n'}
        if unit_installed:            # left behind by an earlier permanent deploy
            self.files['/etc/systemd/system/x2-greeter.service'] = '[Unit]\n'
        self.startup_line = startup_line
        self.build_ok = build_ok
        self.sudo_ok = sudo_ok
        self.start_ok = start_ok
        self.scripts_ok = scripts_ok
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
        self.commands.append(command)
        if 'bin/start.sh' in command:
            self.calls[-1] = 'run start.sh'
            return R.Result(0 if self.start_ok else 1, '',
                            '' if self.start_ok else '== FAILED to start')
        if 'bin/stop.sh' in command:
            self.calls[-1] = 'run stop.sh'
            return R.Result(0, '== greeter stopped', '')
        if f'{R.ROOT}/env.sh' in command and 'cp ' in command:
            self.calls[-1] = 'run install scripts'
            return R.Result(0 if self.scripts_ok else 1, '',
                            '' if self.scripts_ok else 'cp: No such file')
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
        self.commands.append(command)
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


def _index(calls, wanted):
    found = [i for i, c in enumerate(calls) if c == wanted or c.startswith(wanted)]
    assert found, f'{wanted!r} never happened: {calls}'
    return found[0]


def test_deploying_once_to_a_robot_that_never_had_the_service_starts_it_by_hand(weights):
    """What a customer hit: "Deploy once" skips installing the unit and then
    asked systemd to restart it, on a robot where it had never existed."""
    fake = FakeRobot(unit_installed=False)
    outcome, events = run_full(fake, weights, install_service=False)

    assert outcome.ok, [e.message for e in events if e.status is Status.FAILED]
    assert 'run start.sh' in fake.calls
    assert not any('systemctl' in c for c in fake.calls), \
        '没装服务就不该碰 systemctl'

    # start.sh backgrounds the greeter with nohup. Without stdin from
    # /dev/null the greeter inherits the SSH channel, and the command does not
    # return until the greeter exits -- that is, never. Only a real robot
    # shows the hang, so the command itself is what can be held to it here.
    start = [c for c in fake.commands if 'bin/start.sh' in c][0]
    assert '< /dev/null' in start


def test_deploying_once_over_a_permanent_install_turns_boot_start_off(weights):
    """"Deploy once" promises the greeter will not come back after a power
    cycle. An enabled unit from an earlier permanent deploy would break that
    promise, and a running one would be restarted by systemd thirty seconds
    after start.sh killed it -- two greeters talking over each other."""
    fake = FakeRobot(unit_installed=True)
    outcome, events = run_full(fake, weights, install_service=False)

    assert outcome.ok
    disable = _index(fake.calls, 'sudo systemctl disable --now x2-greeter.service')
    assert disable < _index(fake.calls, 'run start.sh'), '要先关掉服务再手动启动'
    started = [e for e in events if e.status is Status.OK and e.step == '启动机器人程序']
    assert started and '开机' in started[0].message, '取消了开机自启要说出来'


def test_deploying_permanently_stops_a_greeter_started_by_hand_first(weights):
    """The mirror image: after "Deploy once", a greeter is running outside
    systemd, and restarting the service would add a second one beside it."""
    fake = FakeRobot()
    outcome, _ = run_full(fake, weights, install_service=True)

    assert outcome.ok
    assert _index(fake.calls, 'run stop.sh') < \
        _index(fake.calls, 'sudo systemctl restart x2-greeter.service')
    assert 'run start.sh' not in fake.calls


def test_a_greeter_that_will_not_start_by_hand_is_a_failure(weights):
    outcome, events = run_full(FakeRobot(start_ok=False), weights,
                               install_service=False)
    assert not outcome.ok
    failed = [e for e in events if e.status is Status.FAILED][0]
    assert failed.step == '启动机器人程序'
    assert 'FAILED to start' in failed.message


def test_the_scripts_the_robot_runs_are_installed_with_the_package(weights):
    """The unit runs bin/service.sh, "Deploy once" runs bin/start.sh, and the
    build sources env.sh. The command-line installer copied all of them; this
    deployment never did, and only worked on robots the installer had already
    been to."""
    fake = FakeRobot()
    run_full(fake, weights)

    install = _index(fake.calls, 'run install scripts')
    assert _index(fake.calls, 'mirror') < install < _index(fake.calls, 'run colcon build')
    command = [c for c in fake.commands if f'{R.ROOT}/env.sh' in c and 'cp ' in c][0]
    for script in ('start.sh', 'stop.sh', 'service.sh', 'uninstall.sh'):
        assert f'tools/deploy/{script}' in command, f'{script} 没装'
    assert 'chmod +x' in command


def test_every_script_the_deployment_installs_is_really_in_the_package():
    """Checked against the repository, not the fake: a renamed script would
    pass every test above and fail on the robot."""
    from deployer.core import deployment as D

    for name in ('env.sh', *D.SCRIPTS):
        assert (REPO / 'tools' / 'deploy' / name).is_file(), name


def test_scripts_that_fail_to_install_stop_the_deployment(weights):
    fake = FakeRobot(scripts_ok=False)
    outcome, _ = run_full(fake, weights)
    assert not outcome.ok
    assert 'run colcon build' not in fake.calls, '脚本没装上就不该往下编译'


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


# -- taking it off a robot ---------------------------------------------------

class UninstallRobot(FakeRobot):
    """Answers as a robot with a deployment on it, then as one without."""

    def __init__(self, *, leftovers='', stop_fails=False, survivor=False, **kw):
        super().__init__(**kw)
        self.leftovers = leftovers
        self.stop_fails = stop_fails
        self.survivor = survivor          # a greeter that outlives pkill

    def run(self, command, timeout=None):
        self.calls.append(f'run {command[:60]}')
        self.commands.append(command)
        if command.startswith('systemctl is-active'):
            return R.Result(0, 'active' if self.stop_fails else 'inactive', '')
        if command.startswith('pkill'):
            return R.Result(0, '31337' if self.survivor else '', '')
        if command.startswith('ls -d'):
            return R.Result(0, self.leftovers, '')
        return R.Result(0, '', '')


def uninstall(fake):
    from deployer.core.deployment import Uninstall
    events = []
    return Uninstall(fake, events.append).run(), events


def test_a_clean_uninstall_leaves_nothing(weights):
    outcome, events = uninstall(UninstallRobot())
    assert outcome.ok
    assert [e for e in events if e.status is Status.FAILED] == []


def test_the_service_is_disabled_before_the_files_go(weights):
    """Deleting the files under a running service leaves systemd restarting a
    program that is no longer there."""
    fake = UninstallRobot()
    uninstall(fake)
    disable = [i for i, c in enumerate(fake.calls) if 'disable' in c][0]
    delete = [i for i, c in enumerate(fake.calls) if 'rm -rf' in c][0]
    assert disable < delete


def test_disable_comes_with_now_so_it_cannot_come_back_between_commands(weights):
    fake = UninstallRobot()
    uninstall(fake)
    assert any('disable --now' in c for c in fake.calls)


def test_a_service_that_will_not_stop_deletes_nothing(weights):
    """Half a deployment is worse than all of it: the unit would keep
    restarting a program whose files had been removed."""
    fake = UninstallRobot(stop_fails=True)
    outcome, events = uninstall(fake)
    assert not outcome.ok
    assert not any('rm -rf' in c for c in fake.calls), '不该删除任何文件'
    assert '停不下来' in [e for e in events if e.status is Status.FAILED][0].message


def test_leftovers_are_reported_rather_than_declared_clean(weights):
    """Every other failure in this project looked like success at the point
    the work was done, so the robot is asked what is left."""
    fake = UninstallRobot(leftovers='/home/run/x2_greeter')
    outcome, events = uninstall(fake)
    assert not outcome.ok
    assert 'x2_greeter' in [e for e in events if e.status is Status.FAILED][0].message


def test_nothing_under_agibot_is_touched(weights):
    """The vendor's own software was never modified, so there is nothing there
    to put back -- and deleting from it would break the robot."""
    fake = UninstallRobot()
    uninstall(fake)
    assert not any('/agibot' in c for c in fake.calls)


def test_the_vendor_agent_is_left_alone(weights):
    """Phase 1 never changes its run mode, so there is nothing to restore."""
    fake = UninstallRobot()
    uninstall(fake)
    assert not any('AgentProperties' in c or 'run_mode' in c for c in fake.calls)


def test_uninstall_stops_a_greeter_started_by_hand(weights):
    """"Deploy once" starts the greeter from bin/start.sh, outside systemd, so
    disabling the service does not touch it. Deleting its files under it
    leaves a greeter running from a directory that no longer exists."""
    fake = UninstallRobot()
    outcome, _ = uninstall(fake)
    assert outcome.ok
    kill = [i for i, c in enumerate(fake.calls) if c.startswith('run pkill')]
    delete = [i for i, c in enumerate(fake.calls) if 'rm -rf' in c]
    assert kill, '没有停手动启动的迎宾程序'
    assert kill[0] < delete[0]


def test_a_greeter_that_survives_being_stopped_deletes_nothing(weights):
    fake = UninstallRobot(survivor=True)
    outcome, events = uninstall(fake)
    assert not outcome.ok
    assert not any('rm -rf' in c for c in fake.calls), '还在跑就不该删文件'


# -- the commands themselves, run for real -----------------------------------
#
# FakeRobot answers whatever it is told to. That is how the check below went
# to a robot unable ever to pass: over SSH every command runs as
# `bash -c "<the whole command>"`, so a pgrep for a literal "x2_greeter/lib"
# finds the bash running it, and reported that as a greeter left behind. No
# fake can see that. Running the exact command through bash on this machine
# can.

def _captured(work):
    """The commands an Uninstall step sends, without a robot."""
    from deployer.core.deployment import Uninstall

    robot = UninstallRobot()
    work(Uninstall(robot))
    return [c for c in robot.commands if 'pgrep' in c or 'pkill' in c]


def _harmless(command, tmp_path):
    """The same command, pointed away from anything real on this machine."""
    return (command.replace(R.ROOT, str(tmp_path / 'not-installed'))
                   .replace('/etc/systemd/system/x2-greeter.service',
                            str(tmp_path / 'no.service')))


def _real_greeter_running():
    import subprocess
    # pgrep's own argv is excluded by pgrep, and pytest's does not contain it.
    return subprocess.run(['pgrep', '-f', 'x2_greeter/lib'],
                          capture_output=True).returncode == 0


needs_bash = pytest.mark.skipif(
    sys.platform != 'linux' or _real_greeter_running(),
    reason='needs bash and pgrep, and no greeter really running here')


@needs_bash
def test_the_leftover_check_does_not_find_itself(tmp_path):
    import subprocess

    [command] = _captured(lambda u: u._verify())
    ran = subprocess.run(['bash', '-c', _harmless(command, tmp_path)],
                         capture_output=True, text=True, timeout=30)
    assert ran.stdout.strip() == '', \
        f'什么都没装,检查却找到了:{ran.stdout.strip()}(是它自己的 bash)'


@needs_bash
def test_the_stop_command_does_not_kill_itself(tmp_path):
    """pkill -f matching its own bash kills the command halfway, which looks
    from the other end like a dropped connection."""
    import subprocess

    [command] = _captured(lambda u: u._stop_service())
    ran = subprocess.run(['bash', '-c', _harmless(command, tmp_path)],
                         capture_output=True, text=True, timeout=30)
    assert ran.returncode >= 0, f'被信号 {-ran.returncode} 杀掉了'
    assert ran.stdout.strip() == '', f'报告还有进程:{ran.stdout.strip()}'

