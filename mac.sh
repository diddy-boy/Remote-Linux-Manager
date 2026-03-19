#!/bin/bash
#  ***** use this to launch Manager.py from Mac OS *****
#  ***** NOTE: you will be asked for a password to install dependencies from Xcode and Homebrew on first run *****
#  ***** this script is designed for compatibility across all recent macOS versions (including Big Sur and newer) ******
#
# --- Configuration ---
SCRIPT_DIR="$(dirname "$0")"
VENV_PATH="$SCRIPT_DIR/.venv_manager"
PYTHON_BIN_NAME="python3"
SCRIPT_NAME="manager.py"

clear
echo "--- Remote Manager Startup for macOS ---"
echo ""

# ===============================================
# 0. ENSURE HOMEBREW ENVIRONMENT IS LOADED & Set Prefixes
# ===============================================

# Determine Homebrew prefix path based on architecture (M1/Intel)
if [[ "$(uname -m)" == "arm64" ]]; then
    HOMEBREW_PREFIX="/opt/homebrew"
else
    HOMEBREW_PREFIX="/usr/local"
fi

# Set the ABSOLUTE path to the Homebrew Python executable
PYTHON_BIN_PATH="$HOMEBREW_PREFIX/bin/$PYTHON_BIN_NAME"

if command -v brew &> /dev/null; then
    echo "[INFO] Loading Homebrew environment from prefix: $HOMEBREW_PREFIX"
    # Attempt to load Homebrew path for shell environment update
    eval "$($HOMEBREW_PREFIX/bin/brew shellenv)" 2>/dev/null || true
fi

# ===============================================
# 1. Check/Install Xcode Command Line Tools
# ===============================================
if ! command -v xcode-select &> /dev/null; then
    echo "[INFO] Xcode Command Line Tools are missing."
    echo "[ACTION] Attempting to install Xcode Command Line Tools..."
    xcode-select --install
    echo ""
    echo "[IMPORTANT] Please wait for the Apple dialog box to complete the installation."
    echo "After installation, re-run this script."
    exit 0
fi

# ===============================================
# 2. Check/Install Homebrew
# ===============================================
if ! command -v brew &> /dev/null; then
    echo "[INFO] Homebrew is missing."
    echo "[ACTION] Attempting to install Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    INSTALL_STATUS=$?
    if [ $INSTALL_STATUS -ne 0 ]; then
        echo ""
        echo "[FATAL] Homebrew installation failed. Please check network and permissions."
        exit 1
    fi
    echo "[SUCCESS] Homebrew installed."
    echo ""
    
    echo "[ACTION] Forcing shell environment update..."
    # Reload Homebrew environment after install
    eval "$($HOMEBREW_PREFIX/bin/brew shellenv)" 2>/dev/null || true
fi

# ===============================================
# 3. CRITICAL: Install Dependencies (Tcl/Tk, libffi, and Python 3)
# ===============================================

# 3a. Install modern Tcl/Tk (REQUIRED FOR _tkinter)
if ! brew list tcl-tk &> /dev/null; then
    echo "[ACTION] Installing stable Tcl/Tk for Tkinter compatibility..."
    brew install tcl-tk
fi

# 3b. Install libffi and OpenSSL 3 
if ! brew list libffi &> /dev/null || ! brew list openssl@3 &> /dev/null; then
    echo "[ACTION] Installing libffi and OpenSSL 3 (supporting libraries)..."
    brew install libffi openssl@3 
fi

# 3c. Install Homebrew Python 3 (REVISED TO ENSURE LINKAGE)
if ! brew list python &> /dev/null; then
    echo "[INFO] Python 3 (via Homebrew) is missing. Installing..."
    brew install python 
elif ! "$PYTHON_BIN_PATH" -c "import _tkinter" &> /dev/null; then
    # CRITICAL: If Python exists but the _tkinter module fails to import, force a reinstall
    echo "[CRITICAL] Python found, but _tkinter module is missing. Forcing reinstall to relink Tcl/Tk..."
    brew reinstall python
