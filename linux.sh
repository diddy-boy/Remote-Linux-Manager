#!/bin/bash

# ****** Universal Linux/Chromebook Launcher (Ubuntu/Debian & Arch) *****
#
# Define the absolute path to the script's directory for robust execution
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# --- Configuration (using absolute paths where possible) ---
VENV_PATH="$SCRIPT_DIR/.venv_pcmanager"
PYTHON_BIN="python3"
SCRIPT_NAME="manager.py"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

clear
echo "--- Remote Linux Manager Startup (Multi-Distro Support) ---"

# --- 1. Distribution and Package Manager Setup ---
PKG_MANAGER=""
REQUIRED_PACKAGES=()
DISTRO_ID=""

if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO_ID=$ID
fi

case "$DISTRO_ID" in
    debian|ubuntu|pop|mint|zorin|elementary|deepin)
        echo "[INFO] Detected Debian/Ubuntu-based distribution (including Zorin OS)."
        PKG_MANAGER="sudo apt-get install -y"
        # Packages for Debian/Ubuntu
        REQUIRED_PACKAGES=("python3" "python3-venv" "python3-gi" "python3-tk")
        # Function to check for packages on Debian/Ubuntu
        check_packages() {
            local MISSING=()
            for PKG in "${REQUIRED_PACKAGES[@]}"; do
                if ! dpkg-query -W -f='${Status}' "$PKG" 2>/dev/null | grep -q "install ok installed"; then
                    MISSING+=("$PKG")
                fi
            done
            echo "${MISSING[@]}"
        }
        ;;
    arch|manjaro|endeavouros|garuda)
        echo "[INFO] Detected Arch-based distribution."
        PKG_MANAGER="sudo pacman --noconfirm -S"
        # Packages for Arch (python-gobject for PyGObject, tk for tkinter/python-tk)
        # FIX APPLIED: Removed 'python-venv' as venv is part of the 'python' package on Arch.
        REQUIRED_PACKAGES=("python" "python-pip" "python-gobject" "tk")
        # Function to check for packages on Arch
        check_packages() {
            local MISSING=()
            for PKG in "${REQUIRED_PACKAGES[@]}"; do
                # Check installed package list, ignoring foreign packages which might clutter
                if ! pacman -Q "$PKG" &> /dev/null; then
                    MISSING+=("$PKG")
                fi
            done
            echo "${MISSING[@]}"
        }
        ;;
    *)
        echo ""
        echo "[FATAL] Unsupported distribution detected: '$DISTRO_ID'."
        echo "This script supports Debian/Ubuntu-based and Arch-based systems."
        exit 1
        ;;
esac

# --- 2. System Dependency Check and Installation ---
echo "[INFO] Checking for essential system dependencies..."

if ! command -v ${PKG_MANAGER%% *} &> /dev/null; then
    echo ""
    echo "[FATAL] The package manager command was not found."
    echo "Please ensure the system is properly configured."
    exit 1
fi

# Get missing packages using the distro-specific function
MISSING_PACKAGES=($(check_packages))

if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    echo ""
    echo "=================================================================="
    echo "  [ACTION REQUIRED] Missing Critical System Packages Detected!"
    echo "  The following packages are required to run the GUI:"
    echo "  -> ${MISSING_PACKAGES[@]}"
    echo "=================================================================="
    echo "The script will now attempt to install these using '$PKG_MANAGER'."
    echo "You may be prompted for your sudo password."

    # Pre-installation update for Ubuntu/Debian/Zorin
    if [[ "$DISTRO_ID" == "debian" || "$DISTRO_ID" == "ubuntu" || "$DISTRO_ID" == "pop" || "$DISTRO_ID" == "mint" || "$DISTRO_ID" == "zorin" ]]; then
        sudo apt-get update
    fi

    # Installation
    if $PKG_MANAGER "${MISSING_PACKAGES[@]}"; then
        echo "[SUCCESS] Required system packages installed."
    else
        echo "[FATAL] Failed to install required system packages."
        echo "Please check the error above and resolve installation issues."
        exit 1
    fi
fi
# --- End Dependency Check and Installation ---

# 3. Check for basic Python availability (Final check after installation attempt)
if ! command -v $PYTHON_BIN &> /dev/null
then
    echo "[FATAL] Python 3 is still not installed or not in PATH. Cannot continue."
    exit 1
fi

# 4. Virtual Environment Setup
if [ ! -d "$VENV_PATH" ]; then
    echo "[INFO] Setting up new virtual environment..."
    # Create the virtual environment without --system-site-packages for stability.
    # The application should still be able to access global packages like python-gobject.
    $PYTHON_BIN -m venv "$VENV_PATH"
fi

# 5. Define Venv Binaries Explicitly
VENV_PYTHON="$VENV_PATH/bin/python"
VENV_PIP="$VENV_PATH/bin/pip"

# 6. Guaranteed requirements.txt creation (Ensures the file exists)
if [ ! -f "$REQUIREMENTS_FILE" ]; then
    echo "[INFO] Creating missing $REQUIREMENTS_FILE..."
    cat <<EOF > "$REQUIREMENTS_FILE"
paramiko
cryptography
EOF
fi

# 7. Install Python Dependencies inside venv (CRITICAL FIX: --break-system-packages)
echo "[INFO] Checking Python package dependencies inside venv..."

# Use the --break-system-packages flag for modern Python installs.
$VENV_PIP install -r "$REQUIREMENTS_FILE" --disable-pip-version-check --break-system-packages
INSTALL_STATUS=$?

if [ $INSTALL_STATUS -ne 0 ]; then
    echo "[ERROR] Failed to install Python dependencies. Please check network connection and PyPi status."
    exit 1
fi

# 8. Launch Application and wait for it to finish gracefully.
echo "[INFO] All dependencies met. Launching $SCRIPT_NAME..."

# Use the VENV_PYTHON path to launch the script
exec $VENV_PYTHON "$SCRIPT_NAME"

echo "--- Remote Linux Manager Finished ---"
exit 0
