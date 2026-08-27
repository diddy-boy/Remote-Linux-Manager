A Linux based tool or windows used to administrate multiple Ubuntu based or Debian derived Linux systems across the same home network.
I was waiting for Zorin to release Zorin Grid so I ended up making my own.

## Detailed Project Functionality

The Remote Linux Manager is a secure, multi-threaded GUI application designed for the mass administration of Linux PCs over SSH.
This tool can be ran from Linux, Mac or Windows to administrate Debian based / derived linux pc's.

“Designed for Linux tinkerers and homelabbers who think Ansible is overkill.”

### Agentless Design

Remote Linux Manager requires no software installation on managed linux systems. If a machine supports SSH, it can be administered without deploying or maintaining remote agents. no extra software to install on these remote pc's.

## Demo Video
[![Demo Video](https://img.youtube.com/vi/0yez1mJtKGY/hqdefault.jpg)](https://www.youtube.com/watch?v=0yez1mJtKGY)

### Centralized PC Management

- **Storage**: Stores the hostname (or IP), username, and password for multiple remote PCs in a local SQLite database (pc_manager.db).
- **Security**: Passwords are not stored in plaintext; they are secured using Fernet symmetric encryption. A unique key (.secret.key) is generated and used to encrypt/decrypt credentials.
- **Interface**: Allows users to easily Add, Edit, and Delete PC entries via dedicated dialogs.
- **Resolution**: Automatically attempts to resolve hostnames by appending .local (e.g., 4pi becomes 4pi.local) to support mDNS/Avahi discovery.

### Live Status and Monitoring

- **Non-Blocking Status Check**: Runs multi-threaded checks to determine if a PC is reachable and responsive via SSH.
- **Update Detection**: Executes a command (apt update followed by apt list --upgradable) to determine the exact number of pending software updates on each machine.
- **Real-Time Logging**: Provides a live activity log that displays connection attempts, command output, errors (Auth/SSH/Timeout), and success messages.
- **Power Cycle Monitoring**: After a Reboot or Shutdown command is initiated, a dedicated monitoring thread polls the PC every 10 seconds to track its status (e.g., "Rebooting (Offline)") and confirms its successful return to service or final offline state.
- Upload a file / script to remote linux pc and can also execute that script file after uploading with sudo password detection.

### Mass Action Execution

- **Multi-Select**: Users can select and execute actions on multiple PCs simultaneously.
- **Secure Command Execution**: Commands requiring root privileges use the sudo -S flag to securely pipe the decrypted password to the remote machine's standard input.
- **Run Update**: Executes a full system update (apt update; apt-get upgrade -y) on the selected machines.
- **Deploy Software**: Prompts the user for a list of packages and runs the corresponding installation command (apt-get install -y) on selected machines.
- **Reboot/Shutdown**: Runs non-blocking power control commands (sudo -S reboot now & or sudo -S shutdown now &) followed by immediate monitoring.

Status bar reports live fleet status — including online/offline counts, pending updates, fleet health, disk space and uptime — refreshed on launch, on manual refresh, and automatically whenever a previously offline PC comes back online, rather than polling on a fixed timer.

### Clone PC

Ability to Clone a pc from a snapshot to a target pc.

- Uses a snapshot from a pc in the list view (that a user can select) and will clone the same software onto a selected pc.
- Will also create users and groups also but new users will not have a password set until a user logs in on the target pc.
- Very easy way to take a new pc just added to the network up to a configured pc (with a snapshot) in one hit.
- No disk partition, no harddrive cloning, just software, users and groups.

The Clone PC feature intentionally does NOT copy home directories, configuration files, services, PPAs, or third‑party software. This avoids hardware-specific breakage, security issues, and unintended side effects, while still delivering a system that is functionally equivalent for further customisation.

### Create Snapshot and Revert to Snapshot

If a user selects a pc from the list and then selects to create a snapshot, an apt --list is performed and stored in the local database against that pc. Then at a later date the user selects the same pc and selects revert to snapshot, the apt --list is performed again but then a diff is performed to generate the sudo apt remove list. This will then revert the pc back to its state when the snapshot was performed regardless of software versions.

### Automatic Drift Detection

Once a PC has a snapshot, the tool automatically compares its currently-installed software against that snapshot the first time it's checked each session (and at most once every 30 minutes thereafter). If anything's changed — packages added or missing versus the snapshot — it's flagged in the activity log so you can decide whether to Revert to Snapshot or take a new one as the updated baseline. This is purely informational; nothing is installed or removed automatically.

## Technologies and Libraries Used

| Category | Technology | Purpose |
|---|---|---|
| GUI Framework | tkinter | (Mac OS has its own but we cater for that too) The main module for creating the Graphical User Interface (GUI), including the root window, frames, and basic widgets. |
| Programming Language | Python 3 | The core logic of the application. |
| Remote Access | Paramiko | The essential Python library for establishing secure and stable SSH connections and executing remote commands. |
| Data Persistence | SQLite 3 | Used for the local, embedded database to store PC configuration details. |
| Data Security | Cryptography (Fernet) | Used to generate the encryption key and securely encrypt/decrypt sensitive SSH passwords before saving them to the database. |
| Concurrency | Python's threading | Manages simultaneous connections, status checks, and monitoring loops to keep the main GUI responsive. |
| Startup & Setup | Shell Script (manage-pcs.sh) | Handles the essential pre-flight checks: ensures Python/GTK dependencies are installed, creates a virtual environment (venv), and installs required Python packages (paramiko, cryptography) for a simple start. |
| Configuration | CSS Styling | Uses a custom CSS provider to apply the distinctive Nord color palette and ensure a clean, modern look. |

## Requirements for running under Windows

Updated and moved away from gtk library so the only requirement is now python on Linux or Windows.

The Windows.bat file now checks for python and installs any extra software on first run up. Same for Linux.sh as well.

Added support for running from a Mac OS system. just run mac.sh to run the program.

Should now work on Windows, Linux, Chrome OS (using linux subsystem) and Mac OS.

Just unzip the attached file and this will make a Manage-PC directory. Within this folder run up the linux.sh script if running from Linux or window.bat if on windows or Mac.sh if running from Mac OS.

You may get prompted for sudo password on first run up to install the supporting files (python 3, pip etc) but subsequent runs wont ask you for sudo password and the app will just run up.

## Backup & Migration

If you want to move this tool to another pc, keeping the database and secret hash key:

1. Backup these files from your installation directory along with linux.sh, manager.py and windows.bat:
   - `pc_manager.db` (your PC list)
   - `.secret.key` (required to decrypt passwords)
   - **Important**: These files contain sensitive data!
2. On the new machine, run the launcher once to create the folder structure.
3. Replace the generated `.secret.key` and `pc_manager.db` with your backups.
4. Your PC list will be restored with all credentials intact.

### 📥 One-Click Download

[**Click here to download the project as a ZIP file**](https://github.com/diddy-boy/Remote-Linux-Manager/archive/refs/heads/main.zip)

1. Unzip the folder.
2. Run **`windows.bat`** (Windows), **`linux.sh`** (Linux), or **`mac.sh`** (macOS).
