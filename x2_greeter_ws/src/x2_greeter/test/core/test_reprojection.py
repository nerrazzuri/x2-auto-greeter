"""The geometry behind reading the chin camera's depth in the stereo picture."""
import math

import numpy as np
import pytest

from x2_greeter.core.reprojection import (DepthReprojector, Fisheye, Pinhole,
                                          coverage_fraction, pose_matrix,
                                          relative_pose, rpy_to_matrix)

# The X2's own URDF: both are fixed children of head_pitch_link.
RGBD_XYZ, RGBD_RPY = (0.05761, -0.011183, -0.04837), (2.2689, 0.0, 1.5708)
STEREO_XYZ, STEREO_RPY = (0.067995, 0.029784, 0.05), (-1.5708, 0.0, -1.574)


def _pinhole(width=64, height=48):
    return Pinhole(fx=100.0, fy=100.0, cx=width / 2, cy=height / 2,
                   width=width, height=height)


def _fisheye(width=128, height=96):
    k = np.array([[100.0, 0, width / 2], [0, 100.0, height / 2], [0, 0, 1]])
    return Fisheye(k=k, d=np.zeros(4), width=width, height=height)


# ------------------------------------------------------------------ rotations

def test_zero_rpy_is_the_identity():
    assert np.allclose(rpy_to_matrix(0, 0, 0), np.eye(3))


def test_yaw_turns_about_z():
    turned = rpy_to_matrix(0, 0, math.pi / 2) @ np.array([1.0, 0.0, 0.0])
    assert np.allclose(turned, [0, 1, 0], atol=1e-9)


def test_the_composition_order_is_urdf_fixed_axis():
    # Rz @ Ry @ Rx, not the other way round: a wrong order still produces a
    # valid rotation matrix, so nothing else here would catch it.
    r, p, y = 0.3, -0.2, 1.1
    expected = (rpy_to_matrix(0, 0, y) @ rpy_to_matrix(0, p, 0)
                @ rpy_to_matrix(r, 0, 0))
    assert np.allclose(rpy_to_matrix(r, p, y), expected)


# ----------------------------------------------------------------- transforms

def test_a_pose_relative_to_itself_is_the_identity():
    assert np.allclose(relative_pose(RGBD_XYZ, RGBD_RPY, RGBD_XYZ, RGBD_RPY),
                       np.eye(4), atol=1e-12)


def test_the_two_cameras_are_about_ten_centimetres_apart():
    """The sanity check that caught the transform being right on hardware: in
    the stereo optical frame y points down, and the chin module sits about
    10 cm below the head pair -- which is what the photograph shows."""
    transform = relative_pose(RGBD_XYZ, RGBD_RPY, STEREO_XYZ, STEREO_RPY)
    assert transform[1, 3] == pytest.approx(0.098, abs=0.005)
    assert np.linalg.norm(transform[:3, 3]) == pytest.approx(0.107, abs=0.01)


def test_relative_pose_composes_back_to_the_original():
    a_to_b = relative_pose(RGBD_XYZ, RGBD_RPY, STEREO_XYZ, STEREO_RPY)
    assert np.allclose(pose_matrix(STEREO_XYZ, STEREO_RPY) @ a_to_b,
                       pose_matrix(RGBD_XYZ, RGBD_RPY))


# ---------------------------------------------------------------- reprojecting

def test_an_identity_transform_puts_depth_back_where_it_came_from():
    source, target = _pinhole(), _fisheye(width=64, height=48)
    r = DepthReprojector(source, target, np.eye(4), step=1, scale=1)
    depth = np.full((48, 64), 2000, dtype=np.uint16)      # 2 m everywhere

    out = r.reproject(depth)
    values = out[out > 0]
    assert values.size > 0
    assert np.allclose(values, 2000, atol=2)


