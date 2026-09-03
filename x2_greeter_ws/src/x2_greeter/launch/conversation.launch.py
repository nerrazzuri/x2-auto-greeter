"""ros2 launch x2_greeter conversation.launch.py [params_file:=...] [venue:=clothing_store]"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('x2_greeter')
    default_params = os.path.join(share, 'config', 'conversation.yaml')

    params_file = LaunchConfiguration('params_file')
    venue = LaunchConfiguration('venue')

    return LaunchDescription([
        DeclareLaunchArgument(
            'params_file', default_value=default_params,
            description='YAML file of x2_conversation parameters'),
        DeclareLaunchArgument(
            'venue', default_value='clothing_store',
            description='Venue profile name (config/venues/<venue>.yaml)'),
        Node(
            package='x2_greeter', executable='x2_conversation',
            name='x2_conversation', output='screen',
            parameters=[params_file, {'venue.profile': venue}]),
    ])
