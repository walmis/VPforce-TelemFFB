@echo off
setlocal EnableExtensions

rem ---------------------------------------------------------------------------
rem  Installs (or updates) TelemFFB into %LOCALAPPDATA%\Programs\TelemFFB and
rem  adds a Start Menu shortcut. Run it from the extracted release folder, next
rem  to VPforce-TelemFFB.exe. User settings live in %LOCALAPPDATA%\VPForce-TelemFFB
rem  and are not touched.
rem ---------------------------------------------------------------------------

set "EXE_NAME=VPforce-TelemFFB.exe"
set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"
set "DEST=%LOCALAPPDATA%\Programs\TelemFFB"
set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\VPforce TelemFFB.lnk"

title Install TelemFFB

if not exist "%SRC%\%EXE_NAME%" (
    echo.
    echo  %EXE_NAME% was not found next to this script.
    echo.
    echo  Extract the whole .zip file first ^(right-click it, "Extract All..."^),
    echo  then run this script from the extracted folder.
    echo.
    pause
    exit /b 1
)
if not exist "%SRC%\assets\" (
    echo.
    echo  The "assets" folder was not found next to %EXE_NAME%.
    echo  Extract the whole .zip file first, then run this script again.
    echo.
    pause
    exit /b 1
)

rem Installing onto itself would delete the files being copied
if /i "%SRC%"=="%DEST%" (
    echo.
    echo  This copy is already installed in %DEST%.
    echo.
    pause
    exit /b 0
)

:check_running
tasklist /FI "IMAGENAME eq %EXE_NAME%" /NH 2>nul | find /i "%EXE_NAME%" >nul
if not errorlevel 1 (
    echo.
    echo  TelemFFB is running. Close all TelemFFB windows ^(including the tray
    echo  icon^), then press any key to continue.
    pause >nul
    goto check_running
)

echo.
echo  Installing TelemFFB to:
echo    %DEST%
echo.

if not exist "%DEST%\" (
    mkdir "%DEST%" || goto fail
)

rem assets is mirrored so files left over from an older version are removed
robocopy "%SRC%\assets" "%DEST%\assets" /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto fail

copy /Y "%SRC%\%EXE_NAME%" "%DEST%\" >nul || goto fail
if exist "%SRC%\_RELEASE_NOTES.txt" (
    copy /Y "%SRC%\_RELEASE_NOTES.txt" "%DEST%\" >nul || goto fail
)

rem Clear the "downloaded from the internet" mark so Windows does not warn on
rem every launch, then create the Start Menu shortcut.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Get-ChildItem -LiteralPath $env:DEST -Recurse -File | Unblock-File -ErrorAction SilentlyContinue;" ^
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SHORTCUT);" ^
    "$s.TargetPath = Join-Path $env:DEST $env:EXE_NAME;" ^
    "$s.WorkingDirectory = $env:DEST;" ^
    "$s.IconLocation = (Join-Path $env:DEST $env:EXE_NAME) + ',0';" ^
    "$s.Description = 'VPforce TelemFFB';" ^
    "$s.Save()"
if errorlevel 1 (
    echo  Warning: the Start Menu shortcut could not be created.
) else (
    echo  Start Menu shortcut created: "VPforce TelemFFB"
)

echo.
echo  Done. TelemFFB is installed in %DEST%
echo.
echo  To pin it: open Start, find "VPforce TelemFFB" under All apps,
echo  right-click it and choose "Pin to Start".
echo  ^(Windows does not allow programs to pin themselves.^)
echo.
choice /C YN /N /M " Open the install folder now? [Y/N] "
if errorlevel 2 goto end
start "" explorer "%DEST%"
goto end

:fail
echo.
echo  Installation failed. Make sure TelemFFB is closed and try again.
echo.
pause
exit /b 1

:end
endlocal
exit /b 0
