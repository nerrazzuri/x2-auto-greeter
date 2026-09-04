"""What the deployment installs must not break what the robot already has.

$X2_GREETER_ROOT/deps is placed ahead of the system path by env.sh, which is
what keeps our Python packages out of ~/.local. The cost is that anything
landing in deps shadows the robot's own copy of the same package -- and one
of them, numpy, is the one the system OpenCV is compiled against.

This is not hypothetical. Installing faster-whisper unpinned put numpy 2.2.6
into deps on a robot whose system numpy is 1.26.1, and every `import cv2` in
the node then failed with "numpy.core.multiarray failed to import": no
detector, no cv_bridge, no depth reprojection. The node does not start, and
nothing about the error names faster-whisper.

Plain file reads, no ROS.
"""
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[3].parent / 'tools' / 'deploy'
INSTALL = DEPLOY / 'install.sh'


def test_the_installer_pins_numpy_below_2():
    text = INSTALL.read_text(encoding='utf-8')
    assert "'numpy<2'" in text or '"numpy<2"' in text, (
        'install.sh does not pin numpy<2. faster-whisper pulls numpy 2.x, '
        "deps shadows the system numpy, and the robot's OpenCV is built "
        'against 1.x -- the node dies on import cv2 with an error that does '
        'not mention numpy or whisper')


def test_the_pin_is_on_the_same_command_as_faster_whisper():
    # A pin in a separate later command is undone by pip resolving
    # faster-whisper's own dependency first.
    lines = INSTALL.read_text(encoding='utf-8').splitlines()
    installs = [i for i, l in enumerate(lines) if 'faster-whisper' in l
                and 'pip install' in ' '.join(lines[max(0, i - 1):i + 1])]
    assert installs, 'no pip install line for faster-whisper found'
    for i in installs:
        window = ' '.join(lines[max(0, i - 2):i + 1])
        assert 'numpy<2' in window, (
            f'line {i + 1} installs faster-whisper without the numpy pin '
            f'on the same command: {lines[i].strip()!r}')


def test_the_installer_verifies_opencv_still_imports():
    # The pin is a prediction; this is the check that it held. Without it a
    # future dependency bump reintroduces the fault silently, on a robot,
    # with the failure appearing only at launch.
    text = INSTALL.read_text(encoding='utf-8')
    assert 'import numpy, cv2' in text or 'import cv2' in text, (
        'install.sh never imports cv2 after installing the deps, so a '
        'shadowed numpy is not discovered until the node fails to launch')
