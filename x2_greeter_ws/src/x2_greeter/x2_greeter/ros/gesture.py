"""Performing a preset motion.

Motion and area IDs arrive already resolved from core.gestures — an LLM never
reaches this far with an unvalidated value.
"""
from __future__ import annotations

from aimdk_msgs.msg import CommonState, McControlArea, McPresetMotion, RequestHeader
from aimdk_msgs.srv import SetMcPresetMotion

from x2_greeter.ros.service_call import call_with_retry

PRESET_MOTION_SERVICE = '/aimdk_5Fmsgs/srv/SetMcPresetMotion'


class GestureDispatcher:
    def __init__(self, node, callback_group=None) -> None:
        self._node = node
        self._client = node.create_client(SetMcPresetMotion, PRESET_MOTION_SERVICE,
                                          callback_group=callback_group)

    def perform(self, motion_id: int, area_id: int) -> bool:
        """Issue one preset motion. True if the robot accepted or started it."""
        request = SetMcPresetMotion.Request()
        request.header = RequestHeader()

        motion = McPresetMotion()
        motion.value = int(motion_id)
        area = McControlArea()
        area.value = int(area_id)
        request.motion = motion
        request.area = area
        # False: a greeting is never important enough to cut short a motion the
        # robot is already performing.
        request.interrupt = False

        def stamp(req):
            req.header.stamp = self._node.get_clock().now().to_msg()

        response = call_with_retry(self._client, request, before_attempt=stamp,
                                   logger=self._node.get_logger())
        if response is None:
            self._node.get_logger().error('SetMcPresetMotion did not respond')
            return False

        if response.response.header.code == 0:
            self._node.get_logger().info(
                f'gesture {motion_id} on area {area_id} accepted '
                f'(task {response.response.task_id})')
            return True
        if response.response.state.value == CommonState.RUNNING:
            self._node.get_logger().info(
                f'gesture {motion_id} on area {area_id} running '
                f'(task {response.response.task_id})')
            return True

        self._node.get_logger().error(
            f'gesture {motion_id} on area {area_id} rejected '
            f'(task {response.response.task_id})')
        return False
