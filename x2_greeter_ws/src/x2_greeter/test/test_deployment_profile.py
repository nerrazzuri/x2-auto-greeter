"""The deployment profile and the script that applies it.

install.sh --site klgw is the whole configuration step of a deployment: get
this wrong and a robot greets people from the wrong script, or calls a cloud
backend that was meant to be off. Both of those look like a working robot.

Plain YAML and one stdlib script, so no ROS and no marker -- the same shape as
test_shipped_config.py.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[2]
PROFILE = REPO / 'tools' / 'deploy' / 'sites' / 'klgw.yaml'
APPLY = REPO / 'tools' / 'deploy' / 'apply_site.py'
SHIPPED = PACKAGE / 'config' / 'greeter.yaml'
PHRASES = PACKAGE / 'config' / 'phrases-klgw.yaml'

SHARE = '/home/run/x2_greeter/ws/install/x2_greeter/share/x2_greeter/config'


def _apply(site: Path, profile: Path = PROFILE):
    return subprocess.run(
        [sys.executable, str(APPLY), str(site), str(profile), '--share', SHARE],
        capture_output=True, text=True)


def _params(path: Path):
    return next(iter(yaml.safe_load(path.read_text()).values()))['ros__parameters']


@pytest.fixture
def site(tmp_path):
    """A site.yaml seeded the way install.sh seeds one: a copy of the shipped file."""
    path = tmp_path / 'site.yaml'
    path.write_text(SHIPPED.read_text())
    return path


# -- the phrases the client actually supplied --------------------------------

def test_the_klgw_phrases_load_through_the_backend_loader():
    from x2_greeter.cognition.canned import load_phrases

    phrases = load_phrases(PHRASES)
    assert len(phrases) == 20
    assert all(p.strip() for p in phrases)


def test_the_phrases_are_speakable_by_a_tts_engine():
    """No curly quotes, dashes or list numbering survived the transcription.

    These are read aloud, and the vendor TTS says punctuation it does not
    recognise rather than skipping it.
    """
    from x2_greeter.cognition.canned import load_phrases

    for phrase in load_phrases(PHRASES):
        bad = [c for c in phrase if c in '‘’“”–—*_#']
        assert not bad, f'{phrase!r} contains {bad}'
        assert not phrase.lstrip()[:3].strip().rstrip('.').isdigit(), (
            f'{phrase!r} still carries the numbering from the client document')


# -- applying the profile ----------------------------------------------------

def test_applying_the_profile_sets_every_key_it_names(site):
    result = _apply(site)
    assert result.returncode == 0, result.stderr

    params = _params(site)
    wanted = yaml.safe_load(PROFILE.read_text())
    for block, keys in wanted.items():
        for key, value in keys.items():
            expected = value.replace('@SHARE@', SHARE) if isinstance(value, str) else value
            assert params[block][key] == expected, f'{block}.{key}'


def test_the_profile_turns_off_the_cloud_backend_and_the_recordings(site):
    """Named separately from the loop above: these two are the point of it.

    provider must not reach Anthropic (no key is deployed, and the client
    bought a fixed script), and tier must not be able to demote to the PC3
    recordings, which say lines from config/phrases.yaml instead.
    """
    assert _apply(site).returncode == 0
    speech = _params(site)['speech']
    assert _params(site)['backend']['provider'] == 'canned'
    assert speech['tier'] == 'tts'
    assert speech['phrases_file'].endswith('phrases-klgw.yaml')


def test_the_greeting_still_ends_with_a_wake_invitation(site):
    """The profile must not quietly drop the one sentence that tells a person
    how to start a real conversation with the robot."""
    assert _apply(site).returncode == 0
    assert len(_params(site)['speech']['wake_invitations']) >= 1


def test_the_profile_leaves_the_safety_gates_alone(site):
    """A deployment profile may choose phrases and cameras. It may not move an
    interlock -- and the shipped values are asserted in test_shipped_config.py,
    so comparing before and after is enough."""
    before = _params(site)
    assert _apply(site).returncode == 0
    after = _params(site)

    assert after['detect'] == before['detect']
    assert after['gestures'] == before['gestures']
    assert after['mc_input'] == before['mc_input']
    assert after['interaction'] == before['interaction']
    assert after['presence'] == before['presence']


def test_the_comments_in_site_yaml_survive(site):
    """site.yaml is the robot's own record of what was proven on it. A profile
    that rewrote the file through a YAML dumper would erase all of it."""
    before = site.read_text().count('#')
    assert _apply(site).returncode == 0
    assert site.read_text().count('#') == before


def test_applying_it_twice_changes_nothing_the_second_time(site):
    assert _apply(site).returncode == 0
    after_first = site.read_text()
    second = _apply(site)
    assert second.returncode == 0
    assert site.read_text() == after_first
    assert 'already applied' in second.stdout


def test_a_key_the_site_file_does_not_have_is_inserted(tmp_path):
    site = tmp_path / 'site.yaml'
    site.write_text('/**:\n  ros__parameters:\n    backend:\n      model: x\n')
    profile = tmp_path / 'p.yaml'
    profile.write_text('backend:\n  provider: canned\n')
    assert _apply(site, profile).returncode == 0
    assert _params(site)['backend'] == {'model': 'x', 'provider': 'canned'}


def test_a_missing_block_is_refused_rather_than_guessed_at(tmp_path):
    site = tmp_path / 'site.yaml'
    site.write_text('/**:\n  ros__parameters:\n    backend:\n      provider: claude\n')
    profile = tmp_path / 'p.yaml'
    profile.write_text('nosuchblock:\n  key: 1\n')
    result = _apply(site, profile)
    assert result.returncode != 0
    assert 'nosuchblock' in result.stderr
    assert site.read_text().endswith('provider: claude\n'), 'left untouched'


# -- install.sh's side of it -------------------------------------------------

INSTALL = REPO / 'tools' / 'deploy' / 'install.sh'
SITES = REPO / 'tools' / 'deploy' / 'sites'


def _code_lines(path: Path):
    """The script without its comments -- which discuss the very flags and
    failures these tests look for, and would otherwise match."""
    return '\n'.join(line for line in path.read_text().splitlines()
                     if not line.lstrip().startswith('#'))


@pytest.mark.parametrize('profile', sorted(SITES.glob('*.yaml')), ids=lambda p: p.stem)
def test_every_site_profile_names_blocks_the_shipped_config_has(profile):
    """A profile that names a block greeter.yaml does not have is a typo that
    would otherwise surface as a failed install halfway through a deployment."""
    shipped = _params(SHIPPED)
    for block, keys in yaml.safe_load(profile.read_text()).items():
        assert block in shipped, f'{profile.name}: no "{block}" block in greeter.yaml'
        assert isinstance(keys, dict), f'{profile.name}: "{block}" must be a block of keys'


def test_an_unknown_site_name_fails_before_anything_is_copied(tmp_path):
    result = subprocess.run(
        ['bash', str(INSTALL), 'nobody@example.invalid', '--site', 'nosuchplace'],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert 'nosuchplace' in result.stderr
    assert 'klgw' in result.stderr, 'it should say which profiles do exist'


def test_the_service_step_allocates_a_terminal_for_sudo():
    """sudo cannot ask for a password down a pipe. Without -t this step ended
    in an askpass error rather than a prompt, and the unit was never installed
    while the script reported success."""
    code = _code_lines(INSTALL)
    assert 'ssh -t' in code
    for line in code.splitlines():
        if 'sudo ' in line and 'ssh ' in line:
            assert 'ssh -t' in line, f'sudo over a pipe: {line.strip()}'


def test_the_log_is_created_before_the_unit_is_enabled():
    """The unit's StandardOutput=append: creates greeter.log as root if it is
    missing, and the node runs as `run` and then cannot write to it."""
    code = _code_lines(INSTALL)
    touch = code.find('touch $ROOT/greeter.log')
    enable = code.find('systemctl enable')
    assert touch != -1, 'the log is never created'
    assert touch < enable, 'the log must exist before systemd can create it as root'


def test_starting_goes_through_systemd_when_the_unit_is_installed():
    """bin/start.sh pkills whatever is running; systemd restarts the service
    thirty seconds later, and the robot ends up with two greeters."""
    code = _code_lines(INSTALL)
    assert 'systemctl start x2-greeter' in code
    assert 'list-unit-files x2-greeter.service' in code


# -- what reaches the robot --------------------------------------------------

UNIT = REPO / 'tools' / 'deploy' / 'x2-greeter.service'


def test_the_design_documents_are_not_shipped_to_the_robot():
    """docs/superpowers holds the spec and the plan this was built from. A
    robot standing in a client's mall is not where they belong, and anyone
    deploying has the repository in front of them already."""
    # Continuations joined first: the command is spread over two lines, and
    # the excludes sit on the other one from the destination.
    joined = _code_lines(INSTALL).replace('\\\n', ' ')
    copies = [line for line in joined.splitlines()
              if 'rsync' in line and '$ROOT/repo/' in line]
    assert copies, 'the line that copies the repository has moved'
    assert "--exclude 'docs'" in copies[0], copies[0]


def test_nothing_deployed_points_at_a_path_that_is_not_deployed():
    """The unit file used to name docs/AGENT_BRINGUP_GUIDE.md as a file:// URI
    under $ROOT/repo. Excluding docs/ turned that into a dangling reference on
    every robot, which is the kind of thing nobody notices until they follow
    it."""
    for path in (UNIT, INSTALL):
        for reference in re.findall(r'file:///home/run/x2_greeter/repo/(\S+)',
                                    path.read_text()):
            assert not reference.startswith('docs/'), (
                f'{path.name} points at {reference}, which install.sh no '
                f'longer copies')
