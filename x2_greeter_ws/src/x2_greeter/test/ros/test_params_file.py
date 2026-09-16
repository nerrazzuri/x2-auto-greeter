"""The node must survive the params file it is actually deployed with.

Every other test builds the node from `parameter_overrides=[Parameter(...)]`,
and that route cannot reproduce what a YAML file does: an empty list passed as
a Python value carries its type, while an empty sequence in YAML does not, so
rclpy has no element type to infer and leaves the parameter uninitialised.
Measured here -- `Parameter('g.w', value=[])` reads back as `[]`, the same key
written `w: []` in a params file raises ParameterUninitializedException.

config/greeter.yaml ships `gestures.enabled_while_walking: []`, and install.sh
seeds every robot's site.yaml from that file, so the deployed configuration hit
this on the first launch on a fresh robot (2026-09-11) and the node died during
construction. A file is the only way to hold the node to that.
"""
import textwrap

import pytest

pytestmark = pytest.mark.ros

# Enough of a config to build the node without loading detector weights or
# reaching the network, plus the value that used to kill it.
PARAMS = textwrap.dedent("""\
    /**:
      ros__parameters:
        detect:
          detector: scripted
        backend:
          provider: canned
        gestures:
          enabled_while_walking: []
    """)


@pytest.fixture
def params_file(tmp_path):
    path = tmp_path / 'site.yaml'
    path.write_text(PARAMS)
    return str(path)


def test_an_empty_list_in_a_params_file_is_uninitialised_not_empty(params_file):
    """The platform behaviour the guard exists for, pinned in one place."""
    import rclpy
    from rclpy.exceptions import ParameterUninitializedException
    from rclpy.node import Node

    rclpy.init(args=['--ros-args', '--params-file', params_file])
    try:
        node = Node('probe')
        node.declare_parameter('gestures.enabled_while_walking', [])
        with pytest.raises(ParameterUninitializedException):
            node.get_parameter('gestures.enabled_while_walking').value
        node.destroy_node()
    finally:
        rclpy.shutdown()


def test_the_node_starts_from_a_params_file_that_disables_walking_gestures(params_file):
    import rclpy

    from x2_greeter.ros.greeting_node import GreetingNode

    rclpy.init(args=['--ros-args', '--params-file', params_file])
    try:
        node = GreetingNode()
        try:
            assert node._walking_gestures == (), (
                'an empty list in the params file means no gestures while '
                'walking, which is what the shipped config asks for')
        finally:
            node.destroy_node()
    finally:
        rclpy.shutdown()
