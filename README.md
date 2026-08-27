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

Table of Contents
Overview
Features at a Glance
Installation
Centralized PC Management
Live Status and Monitoring
Mass Action Execution
Wake-on-LAN
Clone PC
Snapshots and Drift Detection
Technologies Used
Backup and Migration
Overview

The Remote Linux Manager is a secure, multi-threaded GUI application designed for the mass administration of Linux PCs over SSH. It runs on Linux, Mac, or Windows to administrate Debian-based/derived Linux PCs.

Agentless Design

Remote Linux Manager requires no software installation on managed Linux systems. If a machine supports SSH, it can be administered without deploying or maintaining remote agents — no extra software to install on the remote PCs.

Features at a Glance
🖥️ Centralized management of your whole home fleet from one local SQLite database
🔍 Background network scan with autocomplete when adding a new PC
🔐 Encrypted credential storage (Fernet symmetric encryption)
📊 Live status, uptime, disk space, and pending-update tracking
⚙️ Mass command execution, updates, and software deployment across multiple PCs at once
⚡ Wake-on-LAN — sleeping PCs wake themselves up automatically before an action runs
🧬 Clone PC — bring a freshly added machine up to a known-good baseline in one click
📸 Snapshot, revert, and automatic drift detection for installed packages
📁 Upload and optionally execute files/scripts remotely, with sudo password handling
Installation

Requires only Python — no GTK dependency.

Download the project as a ZIP and unzip it. This creates a Manage-PC directory.
Run the launcher for your platform from inside that folder:
bash
   # Linux
   ./linux.sh
bash
   # macOS
   ./mac.sh

   # Windows
   windows.bat

Each launcher checks for Python and installs any required packages (paramiko, cryptography) automatically on first run — you may be prompted for your sudo password once during that initial setup, but not on subsequent runs.

Works on Windows, Linux, Chrome OS (via the Linux subsystem), and macOS.

Centralized PC Management
Storage — Stores the hostname (or IP), username, password, and MAC address for multiple remote PCs in a local SQLite database (pc_manager.db).
Security — Passwords are never stored in plaintext; they're secured using Fernet symmetric encryption. A unique key (.secret.key) is generated and used to encrypt/decrypt credentials.
Interface — Add, edit, and delete PC entries via dedicated dialogs.
Smart Add PC — When adding a new PC, the tool scans the local subnet in the background for SSH-reachable devices and offers them as autocomplete suggestions as you type a hostname or IP. Free-text entry always still works for anything the scan doesn't find. Picking a discovered device also silently captures its MAC address at the same time, so it's Wake-on-LAN ready immediately — no extra field to fill in.
Hostname Resolution — Automatically attempts to resolve hostnames by appending .local (e.g., 4pi becomes 4pi.local) to support mDNS/Avahi discovery.
Automatic MAC Capture — For PCs added manually (not via the scan), the tool reads the MAC address of the machine's active network interface the first time it successfully connects, and stores it against that PC — no manual entry required.
Live Status and Monitoring
Non-Blocking Status Check — Multi-threaded checks determine if a PC is reachable and responsive via SSH.
Update Detection — Runs apt update followed by apt list --upgradable to determine the exact number of pending software updates on each machine.
Real-Time Logging — A live activity log displays connection attempts, command output, errors (Auth/SSH/Timeout), and success messages.
Power Cycle Monitoring — After a Reboot or Shutdown command is initiated, a dedicated monitoring thread polls the PC every 10 seconds to track its status (e.g., "Rebooting (Offline)") and confirms its successful return to service or final offline state.
File Transfer — Upload a file/script to a remote Linux PC, with the option to execute it after uploading and automatic sudo password handling.

Status bar reports live fleet status — including online/offline counts, pending updates, fleet health, disk space, and uptime — refreshed on launch, on manual refresh, and automatically whenever a previously offline PC comes back online, rather than polling on a fixed timer.

Mass Action Execution
Multi-Select — Select and execute actions on multiple PCs simultaneously.
Secure Command Execution — Commands requiring root privileges use the sudo -S flag to securely pipe the decrypted password to the remote machine's standard input.
Run Update — Executes a full system update (apt update && apt-get upgrade -y) on the selected machines.
Deploy Software — Prompts for a list of packages and runs the corresponding install command (apt-get install -y) on selected machines.
Reboot/Shutdown — Runs non-blocking power control commands (sudo -S reboot now & / sudo -S shutdown now &) followed by immediate monitoring.
Wake-on-LAN

