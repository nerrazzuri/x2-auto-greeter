"""The deployment must leave nothing behind when it is removed.

tools/deploy/uninstall.sh is short and complete for exactly one reason: every
byte the install writes lands either inside $X2_GREETER_ROOT or in the single
systemd unit file. That is a property of the *other* scripts, not of
uninstall.sh, and nothing else in the suite checks it -- a one-line edit that
installs a package with --user, or lets a model cache default to $HOME, keeps
every other test green and quietly makes the deployment permanent.

faster-whisper's 'small' model is ~460 MB. That is the one this is really
guarding: a cache under $HOME survives an uninstall, and $HOME/aimdk* is
reserved by the SDK and erased on a firmware upgrade.

Plain file reads, no ROS.
"""
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[3].parent / 'tools' / 'deploy'


def _script(name: str) -> str:
    path = DEPLOY / name
    assert path.is_file(), path
    return path.read_text(encoding='utf-8')


def test_the_deploy_directory_is_where_this_thinks_it_is():
    # If the repo is rearranged, every other test here would pass vacuously.
    assert DEPLOY.is_dir(), DEPLOY


def test_the_whisper_cache_lives_inside_the_removable_root():
    env = _script('env.sh')
    assert 'HF_HOME' in env, (
        'env.sh does not set HF_HOME, so faster-whisper caches its ~460 MB '
        'model wherever HuggingFace defaults to -- under $HOME, outside the '
        'root uninstall.sh removes')
    line = next(l for l in env.splitlines() if l.strip().startswith('export HF_HOME'))
    assert '$X2_GREETER_ROOT' in line, (
        f'HF_HOME is set to {line.strip()!r}, which is not inside the '
        'deployment root; removing the root would not remove the cache')


def _code_lines(name: str):
    """Everything but the comments. These scripts explain themselves at
    length, and the explanations name the very flags this file forbids."""
    for line in _script(name).splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            yield stripped


@pytest.mark.parametrize('name', ['install.sh', 'start.sh', 'service.sh'])
def test_no_script_installs_python_packages_for_the_user(name):
    # `run` is the account every robot ROS node uses. A --user install shadows
    # the system packages those nodes import, and ~/.local is outside the root.
    offenders = [l for l in _code_lines(name) if '--user' in l]
    assert not offenders, (
        f'{name} installs into ~/.local: that shadows the robot\'s own '
        f'packages for every ROS node on the machine, and survives '
        f'uninstall -- {offenders}')


def test_python_dependencies_are_installed_into_the_deployment_root():
    install = _script('install.sh')
    assert '--target=$ROOT/deps' in install, (
        'install.sh must pip install --target=$ROOT/deps so the dependencies '
        'go with the deployment when it is removed')


def test_stopping_stops_both_nodes():
    # The Phase 1 greeter and the Phase 2 conversation node register an MC
    # input source under the same name at the same priority and both hold
    # audio focus, so the second one up can be left unable to gesture with
    # nothing wrong-looking in its log. Whichever is running must be stopped.
    stop = _script('stop.sh')
    for launch in ('greeter.launch', 'conversation.launch'):
        assert launch in stop, f'stop.sh does not stop {launch}'


def test_uninstall_kills_both_nodes_too():
    uninstall = _script('uninstall.sh')
    for launch in ('greeter.launch', 'conversation.launch'):
        assert launch in uninstall, f'uninstall.sh does not stop {launch}'


def test_uninstall_removes_the_root_and_the_unit():
    uninstall = _script('uninstall.sh')
    assert 'rm -rf "$X2_GREETER_ROOT"' in uninstall
    assert 'rm -f "$UNIT"' in uninstall


def test_nothing_under_agibot_is_ever_written():
    # The SDK reserves it, and PC1 is off limits entirely. A write here would
    # not be undone by removing our root.
    for name in ('install.sh', 'start.sh', 'service.sh', 'env.sh', 'stop.sh'):
        for line in _code_lines(name):
            if '/agibot' not in line:
                continue
            assert not any(line.startswith(w) for w in ('cp ', 'rm ', 'mv ',
                                                        'mkdir ', 'rsync ')), \
                f'{name} writes under /agibot: {line!r}'