def test_a_translation_along_the_optical_axis_changes_the_reading():
    source, target = _pinhole(), _fisheye(width=64, height=48)
    moved = np.eye(4)
    moved[2, 3] = -0.5                       # target half a metre behind
    r = DepthReprojector(source, target, moved, step=1, scale=1)

    out = r.reproject(np.full((48, 64), 2000, dtype=np.uint16))
    values = out[out > 0]
    assert values.size > 0
    assert np.median(values) == pytest.approx(1500, abs=30)


def test_zero_and_out_of_range_depths_are_dropped():
    source, target = _pinhole(), _fisheye(width=64, height=48)
    r = DepthReprojector(source, target, np.eye(4), step=1, scale=1,
                         min_depth_m=0.5, max_depth_m=3.0)
    depth = np.zeros((48, 64), dtype=np.uint16)
    depth[:16] = 0                            # no reading
    depth[16:32] = 100                        # 0.1 m, below the floor
    depth[32:] = 9000                         # 9 m, above the ceiling

    assert np.count_nonzero(r.reproject(depth)) == 0


def test_points_behind_the_target_camera_are_dropped_not_folded_in():
    """A fisheye model will project a point behind the lens to a perfectly
    plausible pixel. Depth from behind the robot must not appear in front."""
    source, target = _pinhole(), _fisheye(width=64, height=48)
    behind = np.eye(4)
    behind[:3, :3] = rpy_to_matrix(0, math.pi, 0)     # target faces backwards
    r = DepthReprojector(source, target, behind, step=1, scale=1)

    assert np.count_nonzero(r.reproject(np.full((48, 64), 2000, np.uint16))) == 0


def test_the_nearest_surface_wins_a_shared_pixel():
    # Two source pixels landing on one output pixel: the floor cares about the
    # closer one, so a mean or a last-write-wins would both be wrong.
    source, target = _pinhole(), _fisheye(width=64, height=48)
    r = DepthReprojector(source, target, np.eye(4), step=1, scale=8)
    depth = np.full((48, 64), 3000, dtype=np.uint16)
    depth[20:28, 20:28] = 1000                # a nearer patch inside one cell

    out = r.reproject(depth)
    cell = out[20 // 8, 20 // 8]
    assert cell == pytest.approx(1000, abs=20)


def test_the_output_keeps_the_millimetre_uint16_contract():
    # So camera.depth_scale and median_depth_m keep working unchanged.
    source, target = _pinhole(), _fisheye(width=64, height=48)
    out = DepthReprojector(source, target, np.eye(4), step=1, scale=1).reproject(
        np.full((48, 64), 2000, dtype=np.uint16))
    assert out.dtype == np.uint16


def test_a_wrongly_sized_depth_image_is_refused():
    source, target = _pinhole(), _fisheye()
    r = DepthReprojector(source, target, np.eye(4))
    with pytest.raises(ValueError, match='expected'):
        r.reproject(np.zeros((10, 10), dtype=np.uint16))


def test_an_empty_depth_image_gives_an_empty_map_rather_than_raising():
    source, target = _pinhole(), _fisheye()
    r = DepthReprojector(source, target, np.eye(4))
    assert np.count_nonzero(r.reproject(np.zeros((48, 64), dtype=np.uint16))) == 0
    assert np.count_nonzero(r.reproject(None)) == 0


def test_the_output_shape_follows_the_target_and_the_scale():
    source, target = _pinhole(), _fisheye(width=128, height=96)
    assert DepthReprojector(source, target, np.eye(4), scale=4).output_shape == (24, 32)
    assert DepthReprojector(source, target, np.eye(4), scale=1).output_shape == (96, 128)


def test_coverage_fraction_counts_pixels_with_a_reading():
    assert coverage_fraction(np.zeros((10, 10), dtype=np.uint16)) == 0.0
    full = np.ones((10, 10), dtype=np.uint16)
    assert coverage_fraction(full) == 1.0
    half = np.zeros((10, 10), dtype=np.uint16)
    half[:5] = 1
    assert coverage_fraction(half) == pytest.approx(0.5)