Half the point of a home fleet is machines that sleep to save power, so the tool treats "unreachable" and "asleep" as different things whenever it can tell them apart.

Manual Wake — Select one or more PCs and send a Wake-on-LAN magic packet directly from the toolbar, useful for waking a machine before you sit down to work on it.
Wake-before-action — Before running any command, update, deploy, or file transfer, the tool does a quick port check first. If a PC doesn't answer but has a MAC address on file, it sends a magic packet and waits (up to ~35 seconds) for the PC to boot before proceeding, so a single click on a sleeping PC still just works instead of failing with "offline."
Non-blocking during fleet-wide sweeps — A full status check across the whole fleet doesn't stop and wait for a sleeping PC; it fires the wake packet and moves straight on, so one sleeping machine can never stall the check for the rest of your fleet. That PC simply reports Offline for that one pass and shows as OK on the next check once it's finished booting.
Self-healing via the background watchdog — The 30-second connectivity watchdog nudges sleeping PCs on its own: if it finds a PC unreachable and it has a MAC on file, it sends a wake packet in the background. Combined with the watchdog's existing auto-recovery (which triggers a full re-check the moment a previously offline PC answers again), a sleeping PC can come back online and get fully rechecked with zero manual action — typically within one or two watchdog cycles.
Gentle retry, not spam — Wake attempts for a given PC are throttled to roughly once a minute, so a genuinely offline PC (unplugged, not WoL-capable, etc.) doesn't get flooded with magic packets — it's retried at a low, harmless cadence indefinitely rather than given up on.

Requirements: the target PC needs Wake-on-LAN enabled in BIOS/UEFI and at the OS/NIC level, and needs to be connected via Ethernet — WoL over Wi-Fi is unreliable or unsupported on most hardware. No extra software or agent is needed on the managed PC; this uses the same agentless SSH-only design as the rest of the tool.

Clone PC

Clone a PC from a snapshot to a target PC.

Uses a snapshot from a PC in the list view (selected by the user) and installs the same software onto a selected target.
Also creates the same users and groups, though new users won't have a password set until they log in on the target PC.
A very easy way to bring a newly added PC up to a configured baseline (with a snapshot) in one hit.
No disk partitioning, no hard-drive cloning — just software, users, and groups.

The Clone PC feature intentionally does not copy home directories, configuration files, services, PPAs, or third-party software. This avoids hardware-specific breakage, security issues, and unintended side effects, while still delivering a system that's functionally equivalent for further customisation.

Snapshots and Drift Detection
Create Snapshot / Revert to Snapshot

Selecting a PC and choosing Create Snapshot performs an apt --list and stores it in the local database against that PC. Later, selecting the same PC and choosing Revert to Snapshot performs the apt --list again, diffs it against the saved snapshot, and generates a sudo apt remove list — reverting the PC back to its state at snapshot time, regardless of software versions.

Automatic Drift Detection

Once a PC has a snapshot, the tool automatically compares its currently installed software against that snapshot the first time it's checked each session (and at most once every 30 minutes thereafter). If anything's changed — packages added or missing versus the snapshot — it's flagged in the activity log so you can decide whether to revert or take a new baseline snapshot. This is purely informational; nothing is installed or removed automatically.

Technologies Used
Category	Technology	Purpose
GUI Framework	tkinter	Creates the GUI — root window, frames, and basic widgets (macOS has its own equivalent, catered for too).
Programming Language	Python 3	The core logic of the application.
Remote Access	Paramiko	Establishes secure, stable SSH connections and executes remote commands.
Data Persistence	SQLite 3	Local, embedded database for storing PC configuration details.
Data Security	Cryptography (Fernet)	Generates the encryption key and encrypts/decrypts sensitive SSH passwords before saving them.
Concurrency	Python threading	Manages simultaneous connections, status checks, and monitoring loops to keep the GUI responsive.
Networking	Python socket / struct / ipaddress (standard library)	Local subnet discovery, SSH-port scanning, and constructing/broadcasting Wake-on-LAN magic packets — no extra dependencies required.
Startup & Setup	Shell script (manage-pcs.sh)	Pre-flight checks: ensures Python dependencies are installed, creates a virtual environment, and installs required packages for a simple start.
Styling	Custom CSS provider	Applies the distinctive Nord colour palette for a clean, modern look.
Backup and Migration

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
