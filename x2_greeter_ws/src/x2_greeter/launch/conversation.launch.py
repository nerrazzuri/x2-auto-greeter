"""ros2 launch x2_greeter conversation.launch.py [params_file:=...] [venue:=...]

`venue:=` is an override, not a default. Leave it off and the venue comes
from `venue.profile` in the params file, which is what an operator editing
`config/conversation.yaml` at the venue expects to happen.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def conversation_parameters(context):
    """The x2_conversation parameter list, in precedence order.

    ROS 2 applies these left to right, so anything after the params file
    beats the file. The `venue:=` launch argument therefore gets appended
    only when it was actually given: its declared default is the empty
    string, not 'clothing_store'. With a non-empty default the dict always
    won, and editing `venue.profile` in `config/conversation.yaml` did
    nothing at all -- silently, with the clothing-store script coming out
    of a robot standing in a mall atrium. `docs/HARDWARE_BRINGUP.md`
    section 8 sends the operator to those files, so the file has to be the
    authority.
    """
    parameters = [LaunchConfiguration('params_file').perform(context)]
    venue = LaunchConfiguration('venue').perform(context).strip()
    if venue:
        parameters.append({'venue.profile': venue})
    return parameters


def _conversation_node(context, *args, **kwargs):
    return [Node(
        package='x2_greeter', executable='x2_conversation',
        name='x2_conversation', output='screen',
        parameters=conversation_parameters(context))]


def generate_launch_description():
    share = get_package_share_directory('x2_greeter')
    default_params = os.path.join(share, 'config', 'conversation.yaml')

    return LaunchDescription([
        DeclareLaunchArgument(
            'params_file', default_value=default_params,
            description='YAML file of x2_conversation parameters'),
        DeclareLaunchArgument(
            'venue', default_value='',
            description=('Venue profile name (config/venues/<venue>.yaml). '
                         'Empty -- the default -- means use venue.profile '
                         'from the params file.')),
        OpaqueFunction(function=_conversation_node),
    ])
