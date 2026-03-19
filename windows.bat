@echo off
REM --- Remote Linux Manager Startup (Windows Self-Fixing Launcher) ---
REM
REM ** NOTE : Python MUST be installed to run this tool    **
REM ** Python can be installed from :-                     **
REM ** https://www.python.org/downloads/windows/           **
REM

SET SCRIPT_DIR=%~dp0
SET SCRIPT_NAME=manager.py

CLS
ECHO --- Remote Linux Manager Startup for Windows ---
ECHO.

REM 1. Check if the Python Launcher (py) is available.
py -h >nul 2>&1
IF ERRORLEVEL 1 GOTO PYTHON_NOT_FOUND

REM 2. Check if required packages are installed. We only need to check for one (paramiko).
ECHO [INFO] Checking for required Python packages (paramiko, cryptography)...
REM 'pip show' command returns ERRORLEVEL 0 if found, 1 if not.
py -m pip show paramiko >nul 2>&1

IF ERRORLEVEL 0 (
    ECHO [SUCCESS] Packages are already installed.
    GOTO LAUNCH_APP
)

REM 3. Install packages if not found (ERRORLEVEL was 1)
ECHO [WARNING] Packages not found. Attempting to auto-install: paramiko, cryptography
ECHO.
ECHO --- Starting Package Installation via pip ---
ECHO.

REM Run the installation command
py -m pip install paramiko cryptography

IF ERRORLEVEL 1 (
    ECHO.
    ECHO [FATAL] Installation failed!
    ECHO Please check your internet connection and ensure your user has permission to install packages.
    GOTO APP_FINISHED
)

ECHO [SUCCESS] Packages installed successfully!

:LAUNCH_APP
ECHO.
ECHO [INFO] Launching %SCRIPT_NAME%...
CALL py "%SCRIPT_DIR%%SCRIPT_NAME%"

GOTO APP_FINISHED


:PYTHON_NOT_FOUND
ECHO.
ECHO [FATAL] The Python Launcher ('py' command) was not found. 
ECHO This usually means Python is not installed. 
ECHO Please install Python 3 from www.python.org/downloads/windows/ (and ensure 'Add Python to PATH' is checked).

:APP_FINISHED
ECHO.
ECHO Script finished. Press any key to close this window.
PAUSE > nul
