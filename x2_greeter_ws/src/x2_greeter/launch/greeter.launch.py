"""ros2 launch x2_greeter greeter.launch.py [params_file:=...] [fake_robot:=true]"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('x2_greeter')
    default_params = os.path.join(share, 'config', 'greeter.yaml')

    params_file = LaunchConfiguration('params_file')
    fake_robot = LaunchConfiguration('fake_robot')

    return LaunchDescription([
        DeclareLaunchArgument(
            'params_file', default_value=default_params,
            description='YAML file of x2_greeter parameters'),
        DeclareLaunchArgument(
            'fake_robot', default_value='false',
            description='Also launch the simulated robot (bench testing only)'),
        Node(
            package='x2_greeter', executable='greeting_node', name='x2_greeter',
            output='screen', parameters=[params_file]),
        Node(
            package='x2_greeter', executable='fake_robot', name='fake_robot',
            output='screen', condition=IfCondition(fake_robot)),
    ])
