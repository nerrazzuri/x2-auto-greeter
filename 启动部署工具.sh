#!/usr/bin/env bash
# Double-click this to open the X2 deployer.
#
# Kept at the top of the repository and named in Chinese on purpose: the person
# opening it is a customer looking at a file manager, not an engineer looking
# for tools/deployer/gui/app.py.
#
# Runs from source rather than a packaged binary, so the laptop needs Python.
# It says which piece is missing rather than closing on a traceback -- a window
# that flashes and disappears is the worst thing this could do to someone who
# has no terminal to read.
cd "$(dirname "$0")" || exit 1

PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  MESSAGE="没有找到 Python。请先安装 Python 3.10 或更新版本,然后再打开本工具。"
  command -v zenity >/dev/null && zenity --error --text="$MESSAGE" 2>/dev/null
  echo "$MESSAGE" >&2
  read -r -p "按回车关闭…" _
  exit 1
fi

MISSING=$("$PY" - <<'EOF'
missing = []
for module, package in (('PyQt6', 'PyQt6'), ('paramiko', 'paramiko'), ('yaml', 'PyYAML')):
    try:
        __import__(module)
    except ImportError:
        missing.append(package)
print(' '.join(missing))
EOF
)

if [ -n "$MISSING" ]; then
  echo "还缺这些组件:$MISSING"
  echo "正在安装…"
  "$PY" -m pip install --user $MISSING || {
    echo "自动安装失败。请手动执行:$PY -m pip install --user $MISSING" >&2
    read -r -p "按回车关闭…" _
    exit 1
  }
fi

exec "$PY" tools/deployer/gui/app.py
