"""Telling the cable apart from the address, on either operating system.

A customer who cannot reach the robot has done one of two things: not pushed
the plug in, or not given their wired adapter an address on the robot's
subnet. The fixes have nothing in common, so guessing wrong costs the visit.

Also the one place the Windows port is more than "paramiko instead of ssh":
the instructions name a settings panel, and naming the wrong one is worse than
naming none.
"""
import platform
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / 'tools'))

from deployer.core import network as N          # noqa: E402

ROBOT = '10.0.1.41'


def test_an_address_on_the_robots_subnet_is_recognised():
    assert N.same_subnet('10.0.1.49', ROBOT)
    assert N.same_subnet('10.0.1.1', ROBOT)


def test_an_address_somewhere_else_is_not():
    assert not N.same_subnet('192.168.100.147', ROBOT)
    assert not N.same_subnet('172.20.10.5', ROBOT)


def test_a_nonsense_address_is_not_treated_as_on_subnet():
    assert not N.same_subnet('', ROBOT)
    assert not N.same_subnet('not-an-address', ROBOT)


def test_the_suggested_address_is_on_the_robots_subnet_but_not_the_robot():
    suggested = N._suggest_address(ROBOT)
    assert N.same_subnet(suggested, ROBOT)
    assert suggested != ROBOT


def test_being_off_the_subnet_is_reported_as_an_address_problem(monkeypatch):
    monkeypatch.setattr(N, 'port_open', lambda *a, **k: False)
    monkeypatch.setattr(N, 'route_address', lambda _h: '192.168.100.147')

    result = N.check(ROBOT)
    assert not result
    assert '网段' in result.detail
    assert '192.168.100.147' in result.detail, '要告诉客户现在是什么地址'
    assert N._suggest_address(ROBOT) in result.detail, '要给一个能照着填的地址'


def test_being_on_the_subnet_but_silent_is_reported_as_a_cable_problem(monkeypatch):
    """Same failure to connect, opposite fix. Sending someone to the network
    settings when the plug is loose wastes the visit."""
    monkeypatch.setattr(N, 'port_open', lambda *a, **k: False)
    monkeypatch.setattr(N, 'route_address', lambda _h: '10.0.1.49')

    result = N.check(ROBOT)
    assert not result
    assert '网线' in result.detail
    # The distinction that matters: no instructions for changing the address,
    # because the address is already right.
    assert '控制面板' not in result.detail and '设置 → 网络' not in result.detail


def test_reachable_says_so_and_nothing_else(monkeypatch):
    monkeypatch.setattr(N, 'port_open', lambda *a, **k: True)
    monkeypatch.setattr(N, 'route_address', lambda _h: '10.0.1.49')

    result = N.check(ROBOT)
    assert result
    assert result.local_address == '10.0.1.49'


def test_the_instructions_name_the_panel_this_machine_actually_has(monkeypatch):
    monkeypatch.setattr(platform, 'system', lambda: 'Windows')
    windows = N.fix_instructions(ROBOT)
    assert '控制面板' in windows and 'TCP/IPv4' in windows
    assert '设置 → 网络' not in windows

    monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    linux = N.fix_instructions(ROBOT)
    assert '设置 → 网络' in linux
    assert '控制面板' not in linux


def test_both_sets_of_instructions_give_the_same_address_to_type():
    for system in ('Windows', 'Linux'):
        import unittest.mock as mock
        with mock.patch.object(platform, 'system', lambda: system):
            assert N._suggest_address(ROBOT) in N.fix_instructions(ROBOT)


def test_route_address_does_not_raise_on_an_unroutable_host():
    """Called before anything else, on a machine with no network at all."""
    assert N.route_address('203.0.113.1') is None or isinstance(
        N.route_address('203.0.113.1'), str)


# -- packaged as one file ----------------------------------------------------
#
# PyInstaller unpacks the program into a temporary directory that also holds
# the Python runtime it is running on. Both paths below are only taken in that
# state, which no test run ever reaches by accident, so they are the easiest
# thing in the deployer to break without noticing.

def test_the_bundled_payload_is_what_gets_uploaded_not_the_whole_bundle(
        monkeypatch, tmp_path):
    """Mirroring the unpack directory itself would upload a Python interpreter
    to the robot."""
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    import importlib

    from deployer.gui import app
    importlib.reload(app)
    try:
        assert app.PACKAGE_ROOT == tmp_path / 'payload'
    finally:
        monkeypatch.delattr(sys, '_MEIPASS', raising=False)
        importlib.reload(app)


def test_weights_shipped_inside_the_executable_are_found(monkeypatch, tmp_path):
    """The customer's laptop is cabled to the robot and may have no internet,
    so a copy travels inside the program."""
    from deployer.core import assets

    bundled = tmp_path / 'models'
    bundled.mkdir()
    for name in assets.NAMES:
        (bundled / name).write_text('x')

    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    try:
        assert assets.locate().directory == str(bundled)
    finally:
        monkeypatch.delattr(sys, '_MEIPASS', raising=False)


def test_unfrozen_the_package_root_is_the_repository():
    from deployer.gui import app

    assert (app.PACKAGE_ROOT / 'x2_greeter_ws').is_dir()
    assert (app.PACKAGE_ROOT / 'tools' / 'deploy').is_dir()


# -- the translation table ---------------------------------------------------

def test_every_string_exists_in_both_languages():
    """A key with only one language silently falls back to Chinese, which an
    English-speaking customer reads as the window half-translating itself."""
    from deployer.core import i18n

    missing = [f'{key}.{lang}' for key, entry in i18n.STRINGS.items()
               for lang in i18n.LANGUAGES if not entry.get(lang)]
    assert missing == []


def test_both_languages_take_the_same_parameters():
    """A parameter present in one language and not the other formats fine and
    then shows a sentence with a hole in it."""
    import re
    from deployer.core import i18n

    fields = lambda text: set(re.findall(r'\{(\w+)\}', text))   # noqa: E731
    for key, entry in i18n.STRINGS.items():
        assert fields(entry['zh']) == fields(entry['en']), key


def test_a_message_may_have_a_parameter_called_key():
    """err.wrong_field does. Naming it collided with t()'s own argument and
    replaced the explanation with a TypeError."""
    from deployer.core import i18n

    rendered = i18n.t('err.wrong_field', key='backend', want='canned', got='claude')
    assert 'backend' in rendered and 'canned' in rendered and 'claude' in rendered


def test_an_unknown_key_shows_the_key_rather_than_raising():
    """A customer halfway through a deployment is better served by seeing
    `step.upload` than by the window closing."""
    from deployer.core import i18n

    assert i18n.t('no.such.key') == 'no.such.key'


def test_switching_language_changes_what_comes_out():
    from deployer.core import i18n

    before = i18n.language()
    try:
        i18n.set_language('zh')
        chinese = i18n.t('deploy.start')
        i18n.set_language('en')
        assert i18n.t('deploy.start') != chinese
    finally:
        i18n.set_language(before)