else
    echo "[INFO] Homebrew Python found and appears correctly linked."
fi

# Final check
if [ ! -f "$PYTHON_BIN_PATH" ]; then
    echo "[FATAL] Homebrew Python could not be found or installed. Exiting."
    exit 1
fi
# ===============================================
# 4. Virtual Environment Setup and Dependency Installation
# ===============================================

# 4a. Force VENV Clean-up and Setup (CRITICAL: Ensures new VENV links to newly compiled Homebrew Python)
if [ ! -d "$VENV_PATH" ]; then
    echo "[INFO] Creating new Python Virtual Environment..."
    "$PYTHON_BIN_PATH" -m venv "$VENV_PATH"
fi

echo "[INFO] Setting up new Python Virtual Environment using: $PYTHON_BIN_PATH"
"$PYTHON_BIN_PATH" -m venv "$VENV_PATH"

# 4b. Define Venv Binaries
VENV_PYTHON="$VENV_PATH/bin/python"
VENV_PIP="$VENV_PATH/bin/pip"

# ===============================================
# 4c. Install Python Dependencies
# ===============================================
echo "[INFO] Checking and installing Python dependencies: paramiko, cryptography..."

# Create a temporary requirements file for pip
cat <<EOF > "$SCRIPT_DIR/temp_requirements.txt"
paramiko
cryptography
tkmacosx
EOF

"$VENV_PIP" install -r "$SCRIPT_DIR/temp_requirements.txt" --disable-pip-version-check
INSTALL_STATUS=$?

# Clean up temp file
rm "$SCRIPT_DIR/temp_requirements.txt"

if [ $INSTALL_STATUS -ne 0 ]; then
    echo "[ERROR] Failed to install Python dependencies! Check pip output above."
    exit 1
fi

# ===============================================
# 5. Launch Application
# ===============================================

# --- CRITICAL FIXES FOR LIBRARY LINKAGE ---

# 1. Tcl/Tk Dynamic Library Path Fix (FORCES _tkinter TO FIND LIBRARIES)
TCLTK_PREFIX=$(brew --prefix tcl-tk)
if [ -d "$TCLTK_PREFIX" ]; then
    # Set the Tcl/Tk library path
    export TCL_LIBRARY="$TCLTK_PREFIX/lib/tcl8.6"
    export TK_LIBRARY="$TCLTK_PREFIX/lib/tk8.6"
    
    # CRITICAL: Set DYLD_LIBRARY_PATH to force the dynamic linker to find libtcl and libtk
    export DYLD_LIBRARY_PATH="$TCLTK_PREFIX/lib:$DYLD_LIBRARY_PATH"
    echo "[INFO] Tcl/Tk Environment Variables Set (including DYLD_LIBRARY_PATH fix)."
    echo "       TCL_LIBRARY: $TCL_LIBRARY"
else
    echo "[FATAL] Could not find tcl-tk installation prefix. Cannot apply GUI fix."
    exit 1
fi

# 2. OpenSSL/LibFFI Fix (for cryptography module linkage)
export HOMEBREW_OPT_PATH="$HOMEBREW_PREFIX/opt"
export LDFLAGS="-L$HOMEBREW_OPT_PATH/openssl@3/lib -L$HOMEBREW_OPT_PATH/libffi/lib"
export CPPFLAGS="-I$HOMEBREW_OPT_PATH/openssl@3/include -I$HOMEBREW_OPT_PATH/libffi/include"
echo "[INFO] Setting LDFLAGS/CPPFLAGS for OpenSSL/LibFFI linking..."

# ------------------------------------------------------------------

echo "[INFO] Dependencies met. Launching $SCRIPT_NAME using $VENV_PYTHON..."
"$VENV_PYTHON" "$SCRIPT_DIR/$SCRIPT_NAME"

# ===============================================
# 6. Exit
# ===============================================
echo ""
echo "Script finished."
exit 0
