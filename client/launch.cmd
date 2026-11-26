@echo off
setlocal
pushd "%~dp0.." || exit /b 1
set "desktop_python=build\rtc-env\Scripts\python.exe"
if not exist "%desktop_python%" (
    python -m venv "build\rtc-env" || goto failed
)
"%desktop_python%" -m pip --disable-pip-version-check install --quiet -r "client\requirements.txt" || goto failed
"%desktop_python%" -m "%audimate_module%" %*
set "desktop_exit=%errorlevel%"
popd
exit /b %desktop_exit%

:failed
popd
exit /b 1
