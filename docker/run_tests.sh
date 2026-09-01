#!/usr/bin/env bash
# Build aimdk_msgs + x2_greeter inside the container and run the ROS tests.
# Extra arguments are passed straight through to pytest.
set -euo pipefail

REPO=${REPO:-/repo}
WS=${WS:-/ws}
AIMDK_SRC="$REPO/sdk/aimdk-aarch64-a424add7-artifacts/src/aimdk_msgs"

# The ROS setup scripts reference variables (e.g. AMENT_TRACE_SETUP_FILES)
# that are only conditionally set, which trips `set -u`. Relax nounset only
# around sourcing them.
set +u
source /opt/ros/humble/setup.bash
set -u

mkdir -p "$WS/src"

# aimdk_msgs never changes and takes several minutes to generate, so it is
# copied once and left alone. It is copied rather than mounted because its
# install step writes a prebuilt cache back into its own source tree.
if [ ! -d "$WS/src/aimdk_msgs" ]; then
  echo "== copying aimdk_msgs from the vendor archive"
  cp -r "$AIMDK_SRC" "$WS/src/aimdk_msgs"
fi

echo "== syncing x2_greeter"
rm -rf "$WS/src/x2_greeter"
cp -r "$REPO/x2_greeter_ws/src/x2_greeter" "$WS/src/x2_greeter"
cp "$REPO/pyproject.toml" "$WS/pyproject.toml"

cd "$WS"
echo "== colcon build"
colcon build --packages-select aimdk_msgs x2_greeter \
             --cmake-args -DCMAKE_BUILD_TYPE=Release

set +u
source "$WS/install/setup.bash"
set -u

echo "== pytest -m ros"
# ROS Humble ships pytest plugins from launch_testing and launch_testing_ros
# (registered under the entry-point names launch_testing and launch_ros)
# built against pre-pytest-8 hookspecs; they auto-load via setuptools entry
# points and crash pytest>=8 during collection. We use none of their
# fixtures, so disable both explicitly rather than downgrading pytest (which
# would make the container's pytest diverge from the host's).
python3 -m pytest src/x2_greeter/test -m ros -v -p no:cacheprovider -p no:launch_testing -p no:launch_ros "$@"

# test_audio_tooling.py needs no ROS, but it locates tools/make_greeting_audio.py
# by climbing four parents from its own file path, which only lands on the
# repo root when the test runs from its original tree (REPO, bind-mounted),
# not from the copy synced into WS above. Run it separately, from REPO, so
# that path resolution — and the pyproject.toml pythonpath it relies on to
# import x2_greeter — both work the same way they do on the host.
echo "== pytest (non-ROS audio tooling, from $REPO)"
(cd "$REPO" && python3 -m pytest x2_greeter_ws/src/x2_greeter/test/test_audio_tooling.py -v -p no:cacheprovider -p no:launch_testing -p no:launch_ros)
