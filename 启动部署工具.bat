@echo off
rem Double-click this to open the X2 deployer on Windows.
rem
rem Runs from source, so the laptop needs Python. It installs what is missing
rem and pauses on every failure: a console window that flashes and disappears
rem tells a customer nothing, and there is no terminal for them to read.
setlocal
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (where python >nul 2>nul && set PY=python)

if not defined PY (
  echo.
  echo 没有找到 Python。
  echo 请先从 https://www.python.org/downloads/ 安装 Python 3.10 或更新版本,
  echo 安装时请勾选 "Add Python to PATH",然后重新打开本工具。
  echo.
  pause
  exit /b 1
)

%PY% -c "import PyQt6, paramiko, yaml" >nul 2>nul
if errorlevel 1 (
  echo 正在安装所需组件,第一次打开需要几分钟…
  %PY% -m pip install --user PyQt6 paramiko PyYAML
  if errorlevel 1 (
    echo.
    echo 组件安装失败。请确认这台电脑能上网,然后重试。
    pause
    exit /b 1
  )
)

%PY% tools\deployer\gui\app.py
if errorlevel 1 (
  echo.
  echo 部署工具异常退出,上面是错误信息。
  pause
)
endlocal
