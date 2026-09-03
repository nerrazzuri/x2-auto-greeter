import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'x2_greeter'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test', 'test.*']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        (os.path.join('share', package_name), ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
        (os.path.join('share', package_name, 'config', 'venues'),
         glob('config/venues/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Liang Kai Feng',
    maintainer_email='liangkaifeng1987@gmail.com',
    description='Greets a person who stops in front of the AgiBot X2 head camera.',
    license='Proprietary',
    entry_points={
        'console_scripts': [
            'greeting_node = x2_greeter.ros.greeting_node:main',
            'fake_robot = x2_greeter.sim.fake_robot:main',
        ],
    },
)
