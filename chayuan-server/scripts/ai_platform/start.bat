@echo off
setlocal
pushd "%~dp0..\"
if exist "config\default.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("config\default.env") do (
        if not "%%a"=="" set "%%a=%%b"
    )
)
set "PY=%CHAYUAN_PYTHON%"
if "%PY%"=="" set "PY=python"
"%PY%" -m chayuan_supervisor up --foreground %*
popd
endlocal
