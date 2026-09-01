"""The layering rule, enforced rather than merely documented.

Every ROS import outside ros/ and sim/ is a test that stops running on
Windows, so this guard is what keeps the whole core suite fast and portable.
"""
import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / 'x2_greeter'
ROS_ONLY = {'rclpy', 'aimdk_msgs', 'sensor_msgs', 'std_msgs', 'geometry_msgs',
            'cv_bridge', 'ament_index_python', 'launch', 'launch_ros'}


def imported_roots(path: Path):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split('.')[0]


def modules_under(*layers):
    for path in sorted(PACKAGE_ROOT.rglob('*.py')):
        rel = path.relative_to(PACKAGE_ROOT)
        if rel.parts and rel.parts[0] in layers:
            yield path


def submodules_of(path: Path):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split('.')
            if parts[0] == 'x2_greeter' and len(parts) > 1:
                yield parts[1]


def test_the_package_root_exists():
    assert PACKAGE_ROOT.is_dir(), PACKAGE_ROOT


def test_no_ros_imports_outside_the_ros_and_sim_layers():
    offenders = []
    for path in modules_under('core', 'cognition'):
        for root in imported_roots(path):
            if root in ROS_ONLY:
                offenders.append(f'{path.relative_to(PACKAGE_ROOT)} imports {root}')
    assert offenders == [], '\n'.join(offenders)


def test_core_does_not_depend_on_cognition_or_ros():
    offenders = []
    for path in modules_under('core'):
        for sub in submodules_of(path):
            if sub in ('cognition', 'ros', 'sim'):
                offenders.append(f'{path.relative_to(PACKAGE_ROOT)} imports x2_greeter.{sub}')
    assert offenders == [], '\n'.join(offenders)


def test_cognition_does_not_depend_on_ros():
    offenders = []
    for path in modules_under('cognition'):
        for sub in submodules_of(path):
            if sub in ('ros', 'sim'):
                offenders.append(f'{path.relative_to(PACKAGE_ROOT)} imports x2_greeter.{sub}')
    assert offenders == [], '\n'.join(offenders)


def test_importing_the_package_does_not_pull_in_rclpy():
    init = PACKAGE_ROOT / '__init__.py'
    assert init.read_text(encoding='utf-8').strip() == ''


def module_name_for(path: Path) -> str:
    """Build the dotted module name from the path relative to the package
    root, rather than flattening to `x2_greeter.{layer}.{stem}` -- so this
    keeps working if a subdirectory is ever added under core/ or cognition/.
    """
    rel = path.relative_to(PACKAGE_ROOT.parent).with_suffix('')
    return '.'.join(rel.parts)


@pytest.mark.parametrize('layer', ['core', 'cognition'])
def test_every_layer_module_is_importable_without_ros(layer):
    import importlib
    for path in modules_under(layer):
        if path.name == '__init__.py':
            continue
        importlib.import_module(module_name_for(path))
