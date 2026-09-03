"""The shipped launch file's parameter precedence.

ROS 2 applies a Node's `parameters` list left to right, so a dict listed
after the params file beats the file. `venue.profile` used to be passed as
such a dict with a non-empty launch-argument default, which meant editing
`venue.profile` in `config/conversation.yaml` did nothing at all -- and
`docs/HARDWARE_BRINGUP.md` section 8 sends the operator to that file. This
file pins the corrected precedence: the YAML is the authority, `venue:=` is
an override for one launch.

Marked `ros` and with every import deferred into function scope: `launch`,
`launch_ros` and `ament_index_python` exist only in the Docker harness, and
a module-level import would turn "deselected on host" into a collection
error.
"""
import pytest

pytestmark = pytest.mark.ros


def _launch_module():
    """Import the launch file that is actually installed into share/."""
    import importlib.util
    from pathlib import Path

    from ament_index_python.packages import get_package_share_directory

    path = (Path(get_package_share_directory('x2_greeter')) / 'launch'
            / 'conversation.launch.py')
    assert path.is_file(), path
    spec = importlib.util.spec_from_file_location('conversation_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _context(**launch_arguments):
    """A LaunchContext with the file's declared defaults applied, plus any
    argument a `name:=value` on the command line would have set first."""
    from launch import LaunchContext
    from launch.actions import DeclareLaunchArgument

    module = _launch_module()
    description = module.generate_launch_description()
    context = LaunchContext()
    context.launch_configurations.update(launch_arguments)
    for entity in description.entities:
        if isinstance(entity, DeclareLaunchArgument):
            entity.execute(context)
    return module, context


def _venue_overrides(parameters):
    return [entry for entry in parameters
            if isinstance(entry, dict) and 'venue.profile' in entry]


def test_the_yaml_wins_when_no_venue_argument_is_given():
    module, context = _context()
    parameters = module.conversation_parameters(context)
    assert _venue_overrides(parameters) == [], (
        'with no venue:= on the command line nothing may be layered over '
        "the params file, or the operator's edit to venue.profile in "
        'config/conversation.yaml is silently discarded and a robot in a '
        'mall atrium reads the clothing-store script')


def test_the_params_file_is_still_first_and_is_the_shipped_one():
    module, context = _context()
    parameters = module.conversation_parameters(context)
    assert parameters, 'the node must be given its parameters'
    assert str(parameters[0]).endswith('conversation.yaml')


def test_an_explicit_venue_argument_still_overrides_the_file():
    module, context = _context(venue='mall_atrium')
    parameters = module.conversation_parameters(context)
    assert _venue_overrides(parameters) == [{'venue.profile': 'mall_atrium'}]
    assert parameters.index({'venue.profile': 'mall_atrium'}) > 0, (
        'an explicit override has to come after the file to beat it')


def test_the_declared_default_for_venue_is_empty():
    from launch.actions import DeclareLaunchArgument
    from launch.utilities import perform_substitutions
    from launch import LaunchContext

    module = _launch_module()
    description = module.generate_launch_description()
    declarations = {entity.name: entity
                    for entity in description.entities
                    if isinstance(entity, DeclareLaunchArgument)}
    assert 'venue' in declarations
    default = perform_substitutions(LaunchContext(),
                                    declarations['venue'].default_value)
    assert default == '', (
        "venue's default must stay empty: any non-empty default is layered "
        'over the params file on every launch and makes the YAML key a lie')
