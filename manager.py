#!/usr/bin/env python3
import webbrowser
import warnings
import re
import sys
import sqlite3
import threading
import os
import socket
import struct
import ipaddress
import subprocess
import paramiko
import time
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from tkinter import font as tkfont
from tkinter import filedialog
from cryptography.fernet import Fernet
from datetime import datetime

# --- Cross-Platform Button Logic ---
if sys.platform == "darwin":  # "darwin" is the internal name for macOS
    try:
        from tkmacosx import Button as AdaptiveButton
        print("Using tkmacosx for color support")
    except ImportError:
        # Fallback if the user hasn't installed tkmacosx yet
        AdaptiveButton = tk.Button
        print("tkmacosx not found, falling back to standard buttons")
else:
    # Windows and Linux use standard buttons (which already support colors)
    AdaptiveButton = tk.Button

# --- Redesigned Styling Configuration ---
# Main surfaces
BG_PRIMARY   = '#161718'        # Near-black main background
BG_SECONDARY = '#1f2022'        # Sidebar / topbar surface
BG_TERTIARY  = '#28292b'        # Card / panel surface
BG_LOG       = '#111213'        # Console background

# Text
FG_PRIMARY   = '#e2e3e5'        # Primary text
FG_SECONDARY = '#8a8d91'        # Muted / label text
FG_TERTIARY  = '#555860'        # Very muted (timestamps)

# Accents
ACCENT_BLUE  = '#4a9eff'        # Selection / info blue
ACCENT_GREEN = '#3ecf8e'        # Success / online green
ACCENT_RED   = '#f56565'        # Danger / offline red
ACCENT_AMBER = '#f6ad55'        # Warning / amber

BORDER_COLOR = '#2e3033'        # Subtle border

# Sidebar
SB_BG        = '#111213'
SB_ICON_ACTIVE_BG = '#1e3a5a'
SB_ICON_ACTIVE_FG = '#4a9eff'
SB_ICON_FG   = '#555860'

# Toolbar button palette  (text-button style — no big coloured fills)
BTN_PRIMARY       = '#1a3a5c'
BTN_PRIMARY_HOVER = '#214972'
BTN_PRIMARY_FG    = '#4a9eff'

BTN_SUCCESS       = '#1a3a2a'
BTN_SUCCESS_HOVER = '#1f4a34'
BTN_SUCCESS_FG    = '#3ecf8e'

BTN_DANGER        = '#3a1a1a'
BTN_DANGER_HOVER  = '#4a2020'
BTN_DANGER_FG     = '#f56565'

BTN_WARNING       = '#3a2e1a'
BTN_WARNING_HOVER = '#4a3b20'
BTN_WARNING_FG    = '#f6ad55'

BTN_INFO          = '#2a1f3a'
BTN_INFO_HOVER    = '#352745'
BTN_INFO_FG       = '#a78bfa'

# Legacy aliases kept so dialogs/SSH code compile unchanged
BTN_PRIMARY_HOVER = BTN_PRIMARY_HOVER  # noqa: already set
BTN_SUCCESS_HOVER = BTN_SUCCESS_HOVER  # noqa

# PC Log Color Palette (High contrast, professional neon accents)
PC_COLOR_PALETTE = [
    '#00d4ff',  # Cyan
    '#00ff88',  # Mint green
    '#ff9d00',  # Amber
    '#00ffc8',  # Aqua
    '#a770ff',  # Purple
    '#ffb800',  # Gold
    '#ff4d94',  # Hot pink
    '#4da6ff',  # Sky blue
    '#80ff80',  # Light green
    '#ffa64d',  # Orange
    '#cc99ff',  # Lavender
    '#00e5ff',  # Bright cyan
    '#ffff66',  # Yellow
    '#66ffcc',  # Teal
    '#ff8080',  # Coral
    '#8cd1ff',  # Baby blue
    '#ffcc80',  # Peach
    '#b3ff99',  # Lime
    '#ff99cc',  # Rose
    '#ff6b9d',  # Pink
]

# --- Warning Suppression ---
warnings.filterwarnings("ignore", category=DeprecationWarning)

# --- Global Config ---
APP_NAME = "Remote Linux Manager - V2"
APP_URL  = "https://hackaday.io/project/204282-remote-linux-manager"   # ← update to your actual Hackaday URL
DB_NAME = "pc_manager.db"
KEY_FILE = ".secret.key"
SSH_CONNECT_TIMEOUT = 10
MONITOR_POLL_INTERVAL_SECONDS = 10

# --- Wake-on-LAN-before-connect tuning ---
# When a PC doesn't answer on the SSH port but has a MAC address on file,
# we treat it as "asleep, not offline": send a magic packet and give it a
# window to boot before falling back to the normal Offline result.
WOL_PORT_CHECK_TIMEOUT = 1.5     # quick probe to see if the PC is already up
WOL_BOOT_WAIT_SECONDS = 35       # how long to wait for a woken PC to answer SSH
WOL_POLL_INTERVAL_SECONDS = 3    # how often to re-check the port while waiting
WOL_RETRY_COOLDOWN_SECONDS = 60  # don't re-send a magic packet more than once per this window per PC

# --- Encryption Utility ---
class EncryptionUtility:
    def __init__(self, key_file=KEY_FILE):
        self.key_file = key_file
        self._ensure_key()
        self.fernet = Fernet(self.key)

    def _ensure_key(self):
        if os.path.exists(self.key_file):
            with open(self.key_file, "rb") as f:
                self.key = f.read()
            print("[INFO] Encryption key loaded.")
        else:
            self.key = Fernet.generate_key()
            with open(self.key_file, "wb") as f:
                f.write(self.key)
            print("[INFO] New encryption key generated and saved.")

    def encrypt(self, data):
        return self.fernet.encrypt(data.encode())

    def decrypt(self, token):
        try:
            return self.fernet.decrypt(token).decode()
        except Exception as e:
            print(f"[ERROR] Decryption failed: {e}", file=sys.stderr)
            return None


# --- Wake-on-LAN Utility ---
def is_tcp_port_open(host, port=22, timeout=1.5):
    """Quick, lightweight reachability probe — plain TCP connect, no SSH
    handshake/auth. Used to decide 'asleep vs genuinely offline' without the
    cost of a full paramiko connection attempt."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def send_magic_packet(mac_address, broadcast_ip='255.255.255.255', port=9):
    """
    Builds and broadcasts a Wake-on-LAN magic packet for the given MAC address.
    Returns (success: bool, message: str). Requires the target machine to have
    WoL enabled in BIOS/UEFI and at the OS/NIC level (and to be on Ethernet —
    WoL over Wi-Fi is unreliable/unsupported on most hardware).
    """
    try:
        clean_mac = re.sub(r'[^0-9a-fA-F]', '', mac_address)
        if len(clean_mac) != 12:
            return False, f"Invalid MAC address: '{mac_address}'"

        mac_bytes = bytes.fromhex(clean_mac)
        magic_packet = b'\xff' * 6 + mac_bytes * 16

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic_packet, (broadcast_ip, port))
        sock.close()
        return True, "Magic packet sent"
    except Exception as e:
        return False, str(e)


# --- Network Discovery Utility ---
def get_local_subnet_hosts():
    """
    Best-effort discovery of the local /24 subnet's usable host addresses,
    based on the IP the OS would use to reach the internet (no packets are
    actually sent for this part — it's just how the OS picks a local route).
    Returns a list of ipaddress.IPv4Address objects, or [] if it can't tell.
    """
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0)
        probe.connect(('10.255.255.255', 1))
        local_ip = probe.getsockname()[0]
        probe.close()
        network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
        return list(network.hosts())
    except Exception:
        return []


def scan_for_ssh_hosts(hosts, port=22, timeout=0.4, max_workers=60, progress_callback=None):
    """
    Threaded TCP-connect scan across the given list of IPv4Address objects,
    checking whether `port` (default 22/SSH) is open. Also attempts a reverse
    DNS / mDNS lookup for a friendly hostname where possible.

    Returns a list of dicts: [{'ip': '192.168.1.42', 'hostname': 'pi4' or None}, ...]
    sorted by IP. Intended to be called from a background thread — this
    function blocks until the scan completes.
    """
    found = []
    found_lock = threading.Lock()
    sem = threading.Semaphore(max_workers)

    def _probe(ip_obj):
        ip_str = str(ip_obj)
        with sem:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(timeout)
                    if s.connect_ex((ip_str, port)) == 0:
                        hostname = None
                        try:
                            hostname = socket.gethostbyaddr(ip_str)[0].split('.')[0]
                        except Exception:
                            pass
                        with found_lock:
                            found.append({'ip': ip_str, 'hostname': hostname})
            except Exception:
                pass
            finally:
                if progress_callback:
                    try:
                        progress_callback()
                    except Exception:
                        pass

    threads = [threading.Thread(target=_probe, args=(h,), daemon=True) for h in hosts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    found.sort(key=lambda d: tuple(int(p) for p in d['ip'].split('.')))
    return found


def get_arp_table():
    """
    Best-effort read of the local ARP cache as {ip: mac}. A TCP-connect scan
    of the subnet (scan_for_ssh_hosts) populates this cache as a side effect
    for local hosts, so calling this right after a scan will generally have
    entries for whatever was just found — letting us grab a MAC address for
    Wake-on-LAN without ever needing to SSH into the machine.
    Cross-platform: reads /proc/net/arp on Linux, shells out to `arp -a`
    elsewhere (Windows/macOS).
    """
    table = {}
    mac_re = re.compile(r'([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})')
    ip_re = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')

    try:
        if os.path.exists('/proc/net/arp'):
            with open('/proc/net/arp', 'r') as f:
                lines = f.readlines()[1:]
            for line in lines:
                parts = line.split()
                if len(parts) >= 4:
                    ip, mac = parts[0], parts[3]
                    if mac_re.fullmatch(mac) and mac.lower() != '00:00:00:00:00:00':
                        table[ip] = mac.lower()
        else:
            output = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=5).stdout
            for line in output.splitlines():
                ip_m, mac_m = ip_re.search(line), mac_re.search(line)
                if ip_m and mac_m:
                    table[ip_m.group(1)] = mac_m.group(1).replace('-', ':').lower()
    except Exception:
        pass
    return table


# --- Database Manager ---
class DBManager:
    def __init__(self, db_name=DB_NAME):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._create_table()
        self._upgrade_db()  # Run an upgrade on the DB if number of fields is different

    def _create_table(self):
        """Creates the basic table structure if it doesn't exist."""
        # Create the main PC table with all 10 columns
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS pcs (
                id INTEGER PRIMARY KEY,
                hostname TEXT NOT NULL,
                username TEXT NOT NULL,
                password_encrypted BLOB NOT NULL,
                alias TEXT,
                status TEXT,
                last_update TEXT,
                pending_updates INTEGER DEFAULT 0,
                uptime TEXT DEFAULT 'N/A',
                disk_free TEXT DEFAULT 'N/A',
                mac_address TEXT DEFAULT ''
            )
        """)
        
        # Create the snapshots table
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS software_snapshots (
                id INTEGER PRIMARY KEY,
                pc_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                package_list TEXT NOT NULL,
                FOREIGN KEY (pc_id) REFERENCES pcs(id)
            )
        """)
        self.conn.commit()
        print("[INFO] Database structure verified.")

    def _upgrade_db(self):
        """Adds missing columns to old databases automatically."""
        cursor = self.conn.cursor()

        # ── pcs table migrations ──────────────────────────────────────────────
        cursor.execute("PRAGMA table_info(pcs)")
        existing_pcs_columns = [column[1] for column in cursor.fetchall()]

        pcs_migrations = [
            ("pending_updates", "INTEGER DEFAULT 0"),
            ("uptime", "TEXT DEFAULT 'N/A'"),
            ("disk_free", "TEXT DEFAULT 'N/A'"),
            ("mac_address", "TEXT DEFAULT ''")
        ]
        for col_name, col_type in pcs_migrations:
            if col_name not in existing_pcs_columns:
                try:
                    cursor.execute(f"ALTER TABLE pcs ADD COLUMN {col_name} {col_type}")
                    print(f"[DB] Added missing column to pcs: {col_name}")
                except sqlite3.OperationalError:
                    pass

        # ── software_snapshots table migrations ───────────────────────────────
        cursor.execute("PRAGMA table_info(software_snapshots)")
        existing_snap_columns = [column[1] for column in cursor.fetchall()]

        snap_migrations = [
            ("user_list",  "TEXT DEFAULT ''"),   # passwd-style lines: user:uid:gid:home:shell
            ("group_list", "TEXT DEFAULT ''"),   # group-style lines:  group:gid:members
        ]
        for col_name, col_type in snap_migrations:
            if col_name not in existing_snap_columns:
                try:
                    cursor.execute(f"ALTER TABLE software_snapshots ADD COLUMN {col_name} {col_type}")
                    print(f"[DB] Added missing column to software_snapshots: {col_name}")
                except sqlite3.OperationalError:
                    pass

        self.conn.commit()

    def get_all_pcs(self):
        # Using SELECT * for robust searches
        self.cursor.execute("SELECT * FROM pcs ORDER BY alias")
        return self.cursor.fetchall()

    def add_pc(self, hostname, username, encrypted_password, alias, mac_address=''):
        self.cursor.execute(
            "INSERT INTO pcs (hostname, username, password_encrypted, alias, status, last_update, pending_updates, uptime, disk_free, mac_address) VALUES (?, ?, ?, ?, 'Unknown', 'N/A', 0, 'N/A', 'N/A', ?)",
            (hostname, username, encrypted_password, alias, mac_address or ''),
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def update_mac(self, pc_id, mac_address):
        """Stores/refreshes the MAC address for a PC (used for Wake-on-LAN)."""
        if not mac_address:
            return
        try:
            self.cursor.execute(
                "UPDATE pcs SET mac_address=? WHERE id=?",
                (mac_address, pc_id),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            print(f"[DB ERROR] Failed to update MAC for PC {pc_id}: {e}")

    def delete_pc(self, pc_id):
        self.cursor.execute("DELETE FROM pcs WHERE id=?", (pc_id,))
        self.conn.commit()

    def update_status(self, pc_id, status, last_update, pending_updates=0, uptime='N/A', disk_free='N/A'):
        """Updates the database record for a PC."""
        try:
            self.cursor.execute(
                "UPDATE pcs SET status=?, last_update=?, pending_updates=?, uptime=?, disk_free=? WHERE id=?",
                (status, last_update, pending_updates, uptime, disk_free, pc_id),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            print(f"[DB ERROR] Failed to update status for PC {pc_id}: {e}")

    def update_pc(self, pc_id, hostname, username, encrypted_password, alias):
        self.cursor.execute(
            "UPDATE pcs SET hostname=?, username=?, password_encrypted=?, alias=? WHERE id=?",
            (hostname, username, encrypted_password, alias, pc_id),
        )
        self.conn.commit()

    def delete_all_snapshots_for_pc(self, pc_id):
        self.cursor.execute("DELETE FROM software_snapshots WHERE pc_id=?", (pc_id,))
        self.conn.commit()

    def save_snapshot(self, pc_id, package_list_data, user_list_data="", group_list_data=""):
        self.delete_all_snapshots_for_pc(pc_id)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute(
            "INSERT INTO software_snapshots (pc_id, timestamp, package_list, user_list, group_list) "
            "VALUES (?, ?, ?, ?, ?)",
            (pc_id, timestamp, package_list_data, user_list_data or "", group_list_data or ""),
        )
        self.conn.commit()

    def get_latest_snapshot(self, pc_id):
        """Returns package_list string only (backwards-compatible)."""
        self.cursor.execute(
            "SELECT package_list FROM software_snapshots WHERE pc_id=? ORDER BY timestamp DESC LIMIT 1",
            (pc_id,),
        )
        result = self.cursor.fetchone()
        return result[0] if result else None

    def get_latest_snapshot_full(self, pc_id):
        """Returns (package_list, user_list, group_list) tuple, or None."""
        self.cursor.execute(
            "SELECT package_list, user_list, group_list "
            "FROM software_snapshots WHERE pc_id=? ORDER BY timestamp DESC LIMIT 1",
            (pc_id,),
        )
        result = self.cursor.fetchone()
        if result:
            return result[0], result[1] or "", result[2] or ""
        return None

    def get_latest_snapshot_timestamp(self, pc_id):
        self.cursor.execute(
            "SELECT timestamp FROM software_snapshots WHERE pc_id=? ORDER BY timestamp DESC LIMIT 1",
            (pc_id,),
        )
        result = self.cursor.fetchone()
        return result[0] if result else "N/A"


# --- Tooltip Class ---
class Tooltip:
    """
    A polished dark tooltip that appears after a short hover delay.
    Attach with: Tooltip(widget, "Your text here")
    """
    DELAY_MS  = 500     # ms before tooltip appears
    BG        = '#2a2b2d'
    FG        = '#e2e3e5'
    BORDER    = '#4a9eff'
    FONT      = ('Segoe UI', 9)

    def __init__(self, widget, text):
        self.widget  = widget
        self.text    = text
        self._win    = None
        self._job    = None
        widget.bind('<Enter>',   self._schedule, add='+')
        widget.bind('<Leave>',   self._cancel,   add='+')
        widget.bind('<Button-1>', self._cancel,  add='+')

    def _schedule(self, event=None):
        self._cancel()
        self._job = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self, event=None):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None
        if self._win:
            self._win.destroy()
            self._win = None

    def _show(self):
        if self._win:
            return
        # Position just below-right of the widget
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4

        self._win = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)      # no window chrome
        tw.wm_geometry(f'+{x}+{y}')
        tw.wm_attributes('-topmost', True)

        # Outer border frame
        border = tk.Frame(tw, bg=self.BORDER, padx=1, pady=1)
        border.pack()
        inner = tk.Frame(border, bg=self.BG, padx=8, pady=5)
        inner.pack()
        tk.Label(inner, text=self.text,
                 bg=self.BG, fg=self.FG,
                 font=self.FONT,
                 justify='left',
                 wraplength=260).pack()


# --- Autocomplete Entry Widget ---
class AutocompleteEntry(tk.Entry):
    """
    A plain tk.Entry that shows a filtering dropdown of suggestions as the
    user types. Free-text entry is always preserved — nothing here validates
    or restricts what can be typed; the dropdown is purely a shortcut for
    values that match a discovered/known list. Clicking a suggestion fills
    the field and closes the dropdown; typing something that matches nothing
    just leaves the dropdown empty.

    Usage:
        entry = AutocompleteEntry(parent, ...)
        entry.set_suggestions([{'value': '192.168.1.42', 'label': '192.168.1.42  (pi4)'}, ...])
    """
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._suggestions = []   # list of {'value': ..., 'label': ...}
        self._popup = None
        self._listbox = None
        self.bind('<KeyRelease>', self._on_keyrelease)
        self.bind('<FocusOut>', lambda e: self.after(150, self._hide_popup))
        self.bind('<Escape>', lambda e: self._hide_popup())

    def set_suggestions(self, suggestions):
        """suggestions: list of {'value': str, 'label': str}"""
        self._suggestions = suggestions or []

    def _on_keyrelease(self, event):
        if event.keysym in ('Up', 'Down', 'Return', 'Escape'):
            return
        typed = self.get().strip().lower()
        if not typed or not self._suggestions:
            self._hide_popup()
            return

        matches = [s for s in self._suggestions
                   if typed in s['value'].lower() or typed in s['label'].lower()]
        if not matches:
            self._hide_popup()
            return
        self._show_popup(matches[:12])

    def _show_popup(self, matches):
        if self._popup is None:
            self._popup = tk.Toplevel(self)
            self._popup.wm_overrideredirect(True)
            self._popup.wm_attributes('-topmost', True)
            self._listbox = tk.Listbox(self._popup,
                                        bg=BG_TERTIARY, fg=FG_PRIMARY,
                                        selectbackground=ACCENT_BLUE,
                                        relief='flat', bd=1,
                                        highlightthickness=1,
                                        highlightbackground=BORDER_COLOR,
                                        font=('Consolas', 10),
                                        activestyle='none')
            self._listbox.pack(fill='both', expand=True)
            self._listbox.bind('<<ListboxSelect>>', self._on_select)
            self._listbox.bind('<Button-1>', self._on_select, add='+')

        self._listbox.delete(0, 'end')
        self._match_values = []
        for m in matches:
            self._listbox.insert('end', m['label'])
            self._match_values.append(m['value'])

        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        width = max(self.winfo_width(), 220)
        height = min(22 * len(matches) + 4, 200)
        self._popup.wm_geometry(f'{width}x{height}+{x}+{y}')
        self._popup.deiconify()

    def _on_select(self, event=None):
        if not self._listbox:
            return
        sel = self._listbox.curselection()
        if not sel:
            return
        value = self._match_values[sel[0]]
        self.delete(0, 'end')
        self.insert(0, value)
        self._hide_popup()
        self.icursor('end')

    def _hide_popup(self):
        if self._popup is not None:
            self._popup.destroy()
            self._popup = None
            self._listbox = None


# --- Add PC Dialog Class ---
class AddPCDialog(tk.Toplevel):
    def __init__(self, parent, is_edit=False):
        super().__init__(parent)
        self.title("Edit PC Details" if is_edit else "Add New PC")
        self.transient(parent)
        self.parent = parent
        self.result = None
        self.data = {}
        self._idle_check_timer_id = None
        self._watchdog_timer_id = None

        # ip -> {'hostname': str|None, 'mac': str|None}, populated by the
        # background subnet scan. Used to auto-suggest in the hostname field
        # and to silently carry a discovered MAC address through to on_ok()
        # for Wake-on-LAN, without ever forcing the user to type one in.
        self._discovered = {}

        self.config(bg=BG_PRIMARY)
        self.minsize(600, 280)

        main_frame = tk.Frame(self, bg=BG_PRIMARY)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)

        # Title
        title_label = tk.Label(main_frame,
                               text="Edit PC Details" if is_edit else "Add New PC",
                               bg=BG_PRIMARY, fg=FG_PRIMARY,
                               font=('Segoe UI', 14, 'bold'))
        title_label.pack(pady=(0, 20))

        # Fields frame
        fields_frame = tk.Frame(main_frame, bg=BG_PRIMARY)
        fields_frame.pack(fill='both', expand=True, pady=10)

        fields = [
            ("Alias:", "alias_entry"),
            ("Hostname or IP:", "hostname_entry"),
            ("Username:", "username_entry"),
            ("Password:", "password_entry"),
        ]

        self.entries = {}
        for i, (label_text, entry_key) in enumerate(fields):
            label = tk.Label(fields_frame, text=label_text,
                           bg=BG_PRIMARY, fg=FG_SECONDARY,
                           font=('Segoe UI', 10), anchor='w')
            label.grid(row=i, column=0, sticky='w', padx=(0, 15), pady=8)

            entry_cls = AutocompleteEntry if entry_key == "hostname_entry" else tk.Entry
            entry = entry_cls(fields_frame, width=40,
                           bg=BG_TERTIARY, fg=FG_PRIMARY,
                           insertbackground=FG_PRIMARY,
                           relief='flat', bd=0,
                           font=('Segoe UI', 10))
            entry.config(highlightthickness=1, highlightcolor=ACCENT_BLUE,
                        highlightbackground=BORDER_COLOR)

            if entry_key == "password_entry":
                entry.config(show='●')
                if is_edit:
                    entry.insert(0, "(Leave blank to keep existing password)")

            if entry_key == "hostname_entry":
                self.hostname_entry = entry

            self.entries[entry_key] = entry
            entry.grid(row=i, column=1, sticky='we', padx=0, pady=8)

        fields_frame.columnconfigure(1, weight=1)

        # Scan status line — shows progress of the background subnet scan
        # that powers the hostname autocomplete suggestions.
        self.scan_status_var = tk.StringVar(value="")
        scan_status_label = tk.Label(main_frame, textvariable=self.scan_status_var,
                                      bg=BG_PRIMARY, fg=FG_TERTIARY,
                                      font=('Segoe UI', 8), anchor='w')
        scan_status_label.pack(fill='x', pady=(0, 4))

        # Kick off a background network scan so suggestions are ready (or
        # filling in) by the time the user starts typing. Never blocks the UI.
        if not is_edit:
            self._start_network_scan()

        # Button frame
        button_frame = tk.Frame(main_frame, bg=BG_PRIMARY)
        button_frame.pack(fill='x', pady=(20, 0))

        cancel_btn = AdaptiveButton(button_frame, text="Cancel",
                              command=self.destroy,
                              bg=BG_TERTIARY, fg=FG_PRIMARY,
                              font=('Segoe UI', 10),
                              relief='flat', bd=0,
                              padx=20, pady=8,
                              cursor='hand2')
        cancel_btn.pack(side='right', padx=(10, 0))

        ok_btn = AdaptiveButton(button_frame, text="OK",
                          command=self.on_ok,
                          bg=BTN_PRIMARY, fg='white',
                          font=('Segoe UI', 10, 'bold'),
                          relief='flat', bd=0,
                          padx=20, pady=8,
                          cursor='hand2')
        ok_btn.pack(side='right')

        # Hover effects
        def on_enter(e, btn, color):
            btn['bg'] = color
        def on_leave(e, btn, color):
            btn['bg'] = color

        ok_btn.bind('<Enter>', lambda e: on_enter(e, ok_btn, BTN_PRIMARY_HOVER))
        ok_btn.bind('<Leave>', lambda e: on_leave(e, ok_btn, BTN_PRIMARY))
        cancel_btn.bind('<Enter>', lambda e: on_enter(e, cancel_btn, BG_SECONDARY))
        cancel_btn.bind('<Leave>', lambda e: on_leave(e, cancel_btn, BG_TERTIARY))

        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.update_idletasks()
        self.grab_set()
        width = self.winfo_width()
        height = self.winfo_height()
        x = parent.winfo_x() + parent.winfo_width() // 2 - width // 2
        y = parent.winfo_y() + parent.winfo_height() // 2 - height // 2
        self.geometry(f'+{x}+{y}')

    def _start_network_scan(self):
        self.scan_status_var.set("Scanning local network for SSH-reachable hosts...")

        def worker():
            hosts = get_local_subnet_hosts()
            if not hosts:
                self.after(0, lambda: self.scan_status_var.set(
                    "Could not determine local subnet — enter hostname/IP manually."))
                return

            found = scan_for_ssh_hosts(hosts)
            arp = get_arp_table()

            for entry in found:
                entry['mac'] = arp.get(entry['ip'])

            self.after(0, lambda: self._apply_scan_results(found))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_scan_results(self, found):
        if not self.winfo_exists():
            return  # dialog was closed before the scan finished

        self._discovered = {f['ip']: {'hostname': f['hostname'], 'mac': f['mac']} for f in found}

        suggestions = []
        for f in found:
            label = f['ip'] if not f['hostname'] else f"{f['ip']}  ({f['hostname']})"
            suggestions.append({'value': f['ip'], 'label': label})
            if f['hostname']:
                # Also suggest by hostname, so typing a name works too
                suggestions.append({'value': f['ip'], 'label': f"{f['hostname']}  ({f['ip']})"})

        self.hostname_entry.set_suggestions(suggestions)

        if found:
            self.scan_status_var.set(
                f"Found {len(found)} SSH-reachable device(s) on the network — start typing to see suggestions.")
        else:
            self.scan_status_var.set("No SSH-reachable devices found on the local network.")

    def on_ok(self):
        self.data = {key.replace('_entry', ''): entry.get().strip()
                     for key, entry in self.entries.items()}

        # If what was entered/selected matches a discovered host, carry its
        # MAC address through silently for Wake-on-LAN — no extra field,
        # no extra step for the user.
        entered = self.data.get('hostname', '')
        match = self._discovered.get(entered)
        self.data['mac_address'] = (match or {}).get('mac') or ''

        self.result = "ok"
        self.destroy()

    def show(self):
        self.wait_window(self)
        return self.result, self.data


# --- Deploy Software Dialog Class ---
class DeploySoftwareDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Deploy Software to Selected PCs")
        self.transient(parent)
        self.parent = parent
        self.result = None
        self.packages = []

        self.placeholder_text = "e.g., htop git vim curl"
        self.placeholder_color = '#666666'
        self.default_color = FG_PRIMARY

        self.config(bg=BG_PRIMARY)
        self.minsize(500, 220)

        main_frame = tk.Frame(self, bg=BG_PRIMARY)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)

        # Title
        title_label = tk.Label(main_frame,
                               text="Deploy Software",
                               bg=BG_PRIMARY, fg=FG_PRIMARY,
                               font=('Segoe UI', 14, 'bold'))
        title_label.pack(pady=(0, 10))

        info_label = tk.Label(main_frame,
                             text="Enter package names separated by space or comma:",
                             bg=BG_PRIMARY, fg=FG_SECONDARY,
                             font=('Segoe UI', 9))
        info_label.pack(anchor='w', pady=(0, 10))

        self.software_entry = tk.Entry(main_frame, width=50,
                                      bg=BG_TERTIARY, fg=self.placeholder_color,
                                      insertbackground=FG_PRIMARY,
                                      relief='flat', bd=0,
                                      font=('Segoe UI', 11))
        self.software_entry.config(highlightthickness=1, highlightcolor=ACCENT_BLUE,
                                  highlightbackground=BORDER_COLOR)
        self.software_entry.insert(0, self.placeholder_text)
        self.software_entry.pack(fill='x', pady=10, ipady=8)

        self.software_entry.bind('<FocusIn>', self.on_entry_focus)

        # Button frame
        button_frame = tk.Frame(main_frame, bg=BG_PRIMARY)
        button_frame.pack(fill='x', pady=(20, 0))

        cancel_btn = AdaptiveButton(button_frame, text="Cancel",
                              command=self.destroy,
                              bg=BG_TERTIARY, fg=FG_PRIMARY,
                              font=('Segoe UI', 10),
                              relief='flat', bd=0,
                              padx=20, pady=8,
                              cursor='hand2')
        cancel_btn.pack(side='right', padx=(10, 0))

        deploy_btn = AdaptiveButton(button_frame, text="Deploy",
                              command=self.on_deploy,
                              bg=BTN_SUCCESS, fg='white',
                              font=('Segoe UI', 10, 'bold'),
                              relief='flat', bd=0,
                              padx=20, pady=8,
                              cursor='hand2')
        deploy_btn.pack(side='right')

        # Hover effects
        deploy_btn.bind('<Enter>', lambda e: deploy_btn.config(bg=BTN_SUCCESS_HOVER))
        deploy_btn.bind('<Leave>', lambda e: deploy_btn.config(bg=BTN_SUCCESS))
        cancel_btn.bind('<Enter>', lambda e: cancel_btn.config(bg=BG_SECONDARY))
        cancel_btn.bind('<Leave>', lambda e: cancel_btn.config(bg=BG_TERTIARY))

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        self.grab_set()
        width = self.winfo_width()
        height = self.winfo_height()
        x = parent.winfo_x() + parent.winfo_width() // 2 - width // 2
        y = parent.winfo_y() + parent.winfo_height() // 2 - height // 2
        self.geometry(f'+{x}+{y}')

    def on_entry_focus(self, event):
        if self.software_entry.get() == self.placeholder_text:
            self.software_entry.delete(0, 'end')
            self.software_entry.config(fg=self.default_color)

    def on_deploy(self):
        text = self.software_entry.get().strip()
        if not text or text == self.placeholder_text:
            messagebox.showerror("Error", "Package list cannot be empty.")
            return
        self.packages = text.replace(',', ' ').split()
        self.result = "deploy"
        self.destroy()

    def show(self):
        self.wait_window(self)
        return self.result, self.packages


# --- Run Command Dialog Class ---
class RunCommandDialog(tk.Toplevel):
    # Dictionary of popular commands for quick selection
    #  You can add / alter your own here
    POPULAR_COMMANDS = {
        "Show Disk Usage": "df -h",
        "View System Uptime": "uptime",
        "Check Free Memory (RAM)": "free -h",
        "Top 5 Memory Hogs": "ps aux --sort=-%mem | head -n 6",
        "CPU Temperature (Universal)": "cat /sys/class/thermal/thermal_zone*/temp | cut -c1-2 | sed s/$/°C/",
        "Quick Port Scan (Ports 1-1024) - Run without Sudo": "for port in $(seq 1 1024); do (echo > /dev/tcp/127.0.0.1/$port) >/dev/null 2>&1 && echo Port $port is OPEN; done",
        "Check Kernel Information": "uname -a",
        "View System Logs (tail -n 50)": "tail -n 50 /var/log/syslog || journalctl -n 50",
        "Check Failed Systemd Services": "systemctl --failed",
        "Network Card Info (ifconfig fallback)": "ip a || ifconfig -a",
        "Test Internet Connection (Ping Google DNS)": "ping -c 4 8.8.8.8",
        "Check IP Address (All Non-Loopback)": "ip a | grep 'inet ' | grep -v '127.0.0.1'",
        "Remove Package": "apt remove --purge -y <TYPE PACKAGE NAME HERE>",
        "Cleanup (Autoremove & Purge Cache)": "sudo apt update && sudo apt autoremove --purge -y && sudo apt clean",
    }

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Run Remote Command")
        self.transient(parent)
        self.parent = parent
        self.result = None
        self.command = ""
        self.use_sudo = tk.BooleanVar(value=True)

        self.config(bg=BG_PRIMARY)
        # Increased minsize to accommodate the side-by-side elements
        self.minsize(700, 400)

        main_frame = tk.Frame(self, bg=BG_PRIMARY)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)

        # Title
        title_label = tk.Label(main_frame,
                               text="Run Remote Command",
                               bg=BG_PRIMARY, fg=FG_PRIMARY,
                               font=('Segoe UI', 14, 'bold'))
        title_label.pack(pady=(0, 10))

        # --- Command Section Container (Grid for Listbox + Textbox) ---
        command_section = tk.Frame(main_frame, bg=BG_PRIMARY)
        command_section.pack(fill='both', expand=True, pady=(0, 15))

        # Configure columns: Listbox (weight 2) and Textbox (weight 1)
        command_section.columnconfigure(0, weight=2)
        command_section.columnconfigure(1, weight=1)

        # --- Popular Commands Listbox ---
        list_label = tk.Label(command_section,
                             text="Popular Commands (Click to select):",
                             bg=BG_PRIMARY, fg=FG_SECONDARY,
                             font=('Segoe UI', 9))
        list_label.grid(row=0, column=0, sticky='w', pady=(0, 5))

        list_frame = tk.Frame(command_section, bg=BORDER_COLOR)
        list_frame.grid(row=1, column=0, sticky='nsew', padx=(0, 10))

        self.command_listbox = tk.Listbox(list_frame, height=10,
                                         width=40, # <-- WIDTH FIX APPLIED HERE
                                         bg=BG_TERTIARY, fg=FG_PRIMARY,
                                         selectbackground=ACCENT_BLUE,
                                         selectforeground='white',
                                         relief='flat', bd=0,
                                         exportselection=False,
                                         font=('Consolas', 10))

        # Populate listbox with command display names
        for display_name in self.POPULAR_COMMANDS.keys():
            self.command_listbox.insert(tk.END, display_name)

        # Use ttk.Scrollbar here
        list_vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.command_listbox.yview)
        self.command_listbox.configure(yscrollcommand=list_vsb.set)

        list_vsb.pack(side='right', fill='y')
        self.command_listbox.pack(side='left', fill='both', expand=True, padx=1, pady=1)
        # Bind the selection event to the handler method
        self.command_listbox.bind('<<ListboxSelect>>', self._on_command_select)

        # --- Manual Command Text Widget ---
        text_label = tk.Label(command_section,
                             text="Manual Command / Selected Command:",
                             bg=BG_PRIMARY, fg=FG_SECONDARY,
                             font=('Segoe UI', 9))
        text_label.grid(row=0, column=1, sticky='w', pady=(0, 5))

        text_frame = tk.Frame(command_section, bg=BORDER_COLOR)
        text_frame.grid(row=1, column=1, sticky='nsew')

        self.command_text = tk.Text(text_frame, wrap='word', height=10,
                                   bg=BG_TERTIARY, fg=FG_PRIMARY,
                                   insertbackground=FG_PRIMARY,
                                   relief='flat', bd=0,
                                   font=('Consolas', 10))
        self.command_text.pack(padx=1, pady=1, fill='both', expand=True)

        # Sudo checkbox
        check_frame = tk.Frame(main_frame, bg=BG_PRIMARY)
        check_frame.pack(fill='x', pady=(0, 15))

        sudo_check = tk.Checkbutton(check_frame,
                                   text="Run with sudo (recommended)",
                                   variable=self.use_sudo,
                                   bg=BG_PRIMARY, fg=FG_SECONDARY,
                                   selectcolor=BG_TERTIARY,
                                   activebackground=BG_PRIMARY,
                                   activeforeground=FG_PRIMARY,
                                   font=('Segoe UI', 9),
                                   cursor='hand2')
        sudo_check.pack(anchor='w')

        # Button frame
        button_frame = tk.Frame(main_frame, bg=BG_PRIMARY)
        button_frame.pack(fill='x')

        cancel_btn = AdaptiveButton(button_frame, text="Cancel",
                              command=self.destroy,
                              bg=BG_TERTIARY, fg=FG_PRIMARY,
                              font=('Segoe UI', 10),
                              relief='flat', bd=0,
                              padx=20, pady=8,
                              cursor='hand2')
        cancel_btn.pack(side='right', padx=(10, 0))

        run_btn = AdaptiveButton(button_frame, text="Run Command",
                           command=self.on_run,
                           bg=BTN_PRIMARY, fg='white',
                           font=('Segoe UI', 10, 'bold'),
                           relief='flat', bd=0,
                           padx=20, pady=8,
                           cursor='hand2')
        run_btn.pack(side='right')

        # Hover effects
        run_btn.bind('<Enter>', lambda e: run_btn.config(bg=BTN_PRIMARY_HOVER))
        run_btn.bind('<Leave>', lambda e: run_btn.config(bg=BTN_PRIMARY))
        cancel_btn.bind('<Enter>', lambda e: cancel_btn.config(bg=BG_SECONDARY))
        cancel_btn.bind('<Leave>', lambda e: cancel_btn.config(bg=BG_TERTIARY))

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        self.grab_set()
        width = self.winfo_width()
        height = self.winfo_height()
        x = parent.winfo_x() + parent.winfo_width() // 2 - width // 2
        y = parent.winfo_y() + parent.winfo_height() // 2 - height // 2
        self.geometry(f'+{x}+{y}')

    def _on_command_select(self, event):
        """Handler for when a command is selected from the listbox."""
        selection = self.command_listbox.curselection()
        if selection:
            # Get the display name from the listbox
            display_name = self.command_listbox.get(selection[0])
            # Look up the actual command string
            command_to_run = self.POPULAR_COMMANDS.get(display_name)

            if command_to_run:
                # Clear existing text
                self.command_text.delete("1.0", 'end')
                # Insert the selected command
                self.command_text.insert("1.0", command_to_run)

    def on_run(self):
        self.command = self.command_text.get("1.0", 'end-1c').strip()
        if not self.command:
            messagebox.showerror("Error", "Command cannot be empty.")
            return
        self.result = "run"
        self.destroy()

    def show(self):
        self.wait_window(self)
        return self.result, self.command, self.use_sudo.get()

# --- Send File Dialog Class ---
class SendFileDialog(tk.Toplevel):
    def __init__(self, parent, pc_alias):
        super().__init__(parent)
        self.title(f"Send File to {pc_alias}")
        self.config(bg=BG_PRIMARY)
        self.result = None
        self.transient(parent)

        main_frame = tk.Frame(self, bg=BG_PRIMARY)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)

        title_label = tk.Label(main_frame, text=f"Send File to {pc_alias}", 
                              bg=BG_PRIMARY, fg=FG_PRIMARY, font=('Segoe UI', 12, 'bold'))
        title_label.pack(pady=(0, 15))

        # File Selection Frame
        file_frame = tk.Frame(main_frame, bg=BG_PRIMARY)
        file_frame.pack(fill='x', pady=5)

        self.file_path = tk.StringVar()
        entry = tk.Entry(file_frame, textvariable=self.file_path, bg=BG_TERTIARY, 
                         fg=FG_PRIMARY, insertbackground=FG_PRIMARY, borderwidth=0)
        entry.pack(side='left', fill='x', expand=True, padx=(0, 10), ipady=3)

        browse_btn = AdaptiveButton(file_frame, text="Browse", command=self.on_browse,
                                   bg=BG_SECONDARY, fg=FG_PRIMARY,
                                   activebackground=BORDER_COLOR, activeforeground=FG_PRIMARY,
                                   relief='flat', bd=0, padx=10, pady=4,
                                   font=('Segoe UI', 9), cursor='hand2')
        browse_btn.pack(side='right')
        browse_btn.bind('<Enter>', lambda e: browse_btn.config(bg=BORDER_COLOR))
        browse_btn.bind('<Leave>', lambda e: browse_btn.config(bg=BG_SECONDARY))

        # --- EXECUTE CHECKBOX ---
        self.execute_var = tk.BooleanVar(value=False)
        self.exec_check = tk.Checkbutton(
            main_frame,
            text="Execute script after upload",
            variable=self.execute_var,
            bg=BG_PRIMARY,
            fg=FG_SECONDARY,
            selectcolor=BG_TERTIARY,
            activebackground=BG_PRIMARY,
            activeforeground=FG_PRIMARY,
            font=('Segoe UI', 9)
        )
        self.exec_check.pack(anchor='w', pady=(5, 15))

        # Buttons
        btn_frame = tk.Frame(main_frame, bg=BG_PRIMARY)
        btn_frame.pack(fill='x', pady=(10, 0))

        cancel_btn = AdaptiveButton(btn_frame, text="Cancel", command=self.destroy,
                                   bg=BG_TERTIARY, fg=FG_PRIMARY,
                                   activebackground=BORDER_COLOR, activeforeground=FG_PRIMARY,
                                   relief='flat', bd=0, padx=20, pady=8,
                                   font=('Segoe UI', 10), cursor='hand2')
        cancel_btn.pack(side='right', padx=(10, 0))
        cancel_btn.bind('<Enter>', lambda e: cancel_btn.config(bg=BG_SECONDARY))
        cancel_btn.bind('<Leave>', lambda e: cancel_btn.config(bg=BG_TERTIARY))

        send_btn = AdaptiveButton(btn_frame, text="Send File", command=self.on_send,
                                 bg=BTN_PRIMARY, fg='white',
                                 activebackground=BTN_PRIMARY_HOVER, activeforeground='white',
                                 relief='flat', bd=0, padx=20, pady=8,
                                 font=('Segoe UI', 10, 'bold'), cursor='hand2')
        send_btn.pack(side='right')
        send_btn.bind('<Enter>', lambda e: send_btn.config(bg=BTN_PRIMARY_HOVER))
        send_btn.bind('<Leave>', lambda e: send_btn.config(bg=BTN_PRIMARY))

        self.minsize(420, 200)
        self.update_idletasks()
        self.grab_set()
        x = parent.winfo_x() + parent.winfo_width() // 2 - self.winfo_width() // 2
        y = parent.winfo_y() + parent.winfo_height() // 2 - self.winfo_height() // 2
        self.geometry(f'+{x}+{y}')

    def on_browse(self):
        path = filedialog.askopenfilename()
        if path:
            self.file_path.set(path)

    def on_send(self):
        if not self.file_path.get():
            messagebox.showwarning("Warning", "Please select a file first.")
            return
        self.result = {
            'local_path': self.file_path.get(),
            'execute_after': self.execute_var.get()
        }
        self.destroy()

    def show(self):
        self.wait_window(self)
        return self.result, self.file_path.get()


class CloneSourceDialog(tk.Toplevel):
     
    def __init__(self, parent, target_pc, all_pcs, db_manager):
        """
        Parameters
        ──────────
        parent      : tk root / master window
        target_pc   : dict  – the selected PC that will RECEIVE packages
        all_pcs     : list  – full pc_list_data (target will be excluded)
        db_manager  : DBManager instance – used to look up snapshots
        """
        super().__init__(parent)
        self.title("Clone PC Setup")
        self.transient(parent)
        self.config(bg=BG_PRIMARY)
        self.resizable(False, False)
 
        self.result        = None          # 'clone' or None
        self.selected_source = None        # pc dict of chosen source
 
        self._target_pc   = target_pc
        self._db_manager  = db_manager
 
        # ── Build the list of valid source PCs (have a snapshot, not the target) ──
        self._source_options = []          # list of (display_label, pc_dict, snapshot_ts)
        for pc in all_pcs:
            if pc['id'] == target_pc['id']:
                continue
            ts = db_manager.get_latest_snapshot_timestamp(pc['id'])
            if ts and ts != 'N/A':
                label = f"{pc['alias']}  (snapshot: {ts})"
                self._source_options.append((label, pc, ts))
 
        # ── Main frame ────────────────────────────────────────────────────────
        main = tk.Frame(self, bg=BG_PRIMARY)
        main.pack(fill='both', expand=True, padx=24, pady=20)
 
        # Title
        tk.Label(main,
                 text="Clone PC Setup",
                 bg=BG_PRIMARY, fg=FG_PRIMARY,
                 font=('Segoe UI', 14, 'bold')).pack(anchor='w', pady=(0, 4))
 
        tk.Label(main,
                 text="Select a source PC whose snapshot will be used to bring the target\n"
                      "up to the same software state — packages and user accounts.",
                 bg=BG_PRIMARY, fg=FG_SECONDARY,
                 font=('Segoe UI', 9),
                 justify='left').pack(anchor='w', pady=(0, 18))
 
        # ── Two-column PC display ─────────────────────────────────────────────
        cols = tk.Frame(main, bg=BG_PRIMARY)
        cols.pack(fill='x', pady=(0, 16))
        cols.columnconfigure(0, weight=1)
        cols.columnconfigure(1, weight=0)   # arrow column – fixed
        cols.columnconfigure(2, weight=1)
 
        # Helper: draw a labelled PC card
        def _pc_card(parent, heading, alias, hostname, note_text=None, note_color=FG_SECONDARY):
            frame = tk.Frame(parent, bg=BG_TERTIARY,
                             highlightthickness=1,
                             highlightbackground=BORDER_COLOR)
            tk.Label(frame,
                     text=heading.upper(),
                     bg=BG_TERTIARY, fg=FG_SECONDARY,
                     font=('Segoe UI', 7, 'bold')).pack(anchor='w', padx=10, pady=(8, 0))
            tk.Label(frame,
                     text=alias,
                     bg=BG_TERTIARY, fg=FG_PRIMARY,
                     font=('Segoe UI', 12, 'bold')).pack(anchor='w', padx=10)
            tk.Label(frame,
                     text=hostname,
                     bg=BG_TERTIARY, fg=FG_SECONDARY,
                     font=('Segoe UI', 9)).pack(anchor='w', padx=10, pady=(0, 4))
            if note_text:
                tk.Label(frame,
                         text=note_text,
                         bg=BG_TERTIARY, fg=note_color,
                         font=('Segoe UI', 8)).pack(anchor='w', padx=10, pady=(0, 8))
            else:
                tk.Frame(frame, bg=BG_TERTIARY, height=8).pack()
            return frame
 
        # Source card (left) – driven by the combobox below
        self._source_card_alias    = tk.StringVar(value="— select below —")
        self._source_card_hostname = tk.StringVar(value="")
        self._source_card_note     = tk.StringVar(value="")

        src_frame = tk.Frame(cols, bg=BG_TERTIARY,
                             highlightthickness=1,
                             highlightbackground=BORDER_COLOR)
        src_frame.grid(row=0, column=0, sticky='nsew')

        tk.Label(src_frame,
                 text="SOURCE  (packages & users cloned from here)",
                 bg=BG_TERTIARY, fg=FG_SECONDARY,
                 font=('Segoe UI', 7, 'bold')).pack(anchor='w', padx=10, pady=(8, 0))
        tk.Label(src_frame,
                 textvariable=self._source_card_alias,
                 bg=BG_TERTIARY, fg=ACCENT_BLUE,
                 font=('Segoe UI', 12, 'bold')).pack(anchor='w', padx=10)
        tk.Label(src_frame,
                 textvariable=self._source_card_hostname,
                 bg=BG_TERTIARY, fg=FG_SECONDARY,
                 font=('Segoe UI', 9)).pack(anchor='w', padx=10, pady=(0, 4))
        tk.Label(src_frame,
                 textvariable=self._source_card_note,
                 bg=BG_TERTIARY, fg=ACCENT_AMBER,
                 font=('Segoe UI', 8),
                 wraplength=220, justify='left').pack(anchor='w', padx=10, pady=(0, 8))

        # Arrow (column 1) — points right: source → target
        tk.Label(cols,
                 text="─────►",
                 bg=BG_PRIMARY, fg=ACCENT_GREEN,
                 font=('Segoe UI', 18, 'bold')).grid(row=0, column=1, padx=14)

        # Target card (right, column 2) – fixed
        _pc_card(cols,
                 "TARGET  (packages & users will be added here)",
                 target_pc['alias'],
                 target_pc['hostname']
                 ).grid(row=0, column=2, sticky='nsew')

        # ── Source dropdown ───────────────────────────────────────────────────
        tk.Label(main,
                 text="Select source PC (must have an existing snapshot):",
                 bg=BG_PRIMARY, fg=FG_SECONDARY,
                 font=('Segoe UI', 9)).pack(anchor='w', pady=(0, 4))
 
        self._combo_var = tk.StringVar()
        self._combo = ttk.Combobox(main,
                                   textvariable=self._combo_var,
                                   state='readonly',
                                   font=('Segoe UI', 10))
        self._combo['values'] = [opt[0] for opt in self._source_options]
        self._combo.pack(fill='x', pady=(0, 6), ipady=4)
        self._combo.bind('<<ComboboxSelected>>', self._on_source_selected)
 
        # Style the combobox to match the dark theme
        style = ttk.Style()
        style.theme_use('default')
        style.configure('TCombobox',
                        fieldbackground=BG_TERTIARY,
                        background=BG_TERTIARY,
                        foreground=FG_PRIMARY,
                        selectbackground=ACCENT_BLUE,
                        selectforeground='white',
                        bordercolor=BORDER_COLOR,
                        arrowcolor=FG_SECONDARY)
 
        # "No snapshots" warning
        if not self._source_options:
            tk.Label(main,
                     text="⚠  No other PCs have a snapshot yet.\n"
                          "   Create a snapshot on a reference PC first.",
                     bg=BG_PRIMARY, fg=ACCENT_AMBER,
                     font=('Segoe UI', 9),
                     justify='left').pack(anchor='w', pady=(0, 10))
 
        # ── Info / warning box ────────────────────────────────────────────────
        info_frame = tk.Frame(main, bg='#1a1e24',
                              highlightthickness=1,
                              highlightbackground=BORDER_COLOR)
        info_frame.pack(fill='x', pady=(6, 16))
 
        tk.Label(info_frame,
                 text="ℹ  What will happen",
                 bg='#1a1e24', fg=ACCENT_BLUE,
                 font=('Segoe UI', 9, 'bold')).pack(anchor='w', padx=10, pady=(8, 2))
 
        notes = (
            "• The source PC's last snapshot (stored locally) will be used — "
            "the source machine does NOT need to be online.\n"
            "• Packages present on the source but MISSING on the target will be "
            "installed in batches using apt-get install -y --ignore-missing\n"
            "• Non-system user accounts (UID ≥ 1000) in the snapshot will be "
            "created on the target if they do not already exist.\n"
            "    — Username + UID match: skipped (already correct)\n"
            "    — Username exists, UID differs: skipped with a warning in the log\n"
            "    — Username new, UID free: created with matching UID\n"
            "    — Username new, UID taken: created with next free UID (logged)\n"
            "• New user accounts are created LOCKED with no password — "
            "the log will show the exact sudo passwd commands to run after cloning.\n"
            "• apt/dpkg packages only — Snap / Flatpak packages are listed as "
            "informational notes in the log but are NOT installed automatically.\n"
            "• APT sources lists are NOT modified.  If the source uses PPAs or "
            "vendor repos, some packages may fail — check the log after completion.\n"
            "• If the two PCs run different Ubuntu versions you will be warned "
            "before proceeding."
        )
        tk.Label(info_frame,
                 text=notes,
                 bg='#1a1e24', fg=FG_SECONDARY,
                 font=('Segoe UI', 8),
                 justify='left',
                 wraplength=520).pack(anchor='w', padx=10, pady=(0, 10))
 
        # ── Dry-run checkbox ──────────────────────────────────────────────────
        self._dry_run_var = tk.BooleanVar(value=False)
        dry_run_frame = tk.Frame(main, bg=BG_PRIMARY)
        dry_run_frame.pack(fill='x', pady=(0, 10))

        dry_run_check = tk.Checkbutton(
            dry_run_frame,
            text="Simulation mode  —  calculate and log the diff without installing anything",
            variable=self._dry_run_var,
            bg=BG_PRIMARY, fg=ACCENT_AMBER,
            selectcolor=BG_TERTIARY,
            activebackground=BG_PRIMARY,
            activeforeground=ACCENT_AMBER,
            font=('Segoe UI', 9),
            cursor='hand2'
        )
        dry_run_check.pack(anchor='w')
        self._dry_run_var.trace_add('write', lambda *_: self._refresh_clone_btn_label())

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = tk.Frame(main, bg=BG_PRIMARY)
        btn_frame.pack(fill='x', pady=(0, 0))

        cancel_btn = AdaptiveButton(btn_frame, text="Cancel",
                                    command=self.destroy,
                                    bg=BG_TERTIARY, fg=FG_PRIMARY,
                                    font=('Segoe UI', 10),
                                    relief='flat', bd=0,
                                    padx=20, pady=8, cursor='hand2')
        cancel_btn.pack(side='right', padx=(10, 0))
 
        self._clone_btn = AdaptiveButton(btn_frame,
                                         text="Clone PC  →",
                                         command=self._on_clone,
                                         bg=BTN_SUCCESS, fg='white',
                                         font=('Segoe UI', 10, 'bold'),
                                         relief='flat', bd=0,
                                         padx=20, pady=8, cursor='hand2',
                                         state='disabled')
        self._clone_btn.pack(side='right')
 
        # Hover effects
        self._clone_btn.bind('<Enter>', lambda e: self._clone_btn.config(bg=BTN_SUCCESS_HOVER)
                             if self._clone_btn['state'] == 'normal' else None)
        self._clone_btn.bind('<Leave>', lambda e: self._clone_btn.config(bg=BTN_SUCCESS))
        cancel_btn.bind('<Enter>', lambda e: cancel_btn.config(bg=BG_SECONDARY))
        cancel_btn.bind('<Leave>', lambda e: cancel_btn.config(bg=BG_TERTIARY))
 
        # Centre & grab — withdraw first to suppress white-flash on macOS
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.withdraw()                          # hide while building
        self.update_idletasks()                  # measure geometry without painting
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = parent.winfo_x() + parent.winfo_width()  // 2 - w // 2
        y = parent.winfo_y() + parent.winfo_height() // 2 - h // 2
        self.geometry(f'{w}x{h}+{x}+{y}')
        self.deiconify()                         # show at correct position in one step
        self.grab_set()
 
    # ── Internal helpers ──────────────────────────────────────────────────────
 
    def _on_source_selected(self, event=None):
        """Update the source card when the user picks from the combobox."""
        idx = self._combo.current()
        if idx < 0:
            return
        label, pc, ts = self._source_options[idx]
        self.selected_source = pc
        self._source_card_alias.set(pc['alias'])
        self._source_card_hostname.set(pc['hostname'])
        self._source_card_note.set(f"Snapshot taken: {ts}")
        self._clone_btn.config(state='normal')
        # Update button label to reflect simulation mode if already ticked
        self._refresh_clone_btn_label()

    def _refresh_clone_btn_label(self, *_):
        """Keep the Clone button label in sync with the dry-run checkbox."""
        if self._dry_run_var.get():
            self._clone_btn.config(text="Simulate  →", bg=BTN_WARNING, fg='white')
            self._clone_btn.bind('<Enter>', lambda e: self._clone_btn.config(bg=BTN_WARNING_HOVER)
                                 if self._clone_btn['state'] == 'normal' else None)
            self._clone_btn.bind('<Leave>', lambda e: self._clone_btn.config(bg=BTN_WARNING))
        else:
            self._clone_btn.config(text="Clone PC  →", bg=BTN_SUCCESS, fg='white')
            self._clone_btn.bind('<Enter>', lambda e: self._clone_btn.config(bg=BTN_SUCCESS_HOVER)
                                 if self._clone_btn['state'] == 'normal' else None)
            self._clone_btn.bind('<Leave>', lambda e: self._clone_btn.config(bg=BTN_SUCCESS))
 
    def _on_clone(self):
        if not self.selected_source:
            return
        self.result = 'clone'
        self.dry_run = self._dry_run_var.get()
        self.destroy()

    def show(self):
        self.wait_window(self)
        return self.result, self.selected_source, getattr(self, 'dry_run', False)


# --- Main Window Class ---
class PCManager:
    # This command runs 'apt update' to refresh the package list
    UPDATE_CHECK_CMD = "apt list --upgradable 2>/dev/null | grep -c upgradable || echo 0"
    

    def __init__(self, master, db_manager):
        self.master = master
        self.db_manager = db_manager
        self.pc_list_data = []
        self.current_status_index = 0
        self.monitoring_pcs = {}
        self.encryption_util = EncryptionUtility()
        self.selected_pc_ids = []
        self.pc_colors_map = {}
        

        # Snapshot drift: track the last time each PC id was diffed against its
        # snapshot. A PC is only re-checked once SNAPSHOT_DRIFT_COOLDOWN_SECONDS
        # has passed since its last check — decoupled from any fixed polling
        # timer, since checks are now event-driven (launch / manual refresh /
        # offline-recovery), not on a 5-minute loop.
        self._snapshot_diff_last_check = {}   # pc_id -> datetime of last drift check
        self.SNAPSHOT_DRIFT_COOLDOWN_SECONDS = 30 * 60  # 30 minutes

        self.master.title(f"🖥️ {APP_NAME}")
        self.master.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.master.config(bg=BG_PRIMARY)
        self._fleet_sync_in_progress = False  # True only during a full check_status sweep
        self._last_action_label = "App Started"
        self._last_action_time = datetime.now()
        self._active_actions = 0  
        self._active_actions_lock = threading.Lock()

        # Wake-before-connect bookkeeping: tracks the last time we sent a
        # magic packet to a given PC id, so a run of actions against the
        # same sleeping PC doesn't re-send/re-wait on every single call.
        self._last_wake_attempt = {}
        self._wake_lock = threading.Lock()

        # autotrack pc's online offline every 30 seconds
        self.active_pcs = set() 
        self._watchdog_running = False 
        # Start the watchdog timer
        self.master.after(30000, self._run_lightweight_watchdog)

        self.setup_styles()
        self.setup_ui()
        self.load_pc_data()

        self.log_message(f"[{APP_NAME}] Initialized.")
        self.master.after(2000, lambda: self.on_refresh_clicked(None))

    def setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            style.theme_use('default')

        # Treeview
        style.configure("Custom.Treeview",
                       background=BG_TERTIARY,
                       foreground=FG_PRIMARY,
                       fieldbackground=BG_TERTIARY,
                       borderwidth=0,
                       rowheight=26,
                       font=('Segoe UI', 10))
        style.map('Custom.Treeview',
                 background=[('selected', BTN_PRIMARY)],
                 foreground=[('selected', ACCENT_BLUE)])
        style.configure("Custom.Treeview.Heading",
                       background=BG_SECONDARY,
                       foreground=FG_SECONDARY,
                       relief="flat",
                       borderwidth=0,
                       font=('Segoe UI', 9))
        style.map("Custom.Treeview.Heading",
                 background=[('active', BG_TERTIARY)])

        # Scrollbar – keep thin and dark
        style.configure("Vertical.TScrollbar",
                       background=BG_TERTIARY,
                       troughcolor=BG_PRIMARY,
                       borderwidth=0,
                       arrowsize=10)
        style.map("Vertical.TScrollbar",
                 background=[('active', BORDER_COLOR)])

    def setup_ui(self):
        """Sets up the user interface — sidebar + main content area."""

        # Default target size — comfortable on most desktop monitors.
        target_w, target_h = 1180, 700

        # Clamp to the actual visible screen so the window (and specifically
        # the status bar / metrics ticker at its bottom edge, and the Exit
        # icon at the bottom of the sidebar) never ends up taller than the
        # desktop, which is what pushes it under the taskbar on smaller or
        # higher-DPI displays. This margin is intentionally generous — title
        # bar + taskbar height varies a lot by OS/theme/DPI scaling, and it's
        # far better to leave a bit of unused space than to clip real UI.
        screen_w = self.master.winfo_screenwidth()
        screen_h = self.master.winfo_screenheight()
        reserve_h = 150  # allowance for title bar + taskbar (+ DPI headroom)
        reserve_w = 40

        target_w = min(target_w, max(900, screen_w - reserve_w))
        target_h = min(target_h, max(500, screen_h - reserve_h))

        # Position explicitly near the top of the screen rather than trusting
        # the window manager's default placement, which can otherwise land
        # the window lower than expected and reintroduce the same clipping.
        pos_x = max(0, (screen_w - target_w) // 2)
        pos_y = 20

        self.master.geometry(f"{target_w}x{target_h}+{pos_x}+{pos_y}")
        self.master.minsize(min(1050, target_w), min(560, target_h))
        self.master.config(bg=BG_PRIMARY)

        # ── OUTER SHELL ─────────────────────────────────────────────────────
        outer = tk.Frame(self.master, bg=BG_PRIMARY)
        outer.pack(fill='both', expand=True)

        # ── SIDEBAR ─────────────────────────────────────────────────────────
        sidebar = tk.Frame(outer, bg=SB_BG, width=58)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)

        # Sidebar icon colour palette: (bg_norm, bg_hot, fg)
        _SB_COLORS = {
            'blue':   ('#0d1f33', '#1a3a5c', '#4a9eff'),
            'green':  ('#0d2218', '#1a3a2a', '#3ecf8e'),
            'amber':  ('#2a1f0d', '#3a2e1a', '#f6ad55'),
            'red':    ('#2a1515', '#3a1a1a', '#f56565'),
            'purple': ('#1a1530', '#2a2050', '#a78bfa'),
        }

        def _sb_icon(parent, symbol, tooltip_text, active=False, command=None,
                     danger=False, color=None, side='top'):
            if active:
                bg_norm = SB_ICON_ACTIVE_BG
                fg_norm = SB_ICON_ACTIVE_FG
                bg_hot  = SB_ICON_ACTIVE_BG
                fg_hot  = ACCENT_BLUE
            elif color and color in _SB_COLORS:
                bg_norm, bg_hot, fg_hot = _SB_COLORS[color]
                fg_norm = fg_hot
            elif danger:
                bg_norm, bg_hot, fg_hot = _SB_COLORS['red']
                fg_norm = fg_hot
            else:
                bg_norm = SB_BG
                fg_norm = SB_ICON_FG
                bg_hot  = '#1a2535'
                fg_hot  = ACCENT_BLUE

            lbl = tk.Label(parent, text=symbol, bg=bg_norm, fg=fg_norm,
                           font=('Segoe UI', 13), width=3,
                           cursor='hand2', pady=5)
            lbl.pack(fill='x', pady=1, side=side)
            lbl.bind('<Enter>', lambda e: lbl.config(bg=bg_hot,  fg=fg_hot))
            lbl.bind('<Leave>', lambda e: lbl.config(bg=bg_norm, fg=fg_norm))
            if command:
                lbl.bind('<Button-1>', lambda e: command())
            Tooltip(lbl, tooltip_text)
            return lbl

        def _sb_divider(label_text=None, side='top'):
            """Thin rule with optional small uppercase group label."""
            tk.Frame(sidebar, bg=BORDER_COLOR, height=1).pack(
                fill='x', padx=6, pady=(6, 0), side=side)
            if label_text:
                tk.Label(sidebar, text=label_text.upper(),
                         bg=SB_BG, fg='#3a3d42',
                         font=('Segoe UI', 7)).pack(pady=(2, 0), side=side)

        # Reserve Exit's space FIRST (pinned via side='bottom'), before any
        # other icon is packed. Pack carves cavity space in call order, so
        # whatever is packed last is first to get clipped when the sidebar's
        # total content doesn't fit the window — Exit is too important to
        # risk that, regardless of platform/screen size.
        _sb_divider(side='bottom')
        _sb_icon(sidebar, '⏏',
                 'Exit — close the application',
                 command=self._on_closing,
                 danger=True,
                 side='bottom')

        tk.Frame(sidebar, bg=SB_BG, height=6).pack()  # top padding

        # ── Group: Fleet ─────────────────────────────────────────────────────
        _sb_icon(sidebar, '⬡',
                 'Fleet — your registered Linux servers (current view)',
                 active=True)

        _sb_divider('Manage')

        _sb_icon(sidebar, '＋',
                 'Register PC — add a new Linux server to the fleet',
                 command=self.on_add_pc_clicked,
                 color='green')
        _sb_icon(sidebar, '✎',
                 'Edit PC — update hostname, credentials or alias for selected PC',
                 command=self.on_edit_pc_clicked,
                 color='amber')
        _sb_icon(sidebar, '✕',
                 'Remove PC — de-register selected PC from the fleet',
                 command=lambda: self._show_confirmation_dialog("delete", "Delete"),
                 danger=True)

        _sb_divider('Operate')

        _sb_icon(sidebar, '↺',
                 'Check Status — SSH into all PCs and refresh uptime, disk & update counts',
                 command=self.on_refresh_clicked,
                 color='blue')
        _sb_icon(sidebar, '⬆',
                 'Run Update — execute apt update && apt upgrade -y on selected PC(s)',
                 command=lambda: self._show_confirmation_dialog("update", "Run Update"),
                 color='green')
        _sb_icon(sidebar, '⊕',
                 'Deploy Software — install one or more apt packages on selected PC(s)',
                 command=self.on_deploy_software_clicked,
                 color='green')
        _sb_icon(sidebar, '⌨',
                 'Run Command — execute a custom SSH command on selected PC(s)',
                 command=self.on_run_command_clicked,
                 color='purple')
        _sb_icon(sidebar, '⇡',
                 'Send File — upload a local file to the selected PC via SFTP',
                 command=self.on_send_file_clicked,
                 color='blue')

        _sb_divider('Snapshots')

        _sb_icon(sidebar, '⧖',
                 'Create Snapshot — save current installed package list to the database including users and groups',
                 command=lambda: self._show_confirmation_dialog("create_snapshot", "Create Snapshot"),
                 color='purple')
        _sb_icon(sidebar, '⟲',
                 'Revert to Snapshot — remove packages installed since last snapshot\n⚠ This cannot be undone',
                 command=lambda: self._show_confirmation_dialog("revert", "Revert"),
                 color='purple')
        _sb_icon(sidebar, '⎘',
                 'Clone PC — Create the same users and Install missing packages on this selected PC from another PC\'s snapshot',
                 command=self.on_clone_pc_clicked,
                 color='blue')

        _sb_divider('Power')

        _sb_icon(sidebar, '⚡',
                 'Wake PC — send a Wake-on-LAN magic packet to selected PC(s)\n(requires WoL enabled in BIOS/OS and a wired connection)',
                 command=self.on_wake_clicked,
                 color='amber')
        _sb_icon(sidebar, '↻',
                 'Reboot PC — restart selected PC and monitor reconnection',
                 command=lambda: self._show_confirmation_dialog("reboot", "Reboot"),
                 danger=True)
        _sb_icon(sidebar, '⏻',
                 'Shutdown PC — power off selected PC and monitor connection loss',
                 command=lambda: self._show_confirmation_dialog("shutdown", "Shutdown"),
                 danger=True)

        # Fills any leftover space above the pinned bottom section (Exit),
        # purely for visual spacing — no longer load-bearing for Exit's visibility.
        tk.Frame(sidebar, bg=SB_BG).pack(fill='both', expand=True)

        # Sidebar right-edge border line
        tk.Frame(outer, bg=BORDER_COLOR, width=1).pack(side='left', fill='y')

        # ── MAIN AREA ────────────────────────────────────────────────────────
        main_area = tk.Frame(outer, bg=BG_PRIMARY)
        main_area.pack(side='left', fill='both', expand=True)

        # ── TOPBAR ───────────────────────────────────────────────────────────
        topbar = tk.Frame(main_area, bg=BG_SECONDARY, height=44)
        topbar.pack(fill='x')
        topbar.pack_propagate(False)
        tk.Frame(main_area, bg=BORDER_COLOR, height=1).pack(fill='x')

        tk.Label(topbar, text=APP_NAME,
                 bg=BG_SECONDARY, fg=FG_PRIMARY,
                 font=('Segoe UI', 12, 'bold')).pack(side='left', padx=16, pady=10)

        # Online/Offline badge labels (updated by load_pc_data / _update_pc_row_data)
        self._badge_online = tk.Label(topbar, text='', bg=BG_SECONDARY,
                                      fg=ACCENT_GREEN, font=('Segoe UI', 9, 'bold'),
                                      padx=6, pady=2)
        self._badge_online.pack(side='left', padx=(0, 4))
        self._badge_offline = tk.Label(topbar, text='', bg=BG_SECONDARY,
                                       fg=ACCENT_RED, font=('Segoe UI', 9, 'bold'),
                                       padx=6, pady=2)
        self._badge_offline.pack(side='left')

        # Right side of topbar
        topbar_right = tk.Frame(topbar, bg=BG_SECONDARY)
        topbar_right.pack(side='right', padx=12)

        # Export log button (kept from original)
        self.export_btn = AdaptiveButton(
            topbar_right, text="↓ Export Log",
            command=self.export_log,
            bg=BG_TERTIARY, fg=FG_SECONDARY,
            activebackground=BORDER_COLOR, activeforeground=FG_PRIMARY,
            relief='flat', padx=10, pady=4,
            font=('Segoe UI', 9), cursor='hand2'
        )
        self.export_btn.pack(side='right', padx=(6, 0), pady=8)
        Tooltip(self.export_btn, 'Export Log — save the current log output to a .txt file')

        # Check-for-updates link
        link_btn = tk.Label(topbar_right, text="📘 Updates",
                            bg=BG_SECONDARY, fg=ACCENT_BLUE,
                            font=('Segoe UI', 9, 'underline'), cursor='hand2')
        link_btn.pack(side='right', padx=(0, 4), pady=10)
        link_btn.bind('<Button-1>', lambda e: webbrowser.open(APP_URL))
        Tooltip(link_btn, f'Check for updates — opens {APP_URL}')

        # ── STATUS BAR ────────────────────────────────────────────────────────
        # Reserved FIRST (pinned via side='bottom'), before the flexible
        # content area below is packed with expand=True. Pack carves cavity
        # space in call order, so anything packed last is first to get
        # clipped when total content doesn't fit the window — the status
        # bar (fleet health, disk space, uptime ticker) is too useful to
        # risk losing that way, regardless of platform/screen size.
        tk.Frame(main_area, bg=BORDER_COLOR, height=1).pack(fill='x', side='bottom')
        self.status_frame = tk.Frame(main_area, bg=BG_SECONDARY, height=28)
        self.status_frame.pack(side='bottom', fill='x')
        self.status_frame.pack_propagate(False)

        self.status_label = tk.Label(
            self.status_frame,
            text="Initializing...",
            bg=BG_SECONDARY, fg=ACCENT_BLUE,
            font=('Segoe UI', 9)
        )
        self.status_label.pack(side='left', padx=14, pady=4)

        # ── CONTENT AREA ─────────────────────────────────────────────────────
        content = tk.Frame(main_area, bg=BG_PRIMARY)
        content.pack(fill='both', expand=True, padx=14, pady=10)

        # ── SECTION LABEL HELPER ─────────────────────────────────────────────
        def section_label(parent, text):
            tk.Label(parent, text=text.upper(),
                     bg=BG_PRIMARY, fg=FG_TERTIARY,
                     font=('Segoe UI', 8)).pack(anchor='w', pady=(0, 4))

        # ── VERTICAL SPLITTER (Fleet table top, Log+Metrics bottom) ─────────
        vpane = ttk.PanedWindow(content, orient='vertical')
        vpane.pack(fill='both', expand=True)

        # ── TOP PANE: Fleet table ─────────────────────────────────────────────
        top_pane = tk.Frame(vpane, bg=BG_PRIMARY)
        vpane.add(top_pane, weight=1)

        section_label(top_pane, 'Fleet')

        table_card = tk.Frame(top_pane, bg=BORDER_COLOR)
        table_card.pack(fill='both', expand=True, pady=(0, 4))
        table_inner = tk.Frame(table_card, bg=BG_TERTIARY)
        table_inner.pack(fill='both', expand=True, padx=1, pady=1)

        columns = ("Alias", "Status", "Pending Updates", "Uptime",
                   "Disk Space Free", "Last Update", "Last Snapshot Created")
        self.pc_list_view = ttk.Treeview(table_inner, columns=columns,
                                          show='headings', selectmode='extended',
                                          style='Custom.Treeview')

        width_map = {
            "Alias": 140, "Status": 90, "Pending Updates": 105, "Uptime": 90,
            "Disk Space Free": 115, "Last Update": 165, "Last Snapshot Created": 165
        }
        for col in columns:
            self.pc_list_view.heading(col, text=col)
            self.pc_list_view.column(col, width=width_map.get(col, 100), anchor='center')

        vsb = ttk.Scrollbar(table_inner, orient="vertical",
                            command=self.pc_list_view.yview)
        vsb.pack(side='right', fill='y')
        self.pc_list_view.configure(yscrollcommand=vsb.set)
        self.pc_list_view.pack(side='left', fill='both', expand=True)

        self.pc_list_view.bind('<<TreeviewSelect>>', self.on_selection_changed)
        self.pc_list_view.bind('<Button-1>', self.on_treeview_click)

        # ── BOTTOM PANE: Log + Metrics ────────────────────────────────────────
        bottom_pane = tk.Frame(vpane, bg=BG_PRIMARY)
        vpane.add(bottom_pane, weight=1)

        # Log pane (left, expanding)
        log_pane = tk.Frame(bottom_pane, bg=BG_PRIMARY)
        log_pane.pack(side='left', fill='both', expand=True)

        section_label(log_pane, 'Log output')
        log_card = tk.Frame(log_pane, bg=BORDER_COLOR)
        log_card.pack(fill='both', expand=True)
        log_inner = tk.Frame(log_card, bg=BG_LOG)
        log_inner.pack(fill='both', expand=True, padx=1, pady=1)

        self.log_view = tk.Text(
            log_inner, state='disabled', wrap='none',
            bg=BG_LOG, fg=FG_PRIMARY,
            insertbackground=FG_PRIMARY,
            relief='flat', bd=0,
            font=('Consolas', 10),
            padx=10, pady=8
        )
        log_vsb = ttk.Scrollbar(log_inner, orient="vertical",
                                command=self.log_view.yview)
        self.log_view.configure(yscrollcommand=log_vsb.set)
        log_vsb.pack(side='right', fill='y')
        self.log_view.pack(side='left', fill='both', expand=True)
        self._setup_log_tags()

        # Metrics column (right, fixed width)
        tk.Frame(bottom_pane, bg=BORDER_COLOR, width=1).pack(
            side='left', fill='y', padx=(10, 0))

        metrics_col = tk.Frame(bottom_pane, bg=BG_PRIMARY, width=162)
        metrics_col.pack(side='left', fill='y', padx=(8, 0))
        metrics_col.pack_propagate(False)

        section_label(metrics_col, 'Fleet metrics')

        def _metric_card(parent, label_text):
            card = tk.Frame(parent, bg=BG_TERTIARY, padx=10, pady=8)
            card.pack(fill='x', pady=(0, 6))
            tk.Label(card, text=label_text,
                     bg=BG_TERTIARY, fg=FG_SECONDARY,
                     font=('Segoe UI', 8)).pack(anchor='w')
            val_lbl = tk.Label(card, text='—',
                               bg=BG_TERTIARY, fg=FG_PRIMARY,
                               font=('Segoe UI', 12),
                               wraplength=138, justify='left')
            val_lbl.pack(anchor='w')
            sub_lbl = tk.Label(card, text='',
                               bg=BG_TERTIARY, fg=FG_SECONDARY,
                               font=('Segoe UI', 8))
            sub_lbl.pack(anchor='w')
            return val_lbl, sub_lbl

        self._m_health_val, self._m_health_sub  = _metric_card(metrics_col, 'Fleet health')
        self._m_updates_val, self._m_updates_sub = _metric_card(metrics_col, 'Pending updates')
        self._m_tasks_val, self._m_tasks_sub     = _metric_card(metrics_col, 'Active tasks')
        self._m_uptime_val, self._m_uptime_sub   = _metric_card(metrics_col, 'Longest uptime')

        # ── STARTUP ───────────────────────────────────────────────────────────
        if not hasattr(self, 'status_bar_started') or not self.status_bar_started:
            self.status_label.config(
                text="📡 Waiting for initial fleet synchronization…",
                fg=ACCENT_BLUE)
            self.master.after(5000, self._update_scrolling_status)

        # Kick off first metric refresh
        self.master.after(3000, self._refresh_metric_cards)
    
    def _update_status_bar(self):
        """Updates the status bar text with the current number of active tasks."""
        if hasattr(self, 'status_label'):
            with self._active_actions_lock:
                count = self._active_actions
            if count > 0:
                self.status_label.config(text=f"⚙ Active tasks: {count}", fg=ACCENT_AMBER)
            else:
                self.status_label.config(text="✔ System idle", fg=FG_SECONDARY)
        self._refresh_metric_cards()

    def _refresh_metric_cards(self):
        """Updates the four metric cards in the right-hand column."""
        if not hasattr(self, '_m_health_val'):
            return
        data = self.pc_list_data

        # Fleet health
        total = len(data)
        if total:
            _bad = ('offline', 'shutdown', 'failed', 'connection error', 'timed out', 'unknown')
            online  = sum(1 for p in data
                          if not any(kw in str(p.get('status', '')).lower() for kw in _bad))
            pct     = int(online / total * 100)
            h_color = ACCENT_GREEN if pct >= 80 else ACCENT_AMBER if pct >= 50 else ACCENT_RED
            self._m_health_val.config(text=f"{pct}%", fg=h_color)
            self._m_health_sub.config(text=f"{online} of {total} online")
        else:
            self._m_health_val.config(text="—", fg=FG_PRIMARY)
            self._m_health_sub.config(text="No PCs registered")

        # Pending updates
        try:
            total_upd = sum(
                int(str(p.get('pending_updates', 0)).split()[0])
                for p in data
                if str(p.get('pending_updates', '0')).split()[0].isdigit()
            )
            u_color = ACCENT_RED if total_upd > 20 else ACCENT_AMBER if total_upd > 0 else ACCENT_GREEN
            self._m_updates_val.config(text=str(total_upd), fg=u_color)
            hosts = sum(1 for p in data
                        if str(p.get('pending_updates', '0')).split()[0].isdigit()
                        and int(str(p.get('pending_updates', '0')).split()[0]) > 0)
            self._m_updates_sub.config(text=f"across {hosts} host{'s' if hosts != 1 else ''}")
        except Exception:
            self._m_updates_val.config(text="—", fg=FG_PRIMARY)
            self._m_updates_sub.config(text="")

        # Active tasks
        with self._active_actions_lock:
            tasks = self._active_actions
        t_color = ACCENT_AMBER if tasks > 0 else FG_PRIMARY
        self._m_tasks_val.config(text=str(tasks), fg=t_color)
        self._m_tasks_sub.config(text="running now" if tasks else "idle")

        # Longest uptime
        try:
            uptime_str = self._get_longest_uptime(data)
            if uptime_str and uptime_str != 'N/A':
                parts = uptime_str.split('[')
                self._m_uptime_val.config(text=parts[0].strip(), fg=FG_PRIMARY)
                self._m_uptime_sub.config(text=parts[1].rstrip(']') if len(parts) > 1 else "")
            else:
                self._m_uptime_val.config(text="—", fg=FG_PRIMARY)
                self._m_uptime_sub.config(text="")
        except Exception:
            pass

        # Update topbar online/offline badges
        self._update_fleet_badges()

    def _update_fleet_badges(self):
        """Updates the ● N online / ● N offline badges in the topbar."""
        if not hasattr(self, '_badge_online'):
            return
        data = self.pc_list_data
        offline_kw = ('offline', 'shutdown', 'failed', 'connection error', 'timed out')
        offline = sum(1 for p in data
                      if any(kw in str(p.get('status', '')).lower() for kw in offline_kw))
        online  = len(data) - offline
        self._badge_online.config(text=f"● {online} online")
        self._badge_offline.config(
            text=f"● {offline} offline" if offline else "",
            fg=ACCENT_RED if offline else BG_SECONDARY
        )
    
    def _update_scrolling_status(self):
        """Cycles through interesting stats in the bottom status bar."""
        data = self.pc_list_data
        
        # 1. Base Message (The one you are currently stuck on)
        total_pcs = len(data) if data else 0
        messages = [("✅ Server Fleet Status", f"{total_pcs} Systems Managed")]

        if data:
            # 2. Try to add each stat individually. 
            # If one helper function fails, the others will still show.
            try:
                total_pending = sum(int(pc[7]) for pc in data if str(pc[7]).isdigit())
                messages.append(("📦 Pending Updates", f"{total_pending} Updates Across Fleet"))
            except: pass

            try: messages.append(("⏱️ Longest Uptime", self._get_longest_uptime(data)))
            except: pass

            try: messages.append(("💾 Lowest Disk Space", self._get_lowest_disk(data)))
            except: pass

            try: messages.append(("📸 Oldest Snapshot", self._get_oldest_snapshot(data)))
            except: pass

            try: messages.append(("✅ Fleet Health", self._get_fleet_health(data)))
            except: pass

            try: messages.append(("🕐 Last Checked", self._get_last_checked(data)))
            except: pass

            try: messages.append(("🔺 Most Updates", self._get_most_updates_pending(data)))
            except: pass

            try: messages.append(("⚡ Last Action", self._get_last_action()))
            except: pass

            try:
                offlines = [pc for pc in data if "Offline" in str(pc[5])]
                if offlines:
                    messages.append(("🔴 Offline PCs", f"{len(offlines)} Systems Unreachable"))
            except: pass

        # 3. Logic to toggle the index
        if not hasattr(self, 'current_status_index'):
            self.current_status_index = 0
            
        self.current_status_index = (self.current_status_index + 1) % len(messages)
        
        # 4. Display the selected message
        label_title, label_text = messages[self.current_status_index]
        self.status_label.config(text=f"{label_title}: {label_text}", fg=ACCENT_BLUE)
        
        # 5. RE-SCHEDULE the next toggle (5 seconds)
        self._scrolling_timer_id = self.master.after(5000, self._update_scrolling_status)

    def _run_lightweight_watchdog(self):
        """Checks PC connectivity every 30s and auto-recovers if back online."""
        import socket
        def check_task():
            try:
                pcs_to_check = list(self.pc_list_data) 
                
                for pc_info in pcs_to_check:
                    pc_id = pc_info.get('id')
                    host = pc_info.get('hostname')
                    
                    if not pc_id or not host or pc_id in self.active_pcs:
                        continue

                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2)
                    try:
                        result = sock.connect_ex((host, 22))
                        last_known_status = str(pc_info.get('status', '')).upper()

                        if result == 0:
                            # --- RECOVERY LOGIC ---
                            if "OFFLINE" in last_known_status:
                                self.log_message(f"[RECOVERY] {pc_info.get('alias')} is back online. Rechecking ALL PC's")
                                # Mark as Recovering to prevent the watchdog from triggering again 
                                # while the SSH thread is still running
                                pc_info['status'] = "RECOVERING"
                                
                                # Trigger the SSH thread for this specific PC
                                self.selected_pc_ids = [pc_id]
                                self.master.after(0, lambda: self.on_refresh_clicked(None))
                        else:
                            # --- FAILURE LOGIC ---
                            if "OFFLINE" not in last_known_status:
                                self.log_message(f"[LOST] Connection to {pc_info.get('alias')} ({host}) failed. Marking as OFFLINE.")
                                
                                timestamp = datetime.now().strftime("%H:%M:%S")
                                pc_info['status'] = "OFFLINE"
                                
                                # This updates the actual grid/table view
                                self.master.after(0, lambda p=pc_id, t=timestamp: 
                                    self._update_pc_row_data(p, "OFFLINE", t, "N/A", "N/A", "N/A"))

                            # Nudge sleeping PCs on our own, without waiting for the user
                            # to trigger a manual action. Fire-and-forget (wait=False) —
                            # this loop checks every PC sequentially every 30s, so we
                            # can't afford to block here waiting for a boot. The 60s
                            # cooldown inside _wake_if_needed means this only actually
                            # sends a packet roughly once every other cycle per PC, not
                            # every single 30s pass.
                            connect_host = host if '.' in host else f"{host}.local"
                            self._wake_if_needed(pc_info, connect_host, wait=False)
                    except Exception:
                        pass
                    finally:
                        sock.close()
            except Exception:
                pass
            finally:
                # Always reschedule, even if something unexpected threw above
                if self.master.winfo_exists():
                    self.master.after(30000, self._run_lightweight_watchdog)

        threading.Thread(target=check_task, daemon=True).start()  

    def _setup_treeview_tags(self):
        """Sets up the Treeview tags for coloring rows based on PC alias, using the log colors."""
        for alias, color_hex in self.pc_colors_map.items():
            tag_name = f'pc_color_{alias}'
            self.pc_list_view.tag_configure(tag_name, foreground=color_hex)

    def _setup_log_tags(self):
        for tag in list(self.log_view.tag_names()):
            if tag in self.pc_colors_map:
                try:
                    self.log_view.tag_delete(tag)
                except tk.TclError:
                    pass

        for pc_alias, color_hex in self.pc_colors_map.items():
            self.log_view.tag_config(pc_alias, foreground=color_hex)

        self.log_view.tag_config('default', foreground=FG_PRIMARY)
        self.log_view.tag_config('timestamp', foreground=FG_TERTIARY)

    def _on_closing(self):
        self.log_message("[INFO] Application closed.")
        self.master.destroy()

    def log_message(self, message, tag_override=None):
        """
        Appends a message to the log_view.
        tag_override is used if provided, otherwise it tries to determine the tag from the message content.
        """
        def append():
            now = datetime.now().strftime("[%H:%M:%S]")
            self.log_view.config(state='normal')

            at_bottom = self.log_view.yview()[1] > 0.9

            # Prioritize the explicitly provided tag
            tag = tag_override if tag_override else 'default'
            
            # 2. If no override is provided, use the old string parsing logic
            if not tag_override and hasattr(self, 'pc_colors_map'):
                for pc_alias in self.pc_colors_map.keys():
                    if f" {pc_alias} " in message or f"({pc_alias})" in message or f" {pc_alias}:" in message:
                        tag = pc_alias
                        break

            self.log_view.insert('end', f"{now} {message}\n", tag)

            if at_bottom:
                self.log_view.see('end')

            self.log_view.config(state='disabled')

        self.master.after(0, append)

    def _get_current_time_str(self, mask="%Y-%m-%d %H:%M:%S"):
        return datetime.now().strftime(mask)

    def load_pc_data(self):
        """Refreshes the UI list while maintaining color coding and database safety."""
        # We build into a temporary list first. 
        # This prevents the status bar loop from seeing an empty list while we work.
        temp_list = []
        
        self.pc_list_view.delete(*self.pc_list_view.get_children())
        self.pc_colors_map.clear()

        raw_pcs = self.db_manager.get_all_pcs()

        # Build the color map
        unique_aliases = sorted(list(set(row[4] for row in raw_pcs if len(row) > 4)))
        palette_size = len(PC_COLOR_PALETTE)
        for i, alias in enumerate(unique_aliases):
            self.pc_colors_map[alias] = PC_COLOR_PALETTE[i % palette_size]

        # Setup UI tags
        self._setup_log_tags()
        self._setup_treeview_tags()

        # Handle dynamic height
        num_pcs = len(raw_pcs)
        treeview_height = max(4, min(15, num_pcs))
        self.pc_list_view.config(height=treeview_height)

        # 4. Insert data into the UI and our temporary list
        for idx, row in enumerate(raw_pcs):
            if len(row) < 7:
                continue
                
            pc_id, host, user, enc, alias, status, last = row[:7]
            pending = row[7] if len(row) > 7 else 0
            uptime = row[8] if len(row) > 8 else 'N/A'
            disk_free = row[9] if len(row) > 9 else 'N/A'
            mac_address = row[10] if len(row) > 10 else ''

            last_snapshot_date = self.db_manager.get_latest_snapshot_timestamp(pc_id)

            data_entry = {
                "id": pc_id, "hostname": host, "username": user, "password_encrypted": enc,
                "alias": alias, "status": status, "last_update": last,
                "pending_updates": pending, "index": idx,
                "uptime": uptime, "disk_free": disk_free,
                "mac_address": mac_address or '',
                "last_snapshot_date": last_snapshot_date
            }
            
            # Store in the temporary list
            temp_list.append(data_entry)

            # UI Insertion
            tag = f'pc_color_{alias}'
            tree_values = (alias, status, pending, uptime, disk_free, last, last_snapshot_date)
            self.pc_list_view.insert('', 'end', iid=pc_id, values=tree_values, tags=(tag,))
            
        # ATOMIC SWAP: Update the real list in one go.
        # The status bar loop will now always see a complete list of data.
        self.pc_list_data = temp_list
        self._update_fleet_badges()
        self._refresh_metric_cards()

    #  Active-action counter — incremented/decremented around SSH work
    def _action_start(self):
        with self._active_actions_lock:
            self._active_actions += 1

    def _action_end(self):
        with self._active_actions_lock:
            self._active_actions = max(0, self._active_actions - 1)

            # ✅ When ALL actions are finished
        if self._active_actions == 0:
            self._fleet_sync_in_progress = False

    def _is_busy(self):
        """
        Checks if the manager is currently performing any background SSH tasks
        or a full fleet synchronization.
        """
        # Check if any individual SSH threads are currently active (e.g., Run Command, Send File)
        with self._active_actions_lock:
            active_tasks = self._active_actions > 0
            
        # Returns True if manual tasks are running OR a global refresh is in progress
        return active_tasks or self._fleet_sync_in_progress

    def _rotate_fleet_metrics(self, data):
        """Handles the actual message switching once all data is fresh."""
        total_pcs = len(data)
        total_pending = sum(
            int(str(pc.get('pending_updates', 0)).split()[0])
            for pc in data
            if str(pc.get('pending_updates', '0')).split()[0].isdigit()
        )
        metrics = [
            ("🖥️ Server Fleet Size",        f"{total_pcs} Systems Online"),
            ("📦 Pending Updates",    f"{total_pending} Updates Across Server Fleet"),
            ("⏱️ Longest Uptime",     self._get_longest_uptime(data)),
            ("💾 Lowest Disk Space",  self._get_lowest_disk(data)),
            ("📸 Oldest Snapshot",    self._get_oldest_snapshot(data)),
            ("✅ Server Fleet Health",        self._get_fleet_health(data)),
            ("🕐 Last Checked",       self._get_last_checked(data)),
            ("🔺 Most Updates",       self._get_most_updates_pending(data)),
            ("⚡ Last Action",        self._get_last_action()),
            ("🔴 Offline PCs",        self._get_offline_pcs(data)),
            ("🔄 Recently Rebooted",  self._get_recently_rebooted(data)),
        ]

        # Filter out any metrics that have nothing useful to show
        metrics = [(l, v) for l, v in metrics if v not in (None, '', 'N/A', 'None')]

        if not hasattr(self, 'current_status_index'):
            self.current_status_index = 0
        self.current_status_index = (self.current_status_index + 1) % len(metrics)
        label, value = metrics[self.current_status_index]

        # Time Since Last Check: colour the label amber/red if data is stale
        if label == "🕐 Last Checked" and "ago" in value:
            try:
                # Extract the oldest last_update to decide staleness colour
                oldest_mins = self._oldest_last_update_minutes(data)
                fg = "#ff8c00" if oldest_mins >= 60 else "#5bc0de"
                if oldest_mins >= 240:
                    fg = "#d13438"
            except Exception:
                fg = ACCENT_BLUE
        else:
            fg = ACCENT_BLUE

        self.status_label.config(text=f"{label}: {value}", fg=fg)

    #  Helper: record the last user-initiated action  
    def _record_last_action(self, label):
        """Call this whenever the user triggers an action."""
        self._last_action_label = label
        self._last_action_time  = datetime.now()

    #  New metric helpers 
    def _oldest_last_update_minutes(self, data):
        """Returns how many minutes ago the oldest last_update timestamp was."""
        oldest = None
        for pc in data:
            ts = pc.get('last_update', 'N/A')
            if not ts or ts == 'N/A':
                continue
            try:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                if oldest is None or dt < oldest:
                    oldest = dt
            except ValueError:
                continue
        if oldest is None:
            return 0
        return int((datetime.now() - oldest).total_seconds() / 60)

    def _get_last_checked(self, data):
        """Returns a human-readable string of how long ago the oldest PC was last checked."""
        mins = self._oldest_last_update_minutes(data)
        if mins == 0:
            return "Just now"
        elif mins < 60:
            return f"{mins} min ago"
        elif mins < 120:
            return f"1 hr ago  ⚠️ consider refreshing"
        elif mins < 1440:
            hrs = mins // 60
            return f"{hrs} hrs ago  ⚠️ data may be stale"
        else:
            days = mins // 1440
            return f"{days} day(s) ago  ⚠️ data is stale"

    def _get_most_updates_pending(self, data):
        """Returns the PC with the highest pending update count."""
        best_alias, best_count = None, 0
        for pc in data:
            raw = str(pc.get('pending_updates', '0')).split()[0]
            count = int(raw) if raw.isdigit() else 0
            if count > best_count:
                best_count, best_alias = count, pc.get('alias', '?')
        if best_count == 0:
            return "All PCs up to date"
        return f"{best_count} pending  [{best_alias}]"

    def _get_last_action(self):
        """Returns the last user action and how long ago it was."""
        if not hasattr(self, '_last_action_label'):
            return "N/A"
        mins = int((datetime.now() - self._last_action_time).total_seconds() / 60)
        time_str = self._last_action_time.strftime("%H:%M")
        if mins < 1:
            ago = "just now"
        elif mins < 60:
            ago = f"{mins} min ago"
        else:
            ago = f"{mins // 60} hr ago"
        return f"{self._last_action_label}  [{time_str}]  ({ago})"

    def _get_offline_pcs(self, data):
        """Lists PCs that are currently unreachable / offline."""
        offline_keywords = ['offline', 'failed: ', 'authentication failed',
                            'connection error', 'timed out', 'ssh error']
        offline = [pc.get('alias', '?') for pc in data
                   if any(kw in str(pc.get('status', '')).lower() for kw in offline_keywords)]
        if not offline:
            return "All PCs reachable"
        return f"{len(offline)} offline: {', '.join(offline)}"

    def _get_recently_rebooted(self, data):
        """Lists PCs that have rebooted recently (within the last 24 hrs)."""
        rebooted = []
        cutoff = datetime.now().timestamp() - 86400 
        for pc in data:
            if 'rebooted' not in str(pc.get('status', '')).lower():
                continue
            ts = pc.get('last_update', 'N/A')
            try:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                if dt.timestamp() >= cutoff:
                    rebooted.append(pc.get('alias', '?'))
            except ValueError:
                continue
        if not rebooted:
            return None 
        return f"{', '.join(rebooted)}"

    def _uptime_to_minutes(self, uptime_str):
        """Converts an uptime string like '2 days, 3 hours, 5 minutes' to total minutes."""
        s = str(uptime_str).lower()
        days  = int(m.group(1)) if (m := re.search(r'(\d+)\s+day',    s)) else 0
        hours = int(m.group(1)) if (m := re.search(r'(\d+)\s+hour',   s)) else 0
        mins  = int(m.group(1)) if (m := re.search(r'(\d+)\s+min',    s)) else 0
        return days * 1440 + hours * 60 + mins

    def _get_longest_uptime(self, data):
        """Returns the uptime and alias of the PC that has been up the longest."""
        valid = [(pc.get('alias', '?'), pc.get('uptime', 'N/A'))
                 for pc in data if pc.get('uptime', 'N/A') not in ('N/A', '', None)]
        if not valid:
            return "N/A"
        alias, uptime = max(valid, key=lambda x: self._uptime_to_minutes(x[1]))
        return f"{uptime}  [{alias}]"

    def _disk_free_to_mb(self, disk_str):
        """Converts a disk-free string (e.g. '15G', '500M') to MB for comparison."""
        s = str(disk_str).strip().upper()
        m = re.match(r'([\d.]+)\s*([TGMK]?)', s)
        if not m:
            return float('inf') 
        value, unit = float(m.group(1)), m.group(2)
        return value * {'T': 1_048_576, 'G': 1024, 'M': 1, 'K': 0.001}.get(unit, 1)

    def _get_lowest_disk(self, data):
        """Returns the free disk space and alias of the PC with the least free space."""
        valid = [(pc.get('alias', '?'), pc.get('disk_free', 'N/A'))
                 for pc in data if pc.get('disk_free', 'N/A') not in ('N/A', '', None)]
        if not valid:
            return "N/A"
        alias, disk = min(valid, key=lambda x: self._disk_free_to_mb(x[1]))
        return f"{disk} free  [{alias}]"

    def _get_oldest_snapshot(self, data):
        """Finds the oldest snapshot across all PCs and returns its age and alias.
        If any PC has no snapshot at all, that is flagged as a separate note."""
        oldest_alias = None
        oldest_dt = None

        for pc in data:
            ts = pc.get('last_snapshot_date', 'N/A')
            if not ts or ts == 'N/A':
                continue
            try:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                if oldest_dt is None or dt < oldest_dt:
                    oldest_dt = dt
                    oldest_alias = pc.get('alias', '?')
            except ValueError:
                continue

        # If no PC has any snapshot at all
        if oldest_dt is None:
            return "No snapshots taken yet"

        age_days = (datetime.now() - oldest_dt).days
        if age_days == 0:
            age_str = "Today"
        elif age_days == 1:
            age_str = "Yesterday"
        else:
            age_str = f"{age_days} days ago"

        return f"{age_str}  [{oldest_alias}]"

    def _get_fleet_health(self, data):
        """Summarises overall Server fleet health: counts OK, failed, and offline PCs."""
        ok = sum(1 for pc in data if str(pc.get('status', '')).lower() in ('ok', 'ok (rebooted)', 'at snapshot'))
        failed = sum(1 for pc in data if 'fail' in str(pc.get('status', '')).lower())
        offline = sum(1 for pc in data if 'offline' in str(pc.get('status', '')).lower()
                                       or 'shutdown' in str(pc.get('status', '')).lower())
        parts = []
        if ok:      parts.append(f"✅ {ok} OK")
        if failed:  parts.append(f"❌ {failed} Failed")
        if offline: parts.append(f"🔴 {offline} Offline")
        return "  ".join(parts) if parts else "All Systems Nominal"

    def on_selection_changed(self, event):
        selected_iids = self.pc_list_view.selection()
        self.selected_pc_ids = [int(iid) for iid in selected_iids]

    def on_treeview_click(self, event):
        """Allow clicking on a selected item to deselect it."""
        region = self.pc_list_view.identify_region(event.x, event.y)
        if region == "cell":
            item = self.pc_list_view.identify_row(event.y)
            if item in self.pc_list_view.selection():
                # If clicking on an already selected item, deselect it
                self.pc_list_view.selection_remove(item)
                return "break"  # Prevent default selection behavior

    def _show_confirmation_dialog(self, action, label):
        target_ids = self.selected_pc_ids

        if not target_ids:
            if action == "update" and self._show_custom_dialog(
                "Confirm Update ALL",
                "WARNING: No PC is selected.",
                "Do you want to run 'apt update' and 'apt upgrade -y' on *ALL* listed PCs?",
                warning=True
            ):
                pc_list = self.pc_list_data
                if pc_list:
                    self.log_message(f"[INFO] Running Update ALL on {len(pc_list)} PCs.")
                    self.run_mass_action_thread(pc_list, "update")
                else:
                    self.log_message("[ERROR] No PCs available in the list to update.")
                return

            self.log_message(f"[ERROR] Select a PC first to {action.replace('_', ' ').lower()}.")
            return

        dialog_title = f"Confirm {label}"
        sec = ""
        highlight_text = ""

        if action == "create_snapshot":
            if len(target_ids) != 1:
                self.log_message(f"[ERROR] Snapshot creation requires selecting exactly ONE PC.")
                return
            pc = next((p for p in self.pc_list_data if p["id"] == target_ids[0]), None)
            alias = pc["alias"] if pc else f"ID {target_ids[0]}"
            highlight_text = alias.upper()
            msg = f"Confirm new snapshot CREATION for"
            sec = "A snapshot of the installed software list will be saved to the database, replacing any previous snapshot for this PC."
            dialog_title = "Confirm Software Snapshot Creation"

        elif action == "revert":
            if len(target_ids) > 1:
                self.log_message(f"[ERROR] Reverting a snapshot can only be performed on a single PC at a time.")
                return
            pc = next((p for p in self.pc_list_data if p["id"] == target_ids[0]), None)
            alias = pc["alias"] if pc else f"ID {target_ids[0]}"
            highlight_text = alias.upper()
            msg = f"Confirm REVERT to snapshot for"
            sec = "WARNING: This action will remove all software installed since the last snapshot. This cannot be undone."
            dialog_title = "Confirm Software Snapshot Revert"

        else:
            if len(target_ids) > 1:
                alias = f"{len(target_ids)} PC(s)"
                highlight_text = alias
                op_name = label.lower()
                msg = f"Are you sure you want to {op_name}"
                if action == "shutdown":
                    sec = "This will power off all selected PCs. Monitoring will track loss of connection."
                elif action == "reboot":
                    sec = "This will restart all selected PCs. Monitoring will track connection loss and recovery."
                elif action == "update":
                    sec = "This will run 'apt update' and 'apt upgrade -y' on all selected PCs."
                elif action == "delete":
                    sec = "This removes them from the list only."
            else:
                pc = next((p for p in self.pc_list_data if p["id"] == target_ids[0]), None)
                alias = pc["alias"] if pc else f"ID {target_ids[0]}"
                highlight_text = alias.upper()
                op_name = label.lower().replace(" run", "")
                msg_map = {
                    "delete": (f"Are you sure you want to {op_name}", "This removes it from the list only."),
                    "shutdown": (f"Are you sure you want to {op_name}", "Monitoring will track loss of connection."),
                    "reboot": (f"Are you sure you want to {op_name}", "Monitoring will track connection loss and recovery."),
                    "update": (f"Are you sure you want to update", "This will run 'apt update' and 'apt upgrade -y'.")
                }
                msg, sec = msg_map.get(action, (f"Confirm {op_name} on", ""))

        warning = action in ["delete", "reboot", "shutdown", "revert", "clone"]

        if self._show_custom_dialog(dialog_title, msg, sec, highlight_text, warning):
            if action == "delete":
                self._record_last_action(f"Delete PC")
                self.on_delete_pc_clicked(target_ids)
            elif action == "clone":
                self._record_last_action(f"Clone PC")
                self.run_clone_action(target_ids)
            elif action == "revert":
                self._record_last_action(f"Revert Snapshot")
                self.run_revert_action(target_ids)
            elif action == "create_snapshot":
                self._record_last_action(f"Create Snapshot")
                self.run_create_snapshot_action(target_ids)
            else:
                self._record_last_action(label)
                pc_list = [p for p in self.pc_list_data if p["id"] in target_ids]
                self.run_mass_action_thread(pc_list, action)
        else:
            self.log_message(f"[INFO] {action.replace('_', ' ').capitalize()} cancelled.")

    def _show_custom_dialog(self, title, message, secondary="", highlight="", warning=False):
        """Custom styled confirmation dialog with dark theme."""
        dialog = tk.Toplevel(self.master)
        dialog.title(title)
        dialog.transient(self.master)
        dialog.config(bg=BG_PRIMARY)
        dialog.resizable(False, False)

        result = {'value': False}

        # Main frame
        main_frame = tk.Frame(dialog, bg=BG_PRIMARY)
        main_frame.pack(fill='both', expand=True, padx=25, pady=20)

        # Icon (warning or info)
        icon_label = tk.Label(main_frame,
                             text="⚠️" if warning else "ℹ️",
                             bg=BG_PRIMARY,
                             font=('Segoe UI', 32))
        icon_label.grid(row=0, column=0, rowspan=3, padx=(0, 15), sticky='n')

        # Message frame
        msg_frame = tk.Frame(main_frame, bg=BG_PRIMARY)
        msg_frame.grid(row=0, column=1, sticky='w')

        # Main message
        msg_label = tk.Label(msg_frame,
                            text=message,
                            bg=BG_PRIMARY,
                            fg=FG_PRIMARY,
                            font=('Segoe UI', 11),
                            justify='left')
        msg_label.pack(anchor='w')

        # Highlighted PC name
        if highlight:
            highlight_label = tk.Label(msg_frame,
                                      text=highlight,
                                      bg=BG_PRIMARY,
                                      fg=BTN_WARNING if warning else ACCENT_BLUE,
                                      font=('Segoe UI', 16, 'bold'),
                                      justify='left')
            highlight_label.pack(anchor='w', pady=(5, 5))

        # Secondary message
        if secondary:
            sec_label = tk.Label(msg_frame,
                               text=secondary,
                               bg=BG_PRIMARY,
                               fg=FG_SECONDARY,
                               font=('Segoe UI', 9),
                               justify='left',
                               wraplength=350)
            sec_label.pack(anchor='w', pady=(10, 0))

        # Button frame
        btn_frame = tk.Frame(main_frame, bg=BG_PRIMARY)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(20, 0), sticky='e')

        def on_yes():
            result['value'] = True
            dialog.destroy()

        def on_no():
            result['value'] = False
            dialog.destroy()

        # No button
        no_btn = AdaptiveButton(btn_frame,
                          text="No",
                          command=on_no,
                          bg=BG_TERTIARY,
                          fg=FG_PRIMARY,
                          font=('Segoe UI', 10),
                          relief='flat',
                          bd=0,
                          padx=25,
                          pady=8,
                          cursor='hand2')
        no_btn.pack(side='right', padx=(10, 0))

        # Yes button
        yes_btn = AdaptiveButton(btn_frame,
                           text="Yes",
                           command=on_yes,
                           bg=BTN_DANGER if warning else BTN_PRIMARY,
                           fg='white',
                           font=('Segoe UI', 10, 'bold'),
                           relief='flat',
                           bd=0,
                           padx=25,
                           pady=8,
                           cursor='hand2')
        yes_btn.pack(side='right')

        # Hover effects
        yes_btn.bind('<Enter>', lambda e: yes_btn.config(bg=BTN_DANGER_HOVER if warning else BTN_PRIMARY_HOVER))
        yes_btn.bind('<Leave>', lambda e: yes_btn.config(bg=BTN_DANGER if warning else BTN_PRIMARY))
        no_btn.bind('<Enter>', lambda e: no_btn.config(bg=BG_SECONDARY))
        no_btn.bind('<Leave>', lambda e: no_btn.config(bg=BG_TERTIARY))

        # Center dialog
        dialog.update()
        dialog.grab_set()
        width = dialog.winfo_width()
        height = dialog.winfo_height()
        x = self.master.winfo_x() + self.master.winfo_width() // 2 - width // 2
        y = self.master.winfo_y() + self.master.winfo_height() // 2 - height // 2
        dialog.geometry(f'+{x}+{y}')

        dialog.wait_window()
        return result['value']

    def on_add_pc_clicked(self, event=None):
        dialog = AddPCDialog(self.master)
        result, data = dialog.show()

        if result == "ok" and data['hostname'] and data['username'] and data['password']:
            try:
                encrypted_password = self.encryption_util.encrypt(data['password'])
                mac_address = data.get('mac_address', '')
                new_id = self.db_manager.add_pc(data['hostname'], data['username'], encrypted_password, data['alias'], mac_address)
                if mac_address:
                    self.log_message(f"[INFO] {data['alias']}: MAC address discovered via network scan ({mac_address}) — Wake-on-LAN ready.")
                self.log_message(f"[INFO] Added new PC: {data['alias']}")
                self._record_last_action(f"Register PC: {data['alias']}")
                self.load_pc_data()
                # Check just the new PC without disturbing any ongoing actions
                self.master.after(500, lambda: self._check_new_pc(new_id))
            except Exception as e:
                self.log_message(f"[ERROR] Failed to add PC: {e}")
                self.master.focus()

    def _check_new_pc(self, pc_id):
        """Runs a status check on a single newly added or edited PC in its own thread.
        Safe to call even if other actions are in progress — it doesn't touch the Server fleet sync flag."""
        pc_info = next((p for p in self.pc_list_data if p["id"] == pc_id), None)
        if not pc_info:
            return

        def worker():
            self._action_start()
            try:
                alias = pc_info["alias"]
                self.log_message(f"[INFO] Running initial check on {alias}...")
                pc_data = next((p for p in self.pc_list_data if p["id"] == pc_id), None)
                if pc_data:
                    pc_data['status'] = "In Progress"
                self.master.after(0, lambda: self._update_pc_row_status(pc_id, "In Progress", self._get_current_time_str()))

                success, _ = self._run_ssh_command(pc_id, pc_info, "echo OK")
                new_status = "OK" if success else "Offline"
                current_time = self._get_current_time_str()
                pending_updates, uptime, disk_free = "0", "N/A", "N/A"

                if success:
                    pending_updates = self._check_pending_updates_count(pc_info)
                    uptime, disk_free = self._check_status_data(pc_info)
                    self._fetch_and_store_mac(pc_info)

                self.db_manager.update_status(pc_id, new_status, current_time, pending_updates, uptime, disk_free)
                self.master.after(0, lambda: self._update_pc_row_data(pc_id, new_status, current_time, pending_updates, uptime, disk_free))
                self.log_message(f"[INFO] Initial check complete for {alias}: {new_status}")
            finally:
                self._action_end()

        threading.Thread(target=worker, daemon=True).start()

    def on_edit_pc_clicked(self, event=None):
        if len(self.selected_pc_ids) != 1:
            self.log_message("[ERROR] Select exactly ONE PC to edit.")
            return

        pc_id = self.selected_pc_ids[0]
        pc_info = next((p for p in self.pc_list_data if p["id"] == pc_id), None)

        if not pc_info:
            self.log_message(f"[ERROR] PC with ID {pc_id} not found.")
            return

        dialog = AddPCDialog(self.master, is_edit=True)
        dialog.entries['alias_entry'].delete(0, 'end')
        dialog.entries['alias_entry'].insert(0, pc_info['alias'])
        dialog.entries['hostname_entry'].delete(0, 'end')
        dialog.entries['hostname_entry'].insert(0, pc_info['hostname'])
        dialog.entries['username_entry'].delete(0, 'end')
        dialog.entries['username_entry'].insert(0, pc_info['username'])

        result, data = dialog.show()

        if result == "ok" and data['hostname'] and data['username']:
            new_password = data['password']
            if new_password and "(Leave blank to keep existing password)" not in new_password:
                encrypted_password = self.encryption_util.encrypt(new_password)
            else:
                encrypted_password = pc_info['password_encrypted']

            try:
                self.db_manager.update_pc(pc_id, data['hostname'], data['username'], encrypted_password, data['alias'])
                self.log_message(f"[INFO] Updated PC: {data['alias']}")
                self._record_last_action(f"Edit PC: {data['alias']}")
                self.load_pc_data()
                # Re-check this PC since hostname/credentials may have changed
                self.master.after(500, lambda: self._check_new_pc(pc_id))
            except Exception as e:
                self.log_message(f"[ERROR] Failed to update PC: {e}")
                self.master.focus()

    def on_wake_clicked(self, event=None):
        """
        Sends a Wake-on-LAN magic packet to every selected PC that has a MAC
        address on file. This doesn't open an SSH connection — it just
        broadcasts on the local network, so it works even for PCs that are
        currently powered off/asleep (which is the whole point).
        """
        if not self.selected_pc_ids:
            self.log_message("[ERROR] Select at least one PC to wake.")
            return

        targets = [p for p in self.pc_list_data if p["id"] in self.selected_pc_ids]
        no_mac = [p['alias'] for p in targets if not p.get('mac_address')]
        wakeable = [p for p in targets if p.get('mac_address')]

        if no_mac:
            self.log_message(
                f"[WARN] No MAC address on file for: {', '.join(no_mac)} — "
                f"run a status check on them first (or re-add via a network scan) "
                f"to capture it before Wake-on-LAN will work."
            )

        if not wakeable:
            return

        self._record_last_action(f"Wake-on-LAN → {', '.join(p['alias'] for p in wakeable)}")

        for pc in wakeable:
            success, msg = send_magic_packet(pc['mac_address'])
            if success:
                self.log_message(f"[WAKE] {pc['alias']}: Magic packet sent to {pc['mac_address']}")
            else:
                self.log_message(f"[ERROR] {pc['alias']}: Failed to send magic packet — {msg}")

        self.log_message(
            "[INFO] Wake-on-LAN packets sent. Note: the target PC must have "
            "WoL enabled in BIOS/UEFI and on its network interface, and be "
            "connected via Ethernet (Wi-Fi WoL is unreliable on most hardware)."
        )

    def on_delete_pc_clicked(self, target_ids):
        for pc_id in target_ids:
            try:
                alias = next((p['alias'] for p in self.pc_list_data if p["id"] == pc_id), str(pc_id))
                self.db_manager.delete_pc(pc_id)
                self.pc_list_data = [p for p in self.pc_list_data if p["id"] != pc_id]
                self.pc_list_view.delete(str(pc_id))
                self.log_message(f"[INFO] Removed {alias} from managed Server fleet.")
                self._record_last_action(f"Remove PC: {alias}")
                self.load_pc_data()
            except Exception as e:
                self.log_message(f"[ERROR] Failed to delete PC ID {pc_id}: {e}")
                self.master.focus()
    
    def export_log(self):
        """Saves the current content of the log window to a .txt file."""
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"remote_manager_log_{datetime.now().strftime('%Y-%m-%d')}.txt"
        )
        if file_path:
            try:
                # Get text from the log widget
                log_content = self.log_view.get("1.0", tk.END)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(f"--- Remote Linux Manager Log ---\n")
                    f.write(f"Exported on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write("-" * 30 + "\n\n")
                    f.write(log_content)
                messagebox.showinfo("Export Successful", f"Log saved to:\n{file_path}")
            except Exception as e:
                messagebox.showerror("Export Error", f"Could not save log: {e}")

    def on_deploy_software_clicked(self, event=None):
        dialog = DeploySoftwareDialog(self.master)
        result, packages = dialog.show()

        if result == "deploy" and packages:
            if not self.selected_pc_ids:
                pc_list = self.pc_list_data
                self.log_message(f"[INFO] No selection made. Deploying to ALL {len(pc_list)} PCs.")
            else:
                pc_list = [p for p in self.pc_list_data if p["id"] in self.selected_pc_ids]
                self.log_message(f"[INFO] Deploying to {len(pc_list)} selected PC(s).")

            if not pc_list:
                self.log_message("[ERROR] No PCs available in the list to deploy software.")
                return

            packages_str = ' '.join(packages)
            full_script = (
                f"DEBIAN_FRONTEND=noninteractive apt update && "
                f"DEBIAN_FRONTEND=noninteractive apt install -y {packages_str}"
            )
            command = f"sudo sh -c '{full_script}'"

            self._record_last_action(f"Deploy: {packages_str[:30]}")
            self.run_mass_action_thread(pc_list, "deploy", command=command)
            self.master.focus()

    def on_run_command_clicked(self, event=None):
        dialog = RunCommandDialog(self.master)
        result, command, use_sudo = dialog.show()

        if result == "run" and command:
            if not self.selected_pc_ids:
                pc_list = self.pc_list_data
                self.log_message(f"[INFO] No selection made. Running command on ALL {len(pc_list)} PCs.")
            else:
                pc_list = [p for p in self.pc_list_data if p["id"] in self.selected_pc_ids]
                self.log_message(f"[INFO] Running command on {len(pc_list)} selected PC(s).")

            if not pc_list:
                self.log_message("[ERROR] No PCs available in the list to run command.")
                return

            final_command = command
            if use_sudo:
                if 'sudo' not in command.lower():
                    final_command = f"sudo sh -c '{command}'"

            self.log_message(f"[CMD] Executing: {final_command}")

            self._record_last_action(f"Run Command")
            self.run_mass_action_thread(pc_list, "run_command", command=final_command)
            self.master.focus()

    def _can_connect(self, pc_info, timeout=2):
        host = pc_info["hostname"]
        user = pc_info["username"]
        connect_host = host if '.' in host else f"{host}.local"

        try:
            password = self.encryption_util.decrypt(pc_info["password_encrypted"])
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(connect_host, username=user, password=password, timeout=timeout)
            ssh.close()
            return True
        except Exception:
            return False

    def _wake_if_needed(self, pc_info, connect_host, wait=True):
        """
        Called right before we attempt an SSH connection. If the PC doesn't
        answer on port 22 but has a MAC address on file, we treat it as
        'asleep' rather than 'offline': send a Wake-on-LAN packet, and — if
        wait=True — pause here for it to boot before the caller's real SSH
        attempt proceeds. With wait=False the packet is still sent, but we
        return immediately; the PC will just show Offline this pass and
        pick up as OK on the next check once it's actually booted. This
        keeps a sequential fleet-wide sweep from stalling behind one PC
        that takes 30s to wake.

        Deliberately cheap for the common case — a PC that's already up
        answers the port probe immediately and this returns with no delay
        and no packet sent. Only PCs that are both (a) unreachable right now
        and (b) known to support WoL trigger anything further.
        """
        pc_id = pc_info["id"]
        alias = pc_info["alias"]
        mac = pc_info.get("mac_address")

        if is_tcp_port_open(connect_host, timeout=WOL_PORT_CHECK_TIMEOUT):
            return  # already up — nothing to do

        if not mac:
            return  # asleep or offline, but we have no way to wake it

        # Don't re-send if we already tried this PC very recently — protects
        # against hammering a PC that has a MAC on file but isn't actually
        # waking (bad BIOS/NIC config, unplugged Ethernet, etc.)
        with self._wake_lock:
            last_attempt = self._last_wake_attempt.get(pc_id)
            now = time.time()
            if last_attempt and (now - last_attempt) < WOL_RETRY_COOLDOWN_SECONDS:
                return
            self._last_wake_attempt[pc_id] = now

        success, msg = send_magic_packet(mac)
        if not success:
            self.log_message(f"[WAKE] {alias}: Failed to send magic packet — {msg}")
            return

        if not wait:
            self.log_message(f"[WAKE] {alias}: Not responding — sent Wake-on-LAN packet (will check again next pass, not blocking this sweep).")
            return

        self.log_message(f"[WAKE] {alias}: Not responding — sent Wake-on-LAN packet, waiting up to {WOL_BOOT_WAIT_SECONDS}s for it to boot...")

        waited = 0
        while waited < WOL_BOOT_WAIT_SECONDS:
            time.sleep(WOL_POLL_INTERVAL_SECONDS)
            waited += WOL_POLL_INTERVAL_SECONDS
            if is_tcp_port_open(connect_host, timeout=WOL_PORT_CHECK_TIMEOUT):
                self.log_message(f"[WAKE] {alias}: Back online after ~{waited}s.")
                return

        self.log_message(f"[WAKE] {alias}: Still not responding after {WOL_BOOT_WAIT_SECONDS}s — proceeding anyway (may report Offline).")

    def _reboot_monitor(self, pc_info, action):
        self._action_start()
        try:
            pc_id = pc_info["id"]
            alias = pc_info["alias"]
            target_cmd = "sudo reboot" if action == "reboot" else "sudo shutdown now"

            self.log_message(f"[TASK] Executing {action} command on {alias}...")
            
            # 1. Execute the initial command
            success, status = self._run_ssh_command(pc_id, pc_info, target_cmd)
            current_time = self._get_current_time_str()

            if not success:
                self.log_message(f"[FAIL] Initial {action} command failed on {alias}: {status}")
                return

            # Update status to Rebooting/Shutting Down
            monitoring_status = f"{action.capitalize()}ing..."
            self.log_message(f"[MONITOR] {alias} command sent. Status: {monitoring_status}")
            
            def safe_db_update(s, t, u, up, df):
                self.db_manager.update_status(pc_id, s, t, u, up, df)
                self._update_pc_row_data(pc_id, s, t, u, up, df)

            self.master.after(0, lambda: safe_db_update(monitoring_status, current_time, 0, 'N/A', 'N/A'))

            # Phase 1: Wait for pc to drop offline
            time.sleep(10) 
            self.log_message(f"[MONITOR] Waiting for {alias} to go offline...")

            offline_start = time.time()
            while self._can_connect(pc_info, timeout=2):
                time.sleep(10) 
                if time.time() - offline_start > 300: 
                    self.log_message(f"[WARN] {alias} did not go offline.")
                    return

            self.log_message(f"[MONITOR] {alias} is now offline.")

            if action == "shutdown":
                self.master.after(0, lambda: safe_db_update("Offline (Shutdown)", self._get_current_time_str(), 0, 'N/A', 'N/A'))
                return

            # Phase 2: Reboot polling
            self.log_message(f"[MONITOR] Polling for {alias} to come back online...")
            reconnect_start = time.time()
            
            while not self._can_connect(pc_info, timeout=2):
                time.sleep(10) 
                if time.time() - reconnect_start > 600: 
                    self.log_message(f"[CRITICAL] {alias} failed to reconnect.")
                    self.master.after(0, lambda: safe_db_update("Reboot Timeout", self._get_current_time_str(), 0, 'N/A', 'N/A'))
                    return

            # Phase 3: Refresh data
            # IMPORTANT: We fetch the data in this thread, but UPDATE the DB in the main thread
            self.log_message(f"[SUCCESS] {alias} is back online. Fetching fresh stats...")
            
            upd = self._check_pending_updates_count(pc_info)
            upt, df = self._check_status_data(pc_info)
            final_status = "OK (Rebooted)"
            finish_time = self._get_current_time_str()
            
            # Final thread-safe UI and DB update via the Main Thread
            self.master.after(0, lambda: safe_db_update(final_status, finish_time, upd, upt, df))
            self.log_message(f"[DONE] {alias} monitor finished.")

        except Exception as e:
            self.log_message(f"[ERROR] Monitor exception for {pc_info.get('alias')}: {str(e)}")
        finally:
            self._action_end()

    def run_mass_action_thread(self, pc_list, action, command=None):
        def action_worker():
            self._action_start()
            # Only raise the "Server Fleet Sync" flag for a full check_status sweep
            is_fleet_check = (action == "check_status") and (len(pc_list) == len(self.pc_list_data))
            
            if is_fleet_check:
                self._fleet_sync_in_progress = True

            try:
                # Mark ALL PCs as "In Progress" in pc_list_data BEFORE any SSH work begins.
                for pc in pc_list:
                    if action not in ("reboot", "shutdown"):
                        pc_id = pc["id"]
                        current_time = self._get_current_time_str()
                        pc_data = next((p for p in self.pc_list_data if p["id"] == pc_id), None)
                        if pc_data:
                            pc_data['status'] = "In Progress"
                        self.master.after(0, lambda p=pc_id, t=current_time, up=pc.get('uptime', 'N/A'), df=pc.get('disk_free', 'N/A'): 
                            self._update_pc_row_status(p, "In Progress", t, up, df))

                for pc in pc_list:
                    if action in ("reboot", "shutdown"):
                        threading.Thread(target=self._reboot_monitor, args=(pc, action)).start()
                        continue

                    host = pc["hostname"]
                    alias = pc["alias"]
                    pc_id = pc["id"]

                    self.log_message(f"[TASK] Starting {action} on {alias} ({host})...")

                    # --- Action Logic (run_command, update, deploy, check_status) ---
                    if action == "run_command":
                        output, error, success = self._run_ssh_command_with_output(pc, command)
                        current_time = self._get_current_time_str()
                        pending_updates = self._check_pending_updates_count(pc) if success else 0
                        final_status = "OK" if success else "Cmd Failed"

                        if output:
                            self.log_message(f"[{alias} OUTPUT]:\n{output}", alias)
                        if error:
                            self.log_message(f"[{alias} ERROR]:\n{error}", alias)
                        if not output and not error:
                            self.log_message(f"[{alias}] Command completed with no output.", alias)

                        uptime, disk_free = pc.get('uptime', 'N/A'), pc.get('disk_free', 'N/A')
                        if success:
                            uptime, disk_free = self._check_status_data(pc)

                        self.db_manager.update_status(pc_id, final_status, current_time, pending_updates, uptime, disk_free)
                        self._apply_pc_data_sync(pc_id, final_status, current_time, pending_updates, uptime, disk_free)
                        self.master.after(0, lambda p=pc_id, s=final_status, t=current_time, u=pending_updates, up=uptime, df=disk_free: 
                            self._update_pc_row_data(p, s, t, u, up, df))
                        continue

                    # Handle Standard Actions
                    if action == "update":
                        cmd = "sudo sh -c 'DEBIAN_FRONTEND=noninteractive apt update && DEBIAN_FRONTEND=noninteractive apt upgrade -y'"
                    elif action == "deploy":
                        cmd = command
                    elif action == "check_status":
                        cmd = "echo OK"
                    else:
                        continue

                    success, status_msg = self._run_ssh_command(
                        pc['id'], pc, cmd,
                        # Fleet-wide status sweeps run through every PC sequentially in
                        # this one thread — don't block the whole sweep waiting ~35s for
                        # each sleeping PC to boot. Fire the wake packet and move on; a
                        # sleeping PC will just show Offline this cycle and pick up as OK
                        # on the next check once it's actually finished booting. Deliberate
                        # single/multi-PC actions (update/deploy/run command) still wait.
                        wake_wait=(action != "check_status")
                    )
                    if success:
                        new_status = "OK"
                    else:
                        # Classify connection errors as simply Offline
                        _conn_errs = ('errno', 'getaddrinfo', 'timed out',
                                      'connection refused', 'no route', 'eof')
                        if any(e in status_msg.lower() for e in _conn_errs):
                            new_status = "Offline"
                        else:
                            new_status = f"Failed: {status_msg}"
                    current_time = self._get_current_time_str()

                    pending_updates = self._check_pending_updates_count(pc) if success else 0
                    uptime, disk_free = self._check_status_data(pc) if success else ("N/A", "N/A")

                    # Opportunistically backfill Wake-on-LAN MAC address on a normal
                    # status check pass — cheap no-op once a PC already has one on file.
                    if success and action == "check_status" and not pc.get('mac_address'):
                        self._fetch_and_store_mac(pc)

                    # Snapshot drift check — only once per PC per 30-minute cooldown,
                    # and only on a successful, real check_status pass (not run/update/deploy).
                    if success and action == "check_status" and self._snapshot_drift_due(pc_id):
                        self._snapshot_diff_last_check[pc_id] = datetime.now()
                        self._log_snapshot_drift(pc, alias)

                    self.db_manager.update_status(pc_id, new_status, current_time, pending_updates, uptime, disk_free)
                    self._apply_pc_data_sync(pc_id, new_status, current_time, pending_updates, uptime, disk_free)
                    self.master.after(0, lambda p=pc_id, s=new_status, t=current_time, u=pending_updates, up=uptime, df=disk_free: 
                        self._update_pc_row_data(p, s, t, u, up, df))

                # Update the bottom status bar if this was a full fleet check
                if is_fleet_check:
                    self.master.after(0, lambda: self._rotate_fleet_metrics(list(self.pc_list_data)))

            finally:
                # --- CRITICAL FIX START ---
                if is_fleet_check:
                    self._fleet_sync_in_progress = False
                    self.log_message("[SYSTEM] Fleet refresh complete.")
                
                self._action_end()
                # --- CRITICAL FIX END ---

        threading.Thread(target=action_worker, daemon=True).start()

    def _update_pc_row_status(self, pc_id, status, last_update, uptime=None, disk_free=None):
        iid = str(pc_id)
        pc_data = next((p for p in self.pc_list_data if p["id"] == pc_id), None)

        if self.pc_list_view.exists(iid) and pc_data:
            current_values = list(self.pc_list_view.item(iid, 'values'))
            if current_values:
                if len(current_values) > 1: current_values[1] = status
                if len(current_values) > 3 and uptime is not None: current_values[3] = uptime
                if len(current_values) > 4 and disk_free is not None: current_values[4] = disk_free
                if len(current_values) > 5: current_values[5] = last_update

                # Apply the color tag
                tag = f'pc_color_{pc_data["alias"]}'
                self.pc_list_view.item(iid, values=current_values, tags=(tag,))
                self.master.focus()

        self._update_fleet_badges()

    def _apply_pc_data_sync(self, pc_id, status, last_update, pending_updates=0, uptime='N/A', disk_free='N/A', last_snapshot_date=None):
        """
        Pure in-memory update of self.pc_list_data — no Tk widget calls, so it's
        safe to call directly from a background worker thread. This guarantees
        pc_list_data is accurate the moment the fleet-check loop finishes, rather
        than waiting on a master.after(0, ...) callback to eventually apply it —
        which is what caused the fleet online/offline counter to lag by one PC.
        """
        pc_data = next((p for p in self.pc_list_data if p["id"] == pc_id), None)
        if pc_data:
            pc_data.update({
                'status': status,
                'last_update': last_update,
                'pending_updates': pending_updates,
                'uptime': uptime,
                'disk_free': disk_free,
            })
            if last_snapshot_date:
                pc_data['last_snapshot_date'] = last_snapshot_date

    def _update_pc_row_data(self, pc_id, status, last_update, pending_updates=0, uptime='N/A', disk_free='N/A', last_snapshot_date=None):
        iid = str(pc_id)

        pc_data = next((p for p in self.pc_list_data if p["id"] == pc_id), None)
        if pc_data:
            pc_data.update({
                'status': status,
                'last_update': last_update,
                'pending_updates': pending_updates,
                'uptime': uptime,
                'disk_free': disk_free,
            })
            if last_snapshot_date:
                pc_data['last_snapshot_date'] = last_snapshot_date

        if self.pc_list_view.exists(iid) and pc_data:
            current_values = list(self.pc_list_view.item(iid, 'values'))

            current_values[1] = status
            current_values[2] = pending_updates
            current_values[3] = uptime
            current_values[4] = disk_free
            current_values[5] = last_update

            if len(current_values) > 6 and last_snapshot_date:
                current_values[6] = last_snapshot_date

            # Apply the color tag for consistency
            tag = f'pc_color_{pc_data["alias"]}'
            self.pc_list_view.item(iid, values=current_values, tags=(tag,))

        # Keep the topbar ● online / ● offline badges live — previously these
        # only refreshed on load_pc_data(), so they went stale after every
        # check_status sweep, reboot, revert, clone, or watchdog detection.
        self._update_fleet_badges()

    def _check_pending_updates_count(self, pc_info):
        """The single source of truth for fetching the update count."""
        cmd = "apt list --upgradable 2>/dev/null | grep -c upgradable || echo 0"
        try:
            ssh = self._get_ssh_client(pc_info)
            if ssh:
                stdin, stdout, stderr = ssh.exec_command(cmd)
                count = stdout.read().decode().strip()
                ssh.close()
                return count if count.isdigit() else "0"
        except:
            pass
        return "0"

    def _run_ssh_command(self, pc_id, pc_info, command, wake_wait=True):
        """Unified SSH Engine. Returns (success, output_or_error).
        wake_wait=False sends a wake packet if needed but doesn't block
        waiting for the PC to boot — used by the sequential fleet-wide
        status sweep so one sleeping PC can't stall checks on the rest."""
        
        # --- CHECK-IN --- (track which PC is active; _active_actions is managed by _action_start/_action_end)
        with self._active_actions_lock:
            self.active_pcs.add(pc_id)

        host = pc_info["hostname"]
        user = pc_info["username"]
        alias = pc_info["alias"]
        connect_host = host if '.' in host else f"{host}.local"
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            self._wake_if_needed(pc_info, connect_host, wait=wake_wait)
            password = self.encryption_util.decrypt(pc_info["password_encrypted"])
            ssh.connect(connect_host, username=user, password=password, timeout=SSH_CONNECT_TIMEOUT)

            if 'sudo' in command.lower() and 'sudo -S' not in command:
                command = command.replace('sudo', 'sudo -S', 1)
                stdin, stdout, stderr = ssh.exec_command(command, get_pty=False)
                stdin.write(password + '\n')
                stdin.flush()
            else:
                stdin, stdout, stderr = ssh.exec_command(command)

            output = stdout.read().decode().strip()
            error = stderr.read().decode().strip()
            exit_status = stdout.channel.recv_exit_status()
            clean_err = error.replace('WARNING: apt does not have a stable CLI interface.', '').strip()
            
            if exit_status == 0 or (exit_status == 1 and "apt list" in command and not clean_err):
                if "uptime" not in command:
                    self.log_message(f"[SSH OK] {alias}: Success")
                return True, output if output else "Success"
            
            if any(x in command for x in ['reboot', 'shutdown']):
                self.log_message(f"[SSH OK] {alias}: Reboot/Shutdown initiated")
                return True, "Reboot/Shutdown initiated"

            return False, clean_err if clean_err else f"Exit code: {exit_status}"

        except Exception as e:
            # Specialized reboot handling
            if any(x in command for x in ['reboot', 'shutdown']):
                if any(msg in str(e).lower() for msg in ['reset by peer', 'timed out', 'eof']):
                    return True, "Connection dropped (Expected)"
            
            self.log_message(f"[SSH FAIL] {alias}: {e}")
            return False, str(e)
            
        finally:
            # --- CHECK-OUT --- (only remove from active_pcs; _active_actions is managed by _action_start/_action_end)
            try:
                ssh.close()
            except:
                pass
                
            with self._active_actions_lock:
                if pc_id in self.active_pcs:
                    self.active_pcs.remove(pc_id)
            
            self.master.after(0, self._update_status_bar)

    def _check_pending_updates_count(self, pc_info):
        """Fetches the count of upgradable packages using the unified engine."""
        cmd = "apt list --upgradable 2>/dev/null | grep -c upgradable || echo 0"
        success, output = self._run_ssh_command(pc_info['id'], pc_info, cmd)
        
        if success and output.isdigit():
            return output
        return "0"
    
    def _check_status_data(self, pc_info):
        """
        The single source of truth for Uptime and Disk Space.
        Works on both Raspberry Pi (no sudo) and Mint (sudo) 
        because these specific commands don't require root.
        """
        # We combine both commands into one string to save SSH connection time
        cmd = "uptime -p && df -h / | awk 'NR==2 {print $4}'"
        
        success, output = self._run_ssh_command(pc_info['id'], pc_info, cmd)
        
        if success and output:
            lines = output.splitlines()
            if len(lines) >= 2:
                # lines[0] = "up 2 hours, 30 minutes"
                # lines[1] = "15G"
                uptime = lines[0].replace("up ", "").strip()
                disk = lines[1].strip()
                return uptime, disk
        
        return "N/A", "N/A"

    def _fetch_and_store_mac(self, pc_info):
        """
        Reads the MAC address of the PC's default-route network interface over
        SSH and stores it in the DB. Cheap no-op if the PC already has one on
        file — this is meant to opportunistically backfill Wake-on-LAN data
        during normal status checks, not run on every single refresh.
        """
        pc_id = pc_info['id']
        if pc_info.get('mac_address'):
            return pc_info['mac_address']

        cmd = ("cat /sys/class/net/$(ip -4 route show default 2>/dev/null | "
               "awk '{print $5; exit}')/address 2>/dev/null")
        success, output = self._run_ssh_command(pc_id, pc_info, cmd)

        mac = output.strip().lower() if success and output else ''
        if mac and re.match(r'^([0-9a-f]{2}:){5}[0-9a-f]{2}$', mac):
            self.db_manager.update_mac(pc_id, mac)
            pc_info['mac_address'] = mac
            # Keep the in-memory fleet list in sync too
            live = next((p for p in self.pc_list_data if p["id"] == pc_id), None)
            if live is not None:
                live['mac_address'] = mac
            self.log_message(f"[INFO] {pc_info['alias']}: MAC address recorded ({mac}) — Wake-on-LAN ready.")
            return mac
        return ''

    def on_refresh_clicked(self, event=None):
        if self._fleet_sync_in_progress:
            self.log_message("[AUTO] Skipped — fleet sync already in progress.")
            return

        self._fleet_sync_in_progress = True
        self._record_last_action("Check Status")
        self.log_message("[INFO] Checking status, updates, and metrics for all PCs...")
        
        pc_list = self.pc_list_data
        # We NO LONGER reset the timer here; we wait for the thread to finish.
        self.run_mass_action_thread(pc_list, "check_status", command="Returned OK")

    def _run_ssh_command_with_output(self, pc_info, command):
        host = pc_info["hostname"]
        user = pc_info["username"]
        alias = pc_info["alias"]
        connect_host = host if '.' in host else f"{host}.local"

        password = self.encryption_util.decrypt(pc_info["password_encrypted"])

        if not isinstance(password, str):
            self.log_message(f"[FATAL] Auth failed for '{alias}'. Invalid password key.")
            return "", "Authentication Error", False

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self._wake_if_needed(pc_info, connect_host)
            ssh.connect(connect_host, username=user, password=password, timeout=SSH_CONNECT_TIMEOUT)

            if 'sudo' in command.lower():
                if 'sudo -S' not in command:
                    command = command.replace('sudo', 'sudo -S', 1)

                stdin, stdout, stderr = ssh.exec_command(command, get_pty=False)
                stdin.write(password + '\n')
                stdin.flush()
            else:
                stdin, stdout, stderr = ssh.exec_command(command)

            output = stdout.read().decode()
            error = stderr.read().decode()
            exit_status = stdout.channel.recv_exit_status()

            warning_text = 'WARNING: apt does not have a stable CLI interface. Use with caution in scripts.'
            clean_error = error.replace(warning_text, '').strip()

            if exit_status != 0 and clean_error:
                return "", clean_error, False

            return output.strip(), "", True

        except Exception as e:
            return "", str(e), False
        finally:
            try:
                ssh.close()
            except:
                pass

    def _snapshot_drift_due(self, pc_id):
        """True if this PC has never been drift-checked, or its last check
        was more than SNAPSHOT_DRIFT_COOLDOWN_SECONDS ago."""
        last = self._snapshot_diff_last_check.get(pc_id)
        if last is None:
            return True
        return (datetime.now() - last).total_seconds() >= self.SNAPSHOT_DRIFT_COOLDOWN_SECONDS

    def _check_snapshot_drift(self, pc_info):
        """
        Read-only comparison of this PC's currently manually-installed packages
        against its stored snapshot. Never modifies the remote PC.

        Returns:
            (missing, extra)  -- both sets of package names, if a snapshot exists
            (None, None)      -- if there's no snapshot yet, or the check failed
        """
        pc_id = pc_info['id']
        snapshot_raw = self.db_manager.get_latest_snapshot(pc_id)
        if not snapshot_raw:
            return None, None  # nothing to compare against yet

        snapshot_set = set(l.strip() for l in snapshot_raw.splitlines() if l.strip())

        # Same filter used when the snapshot was created / when Revert runs,
        # so the comparison is apples-to-apples.
        command_current = (
            "apt-mark showmanual | "
            "sed 's/:.*//' | "
            "grep -v -E '^(lib|gir1|glib|python3-lib|fonts-|xfonts-|gcc-|cpp-|linux-image|linux-headers|linux-modules)' | "
            "sort -u"
        )
        current_raw, error, success = self._run_ssh_command_with_output(pc_info, command_current)
        if not success:
            return None, None

        current_set = set(l.strip() for l in current_raw.splitlines() if l.strip())
        missing = snapshot_set - current_set   # in snapshot, not currently installed
        extra   = current_set - snapshot_set   # installed now, not in snapshot
        return missing, extra

    def _log_snapshot_drift(self, pc_info, alias):
        """
        Runs the drift check and writes a clear, actionable log entry.
        Purely informational — never installs, removes, or re-snapshots anything.
        The admin decides via the existing Revert-to-Snapshot / Create-Snapshot buttons.
        """
        missing, extra = self._check_snapshot_drift(pc_info)

        if missing is None:
            # No snapshot exists for this PC yet — nothing to compare, stay quiet.
            return

        if not missing and not extra:
            self.log_message(f"[SNAPSHOT] {alias}: matches its snapshot exactly. No drift.", alias)
            return

        self.log_message(f"[SNAPSHOT] {alias}: drift detected vs last snapshot —", alias)
        if missing:
            self.log_message(
                f"[SNAPSHOT]   ↓ {len(missing)} package(s) in snapshot but NOT installed: "
                f"{', '.join(sorted(missing))}", alias
            )
        if extra:
            self.log_message(
                f"[SNAPSHOT]   ↑ {len(extra)} package(s) installed since snapshot: "
                f"{', '.join(sorted(extra))}", alias
            )
        self.log_message(
            f"[SNAPSHOT]   → Review {alias} and choose: 'Revert to Snapshot' to remove the "
            f"extras above, or 'Create Snapshot' to adopt the current state as the new baseline.",
            alias
        )

    def run_create_snapshot_action(self, target_ids):
        pc_id = target_ids[0]
        pc = next((p for p in self.pc_list_data if p["id"] == pc_id), None)

        def worker():
            self._action_start()
            try:
                self.log_message(f"[TASK] Starting filtered snapshot creation on {pc['alias']}...")

                # -- Packages -----------------------------------------------------
                # 1. Get only explicitly installed packages (not auto dependencies)
                # 2. Strip architecture suffixes (:amd64, :arm64, :i386, etc.)
                #    so the snapshot is portable across different CPU architectures
                # 3. Filter out library packages (^lib) to keep the list app-focused
                pkg_cmd = (
                    "apt-mark showmanual | "
                    "sed 's/:.*//' | "
                    "grep -v -E '^(lib|gir1|glib|python3-lib|fonts-|xfonts-|gcc-|cpp-|linux-image|linux-headers|linux-modules)' | "
                    "sort -u"
                )

                pkg_output, pkg_error, pkg_success = self._run_ssh_command_with_output(pc, pkg_cmd)

                if not pkg_success or not pkg_output:
                    self.log_message(f"[FAIL] Snapshot creation failed for {pc['alias']}: {pkg_error or 'No packages found'}")
                    return

                # Strip any trailing whitespace/blank lines before saving
                pkg_output = pkg_output.strip()

                # -- Users (UID >= 1000, excluding nobody at 65534) ---------------
                user_cmd = ("awk -v OFS=: -F: '($3>=1000 && $3!=65534)"
                            "{print $1,$3,$4,$6,$7}' /etc/passwd")
                user_output, _, user_success = self._run_ssh_command_with_output(pc, user_cmd)
                user_data = user_output.strip() if user_success and user_output else ""

                # -- Groups with GID >= 1000 --------------------------------------
                group_cmd = ("awk -v OFS=: -F: '($3>=1000)"
                             "{print $1,$3,$4}' /etc/group")
                group_output, _, group_success = self._run_ssh_command_with_output(pc, group_cmd)
                group_data = group_output.strip() if group_success and group_output else ""

                # -- Save everything ----------------------------------------------
                self.db_manager.save_snapshot(pc_id, pkg_output, user_data, group_data)
                current_time = self._get_current_time_str()

                # New baseline just taken — clear any cooldown so the next
                # check_status compares against it right away, not up to 30 min later.
                self._snapshot_diff_last_check.pop(pc_id, None)

                n_pkgs   = len([l for l in pkg_output.splitlines() if l.strip()])
                n_users  = len([l for l in user_data.splitlines()  if l.strip()]) if user_data  else 0
                n_groups = len([l for l in group_data.splitlines() if l.strip()]) if group_data else 0

                self.log_message(
                    f"[SUCCESS] Snapshot created for {pc['alias']} at {current_time}. "
                    f"Saved {n_pkgs} app package(s), "
                    f"{n_users} user(s), {n_groups} group(s)."
                )
                self.master.after(0, lambda p=pc_id, t=current_time: self._update_pc_row_data(
                    p, pc['status'], pc['last_update'], pc['pending_updates'],
                    pc['uptime'], pc['disk_free'], t
                ))
            finally:
                self._action_end()

        threading.Thread(target=worker, daemon=True).start()
        self.master.focus()

    def run_revert_action(self, target_ids):
        pc_id = target_ids[0]
        pc = next((p for p in self.pc_list_data if p["id"] == pc_id), None)

        def worker():
            self._action_start()
            try:
                self.log_message(f"[TASK] Starting differential revert operation on {pc['alias']}...")
                snapshot_packages_raw = self.db_manager.get_latest_snapshot(pc_id)
                if not snapshot_packages_raw:
                    self.log_message(f"[FAIL] Revert failed for {pc['alias']}: No snapshot found in database.")
                    return

                snapshot_set = set(l.strip() for l in snapshot_packages_raw.splitlines() if l.strip())

                # Match EXACTLY the same filter used when the snapshot was created:
                # manually installed only, arch suffix stripped, lib packages excluded
                command_current = (
                    "apt-mark showmanual | "
                    "sed 's/:.*//' | "
                    "grep -v -E '^(lib|gir1|glib|python3-lib|fonts-|xfonts-|gcc-|cpp-|linux-image|linux-headers|linux-modules)' | "
                    "sort -u"
                )
                current_packages_raw, error, current_success = self._run_ssh_command_with_output(pc, command_current)

                if not current_success:
                    self.log_message(f"[FAIL] Revert failed for {pc['alias']}: Could not fetch current package list ({error}).")
                    return

                current_set = set(l.strip() for l in current_packages_raw.splitlines() if l.strip())

                # Packages manually installed AFTER the snapshot was taken
                packages_to_remove = current_set - snapshot_set

                current_time = self._get_current_time_str()
                if not packages_to_remove:
                    self.log_message(f"[INFO] {pc['alias']} (Revert): System is already at snapshot state. No packages to remove.")
                    new_status = "At Snapshot"
                    self.db_manager.update_status(pc_id, new_status, current_time, 0, pc['uptime'], pc['disk_free'])
                    self.master.after(0, lambda p=pc_id, s=new_status, t=current_time, u=0, up=pc['uptime'], df=pc['disk_free']: self._update_pc_row_data(p, s, t, u, up, df))
                    return

                num_removed = len(packages_to_remove)
                remove_list_str = " ".join(sorted(packages_to_remove))

                self.log_message(f"[INFO] Calculated diff: {num_removed} package(s) to remove from {pc['alias']}.")
                self.log_message(f"[CMD] {pc['alias']}: Executing removal of {num_removed} packages. The list contains: {' '.join(list(packages_to_remove)[:5])} ...")

                removal_script = (
                    f"DEBIAN_FRONTEND=noninteractive apt remove --purge -y {remove_list_str} && "
                    f"apt autoremove -y"
                )
                removal_command = f"sudo sh -c '{removal_script}'"

                success, status = self._run_ssh_command(pc_id, pc, removal_command)

                current_time = self._get_current_time_str()
                if success:
                    pending_updates = self._check_pending_updates_count(pc)
                    uptime, disk_free = self._check_status_data(pc)
                    new_status = f"Reverted ({num_removed} removed)"
                    self.log_message(f"[SUCCESS] Revert operation finished on {pc['alias']}. Updates pending: {pending_updates}.")
                    # PC should now match its snapshot — clear the cooldown so the
                    # next check confirms that right away instead of waiting up to 30 min.
                    self._snapshot_diff_last_check.pop(pc_id, None)
                    self.db_manager.update_status(pc_id, new_status, current_time, pending_updates, uptime, disk_free)
                    self.master.after(0, lambda p=pc_id, s=new_status, t=current_time, u=pending_updates, up=uptime, df=disk_free: self._update_pc_row_data(p, s, t, u, up, df))
                else:
                    new_status = f"Revert Failed: {status.splitlines()[-1]}"
                    self.log_message(f"[FAIL] Revert operation failed for {pc['alias']}: {status}")
                    self.db_manager.update_status(pc_id, new_status, current_time, 0, pc['uptime'], pc['disk_free'])
                    self.master.after(0, lambda p=pc_id, s=new_status, t=current_time, u=0, up=pc['uptime'], df=pc['disk_free']: self._update_pc_row_data(p, s, t, u, up, df))
            finally:
                self._action_end()

        threading.Thread(target=worker).start()
        self.master.focus()
        self._fleet_sync_in_progress = False

    def on_clone_pc_clicked(self, event=None):
        """
        Entry point for the Clone PC button.
        Requires exactly one PC to be selected (the TARGET).
        """
        if len(self.selected_pc_ids) != 1:
            self.log_message("[ERROR] Select exactly ONE PC as the clone target first.")
            return

        target_id = self.selected_pc_ids[0]
        target_pc = next((p for p in self.pc_list_data if p['id'] == target_id), None)
        if not target_pc:
            self.log_message("[ERROR] Selected PC not found in list.")
            return

        # ── Reinforce parent background before opening dialog ──────────────────
        self.master.config(bg=BG_PRIMARY)

        # ── Open the source-selection dialog ──────────────────────────────────
        dialog = CloneSourceDialog(
            self.master,
            target_pc=target_pc,
            all_pcs=self.pc_list_data,
            db_manager=self.db_manager
        )
        result, source_pc, dry_run = dialog.show()

        # ── Restore UI after dialog closes ─────────────────────────────────────
        self.master.config(bg=BG_PRIMARY)
        style = ttk.Style()
        style.configure("Custom.Treeview",
                    background=BG_TERTIARY,
                    foreground=FG_PRIMARY,
                    fieldbackground=BG_TERTIARY)
        style.configure("Custom.Treeview.Heading",
                    background=BG_SECONDARY,
                    foreground=FG_SECONDARY,
                    relief="flat",
                    borderwidth=0)
        style.map("Custom.Treeview.Heading",
              background=[('active', BG_TERTIARY)])
        self.pc_list_view.update_idletasks()
        self.load_pc_data()

        if result != 'clone' or not source_pc:
            self.log_message("[INFO] Clone operation cancelled.")
            return

        # ── OS check before cloning ────────────────────────────────────────────
        self.log_message("[TASK] Checking OS compatibility...")

        def _get_os_info(pc):
            cmd = (
                "lsb_release -is 2>/dev/null || "
                "cat /etc/os-release | grep '^ID=' | cut -d= -f2; "
                "lsb_release -ds 2>/dev/null | cut -f2- || "
                "cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'; "
                "lsb_release -rs 2>/dev/null || "
                "cat /etc/os-release | grep VERSION_ID | cut -d= -f2 | tr -d '\"'"
            )

            success, output = self._run_ssh_command(pc['id'], pc, cmd)

            if not success:
                return None

            lines = [l.strip() for l in output.splitlines() if l.strip()]
            if len(lines) < 3:
                return None

            distro = lines[0].lower()
            pretty = lines[1]
            version = lines[2]
            major = version.split('.')[0] if version else None

            return {
                "distro": distro,
                "pretty": pretty,
                "version": version,
                "major": major
            }

        target = _get_os_info(target_pc)
        # Only SSH the target — the source may be offline (snapshot-only clone).
        # Probe port 22 first; if unreachable skip the compatibility check for source.
        source = None
        try:
            import socket as _sock
            _c = _sock.create_connection((source_pc['hostname'], 22), timeout=2)
            _c.close()
            source = _get_os_info(source_pc)
        except Exception:
            self.log_message(f"[INFO] {source_pc['alias']} is offline — skipping source OS compatibility check.")

        version_warning = ""

        if source and target:
            distro_changed = source["distro"] != target["distro"]
            major_changed = source["major"] != target["major"]

            if distro_changed or major_changed:
                version_warning = (
                    f"\n\n⚠  SYSTEM DIFFERENCE DETECTED\n"
                    f"   Source : {source['pretty']}\n"
                    f"   Target : {target['pretty']}\n\n"
                )

            if distro_changed:
                version_warning += "• Different Linux distributions detected\n"

            if major_changed:
                version_warning += "• Different major versions detected\n"

            version_warning += (
                "\nSome packages may not be available on the target system.\n"
                "The install will use --ignore-missing so failures won't stop the process."
            )

        # ── Show warning (if any) before continuing ────────────────────────────
        if version_warning:
            proceed = messagebox.askyesno(
                "System Compatibility Warning",
                version_warning + "\n\nDo you want to continue?"
            )
            if not proceed:
                self.log_message("[INFO] Clone cancelled due to system differences.")
                return
 
        # ── Final confirmation dialog ─────────────────────────────────────────
        dry_run_notice = (
            "\n⚙  SIMULATION MODE — no software will be installed.\n"
            "   The diff will be calculated and logged only."
            if dry_run else ""
        )
        confirmed = self._show_custom_dialog(
            title="Confirm Simulation" if dry_run else "Confirm Clone Operation",
            message=f"You are about to {'SIMULATE a clone' if dry_run else 'clone'}:\n\n"
                    f"  FROM  →  {source_pc['alias']}  ({source_pc['hostname']})\n"
                    f"  TO    →  {target_pc['alias']}  ({target_pc['hostname']})\n"
                    f"{dry_run_notice}",
            secondary=(
                "The diff will be calculated and logged — no changes will be made to the target."
                if dry_run else
                "Packages missing on the target will be installed.  Nothing will be removed.\n"
                "User accounts (UID ≥ 1000) in the snapshot will be created if not present.\n"
                "New accounts are created locked — the log will list the passwd commands needed.\n"
                "This may take several minutes — watch the log for progress."
            ),
            highlight="Are you sure?  Yes / No",
            warning=not dry_run
        )
 
        if not confirmed:
            self.log_message("[INFO] Clone operation cancelled by user.")
            return
 
        mode = "Simulate" if dry_run else "Clone"
        self._record_last_action(f"{mode}: {source_pc['alias']} → {target_pc['alias']}")
        self.run_clone_action(target_pc, source_pc, dry_run=dry_run)
        self.master.focus()
 
    def run_clone_action(self, target_pc, source_pc, dry_run=False):
        """
        Background worker that performs the full clone sequence.
 
        Steps
        ─────
        1.  Fetch the source snapshot from the database  (no SSH needed)
        2.  SSH → target: get its current dpkg package list
        3.  Diff: packages in source snapshot that are NOT on target
        4.  SSH → target: apt-get install -y --ignore-missing <diff>
        5.  SSH → source (if online): snap list  →  log informational note
        6.  Update DB + UI row
        """
        target_id  = target_pc['id']
        target_alias = target_pc['alias']
        source_alias = source_pc['alias']
 
        def worker():
            self._action_start()
            try:
                # ── Separator in the log ──────────────────────────────────────
                sep = "─" * 60
                self.log_message(f"[CLONE] {sep}")
                self.log_message(f"[CLONE] Starting clone operation")
                self.log_message(f"[CLONE]   Source  : {source_alias}  (snapshot)")
                self.log_message(f"[CLONE]   Target  : {target_alias}  (live)")
                self.log_message(f"[CLONE] {sep}")
 
                # Safety default — overwritten by every normal code path
                final_status = "Clone Error"

                # Mark target as in-progress
                now = self._get_current_time_str()
                self.master.after(0, lambda: self._update_pc_row_status(
                    target_id, "Cloning...", now,
                    target_pc.get('uptime', 'N/A'),
                    target_pc.get('disk_free', 'N/A')))
 
                # ── Step 1: Load source snapshot ──────────────────────────────
                self.log_message(f"[CLONE] Step 1/5 — Loading snapshot for {source_alias}...")
                snap_full = self.db_manager.get_latest_snapshot_full(source_pc['id'])
                if not snap_full:
                    self.log_message(
                        f"[FAIL]  Clone aborted: {source_alias} has no snapshot in the database."
                    )
                    self._clone_finish(target_id, target_pc, "Clone Failed (no snapshot)", now)
                    return
                snapshot_raw, snap_user_data, snap_group_data = snap_full

                source_set = set(pkg.strip() for pkg in snapshot_raw.splitlines() if pkg.strip())
                snap_users  = [l.strip() for l in snap_user_data.splitlines()  if l.strip()]
                snap_groups = [l.strip() for l in snap_group_data.splitlines() if l.strip()]
                self.log_message(
                    f"[CLONE]   Source snapshot: {len(source_set)} packages, "
                    f"{len(snap_users)} user(s), {len(snap_groups)} group(s)."
                )
 
                # ── Step 2: Get current packages on target ────────────────────
                self.log_message(f"[CLONE] Step 2/5 — Fetching installed packages from {target_alias}...")
                dpkg_cmd = "dpkg --get-selections | awk '{if ($2 == \"install\") print $1}' | sed 's/:.*//' | sort -u"
                success, current_raw = self._run_ssh_command(target_id, target_pc, dpkg_cmd)
                if not success:
                    self.log_message(
                        f"[FAIL]  Clone aborted: could not fetch package list from "
                        f"{target_alias} — {current_raw}"
                    )
                    self._clone_finish(target_id, target_pc, "Clone Failed (SSH error)", now)
                    return
 
                target_set = set(pkg.strip() for pkg in current_raw.splitlines() if pkg.strip())
                self.log_message(f"[CLONE]   Target currently has {len(target_set)} packages installed.")
 
                # ── Step 3: Diff ──────────────────────────────────────────────
                to_install = sorted(source_set - target_set)
                already_present = len(source_set & target_set)
 
                self.log_message(f"[CLONE] Step 3/5 — Computing diff...")
                self.log_message(f"[CLONE]   Already present on target : {already_present} packages  (no action needed)")
                self.log_message(f"[CLONE]   To be installed on target  : {len(to_install)} packages")
 
                if not to_install:
                    self.log_message(
                        f"[CLONE]   {target_alias} already has all packages from {source_alias}'s snapshot."
                    )
                    self.log_message(f"[CLONE]   Skipping package install — continuing to user/group step.")
                    final_status = "Packages up to date"
 
                # Log the full list in manageable chunks so the log stays readable
                chunk_size = 10
                for i in range(0, len(to_install), chunk_size):
                    chunk = to_install[i:i + chunk_size]
                    self.log_message(f"[CLONE]   Installing: {' '.join(chunk)}")
 
                # ── Batch size (used by both live install and sim report) ─────
                INSTALL_BATCH_SIZE = 50
                n_batches = (len(to_install) + INSTALL_BATCH_SIZE - 1) // INSTALL_BATCH_SIZE

                # ── Pre-flight summary (live run only — sim gets its own block) ─
                if not dry_run:
                    self.log_message(f"[CLONE] {sep}")
                    self.log_message(f"[CLONE] ── Pre-flight summary ──────────────────────────────")
                    self.log_message(f"[CLONE]   Source packages (snapshot) : {len(source_set)}")
                    self.log_message(f"[CLONE]   Target packages (current)  : {len(target_set)}")
                    self.log_message(f"[CLONE]   Already present / skipped  : {already_present}")
                    self.log_message(f"[CLONE]   To be installed            : {len(to_install)}")
                    self.log_message(f"[CLONE]   Install batches            : {n_batches}  ({INSTALL_BATCH_SIZE} pkgs/batch)")
                    self.log_message(f"[CLONE]   Users  in snapshot         : {len(snap_users)}")
                    self.log_message(f"[CLONE]   Groups in snapshot         : {len(snap_groups)}")
                    self.log_message(f"[CLONE]   Mode                       : LIVE install")
                    self.log_message(f"[CLONE] {sep}")

                # ── Step 4: Install the diff (or skip if simulation) ──────────
                if dry_run:
                    self.log_message(f"[CLONE] Step 4/5 — SIMULATION: skipping package install.")
                    self.log_message(f"[CLONE] Step 5/5 — SIMULATION: computing user/group diff...")

                    # Fetch target state for the simulation report (read-only)
                    _, _tp = self._run_ssh_command(
                        target_id, target_pc,
                        "awk -v OFS=: -F: \'($3>=1000 && $3!=65534){print $1,$3}\' /etc/passwd"
                    )
                    sim_target_users = {}
                    for _l in (_tp or "").splitlines():
                        _p = _l.split(":", 1)
                        if len(_p) == 2:
                            try: sim_target_users[_p[0].strip()] = int(_p[1].strip())
                            except ValueError: pass

                    _, _tg = self._run_ssh_command(
                        target_id, target_pc,
                        "awk -v OFS=: -F: \'{print $1,$3}\' /etc/group"
                    )
                    sim_target_groups = set()
                    for _l in (_tg or "").splitlines():
                        _p = _l.split(":", 1)
                        if _p: sim_target_groups.add(_p[0].strip())

                    _, _ssh_raw = self._run_ssh_command(target_id, target_pc, "whoami")
                    sim_ssh_user = _ssh_raw.strip() if _ssh_raw else ""

                    _, _au = self._run_ssh_command(
                        target_id, target_pc, "awk -F: \'{print $3}\' /etc/passwd"
                    )
                    sim_taken_uids = set()
                    for _u in (_au or "").splitlines():
                        try: sim_taken_uids.add(int(_u.strip()))
                        except ValueError: pass

                    # Parse source users/groups for the sim
                    sim_src_users = {}
                    for _l in snap_users:
                        _p = _l.split(":", 4)
                        if len(_p) == 5:
                            try: sim_src_users[_p[0]] = (int(_p[1]), int(_p[2]), _p[3], _p[4])
                            except ValueError: pass

                    sim_src_groups = {}
                    for _l in snap_groups:
                        _p = _l.split(":", 2)
                        if len(_p) >= 2:
                            try: sim_src_groups[_p[0]] = int(_p[1])
                            except ValueError: pass

                    sim_groups_new      = [g for g in sim_src_groups if g not in sim_target_groups]
                    sim_users_match     = []
                    sim_users_conflict  = []
                    sim_users_new_clean = []
                    sim_users_new_remap = []
                    sim_users_ssh_skip  = []

                    for _u, (_uid, _gid, _home, _shell) in sim_src_users.items():
                        if _u == sim_ssh_user:
                            sim_users_ssh_skip.append(_u)
                        elif _u in sim_target_users:
                            if sim_target_users[_u] == _uid:
                                sim_users_match.append(_u)
                            else:
                                sim_users_conflict.append(
                                    f"{_u} (source uid {_uid} vs target uid {sim_target_users[_u]})"
                                )
                        elif _uid in sim_taken_uids:
                            sim_users_new_remap.append(f"{_u} (uid {_uid} taken — would get new uid)")
                        else:
                            sim_users_new_clean.append(f"{_u} (uid {_uid})")

                    self.log_message(f"[CLONE] {sep}")
                    self.log_message(f"[CLONE] ── Simulation results ─────────────────────────────────────────")
                    self.log_message(f"[CLONE]   Source  →  {source_alias:<20}  {len(source_set)} packages  |  {len(snap_users)} user(s)  |  {len(snap_groups)} group(s)")
                    self.log_message(f"[CLONE]   Target  →  {target_alias:<20}  {len(target_set)} packages installed")
                    self.log_message(f"[CLONE]   {sep}")
                    self.log_message(f"[CLONE]   Packages  already present : {already_present}")
                    self.log_message(f"[CLONE]   Packages  WOULD install   : {len(to_install)}  ({n_batches} batch(es) of {INSTALL_BATCH_SIZE})")
                    self.log_message(f"[CLONE]   Groups   WOULD create     : {len(sim_groups_new)}"
                                     + (f"  → {', '.join(sim_groups_new)}" if sim_groups_new else ""))
                    self.log_message(f"[CLONE]   Users    WOULD create     : {len(sim_users_new_clean) + len(sim_users_new_remap)}")
                    for _u in sim_users_new_clean:
                        self.log_message(f"[CLONE]     + {_u}")
                    for _u in sim_users_new_remap:
                        self.log_message(f"[CLONE]     + {_u}  ⚠ uid remap")
                    if sim_users_conflict:
                        self.log_message(f"[CLONE]   Users    WOULD skip (conflict) : {len(sim_users_conflict)}")
                        for _u in sim_users_conflict:
                            self.log_message(f"[CLONE]     ! {_u}")
                    if sim_users_match:
                        self.log_message(f"[CLONE]   Users    already match  : {len(sim_users_match)}"
                                         + f"  → {', '.join(sim_users_match)}")
                    if sim_users_ssh_skip:
                        self.log_message(f"[CLONE]   Users    skipped (SSH)  : {', '.join(sim_users_ssh_skip)}")
                    if sim_users_new_clean or sim_users_new_remap:
                        self.log_message(f"[CLONE]   ⚠  New users will be created LOCKED — password must be set after cloning.")
                        self.log_message(f"[CLONE]      Each user is prompted on first console login, or run: sudo passwd <username>")
                    self.log_message(f"[CLONE] {sep}")
                    self.log_message(f"[CLONE] Simulation complete.  No changes were made to {target_alias}.")
                    self.log_message(f"[CLONE]   Review the results above, then run Clone PC with Simulation OFF to apply.")
                    pkg_word  = "package" if len(to_install) == 1 else "packages"
                    usr_would = len(sim_users_new_clean) + len(sim_users_new_remap)
                    final_status = f"Simulated ({len(to_install)} {pkg_word}, {usr_would} user(s))"
                    now = self._get_current_time_str()
                    # Simulation is complete — finish here, skip live Steps 5 & Snap
                    self.log_message(f"[CLONE] {sep}")
                    self._clone_finish(target_id, target_pc, final_status, now)
                    return
                elif to_install:
                    self.log_message(f"[CLONE] Step 4/5 — Installing {len(to_install)} package(s) on {target_alias} in {n_batches} batch(es)...")
                    self.log_message(f"[CLONE]   This may take several minutes.  Please wait...")

                    # ── apt-lock guard: wait up to 5 min for any other apt/dpkg process ──
                    APT_LOCK_RETRIES   = 10
                    APT_LOCK_WAIT_SECS = 30
                    lock_check_cmd = (
                        "sudo sh -c '"
                        "fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock "
                        "       /var/cache/apt/archives/lock /var/lib/dpkg/lock "
                        "2>/dev/null; echo $?'"
                    )
                    lock_clear = False
                    for attempt in range(1, APT_LOCK_RETRIES + 1):
                        lk_ok, lk_out = self._run_ssh_command(target_id, target_pc, lock_check_cmd)
                        # fuser exits 1 when no process holds the file (i.e. lock is free)
                        last_line = lk_out.strip().splitlines()[-1] if lk_out.strip() else "1"
                        if last_line == "1":
                            lock_clear = True
                            break
                        self.log_message(
                            f"[CLONE]   apt lock held by another process — waiting {APT_LOCK_WAIT_SECS}s "
                            f"(attempt {attempt}/{APT_LOCK_RETRIES})…"
                        )
                        time.sleep(APT_LOCK_WAIT_SECS)

                    if not lock_clear:
                        self.log_message(
                            f"[FAIL]  Clone aborted: apt/dpkg lock still held on {target_alias} "
                            f"after {APT_LOCK_RETRIES * APT_LOCK_WAIT_SECS}s.  "
                            f"Another process may be running apt on that machine."
                        )
                        self._clone_finish(target_id, target_pc, "Clone Failed (apt locked)", self._get_current_time_str())
                        return

                    # ── Disk space pre-check ─────────────────────────────────
                    DISK_MIN_MB = 500
                    df_ok, df_out = self._run_ssh_command(
                        target_id, target_pc,
                        "df / --output=avail -BM | tail -1 | tr -d 'M '"
                    )
                    if df_ok and df_out.strip().isdigit():
                        free_mb = int(df_out.strip())
                        self.log_message(
                            f"[CLONE]   Disk space on {target_alias}: {free_mb} MB free."
                        )
                        if free_mb < DISK_MIN_MB:
                            self.log_message(
                                f"[FAIL]  Clone aborted: only {free_mb} MB free on {target_alias} "
                                f"(minimum {DISK_MIN_MB} MB required).  Free up space and retry."
                            )
                            self._clone_finish(target_id, target_pc, "Clone Failed (low disk)", self._get_current_time_str())
                            return
                    else:
                        self.log_message(
                            f"[WARN]  Could not read disk space on {target_alias} — continuing anyway."
                        )

                    # ── Batched install ───────────────────────────────────────
                    batch_installed = 0
                    batch_failed    = 0
                    for batch_num, batch_start in enumerate(range(0, len(to_install), INSTALL_BATCH_SIZE), start=1):
                        batch = to_install[batch_start : batch_start + INSTALL_BATCH_SIZE]
                        pkg_string = ' '.join(batch)
                        self.log_message(
                            f"[CLONE]   Batch {batch_num}/{n_batches}: installing {len(batch)} package(s)…"
                        )
                        install_script = (
                            "DEBIAN_FRONTEND=noninteractive apt-get install -y "
                            "--ignore-missing "
                            "--allow-unauthenticated "
                            f"{pkg_string}"
                        )
                        install_cmd = f"sudo sh -c '{install_script}'"
                        b_ok, b_out = self._run_ssh_command(target_id, target_pc, install_cmd)
                        if b_ok:
                            batch_installed += len(batch)
                            self.log_message(f"[CLONE]   Batch {batch_num}/{n_batches}: OK")
                        else:
                            batch_failed += len(batch)
                            self.log_message(
                                f"[WARN]  Batch {batch_num}/{n_batches}: apt error — "
                                f"{b_out[:300] if b_out else 'no output'}"
                            )

                    now = self._get_current_time_str()
                    self.log_message(f"[CLONE] {sep}")
                    if batch_failed == 0:
                        self.log_message(f"[SUCCESS] Clone complete — {batch_installed} package(s) installed on {target_alias}.")
                        final_status = f"Clone OK ({batch_installed} installed)"
                    else:
                        self.log_message(
                            f"[WARN]  Clone finished with errors: "
                            f"{batch_installed} installed, {batch_failed} may have failed (see log)."
                        )
                        final_status = f"Clone Partial ({batch_installed} ok, {batch_failed} failed)"
 

                # ── Step 5: Clone users and groups ────────────────────────────
                self.log_message(f"[CLONE] Step 5/5 — Cloning users and groups to {target_alias}...")

                if not snap_users and not snap_groups:
                    self.log_message(f"[CLONE]   No user/group data in snapshot — skipping (re-snapshot the source to capture this).")
                else:
                    # Fetch the SSH login user so we never touch it
                    _, ssh_user_raw = self._run_ssh_command(target_id, target_pc, "whoami")
                    ssh_user = ssh_user_raw.strip() if ssh_user_raw else ""

                    # -- Parse source users: {username: (uid, gid, home, shell)} --
                    source_users = {}
                    for line in snap_users:
                        parts = line.split(":", 4)
                        if len(parts) == 5:
                            uname, uid, gid, home, shell = parts
                            try:
                                source_users[uname] = (int(uid), int(gid), home, shell)
                            except ValueError:
                                pass

                    # -- Fetch existing users on target --------------------------
                    _, target_passwd = self._run_ssh_command(
                        target_id, target_pc,
                        "awk -v OFS=: -F: '($3>=1000 && $3!=65534){print $1,$3}' /etc/passwd"
                    )
                    target_users = {}  # {username: uid}
                    for line in (target_passwd or "").splitlines():
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            try:
                                target_users[parts[0].strip()] = int(parts[1].strip())
                            except ValueError:
                                pass

                    # -- Fetch taken UIDs on target ------------------------------
                    _, all_uids_raw = self._run_ssh_command(
                        target_id, target_pc,
                        "awk -F: '{print $3}' /etc/passwd"
                    )
                    taken_uids = set()
                    for uid_str in (all_uids_raw or "").splitlines():
                        try:
                            taken_uids.add(int(uid_str.strip()))
                        except ValueError:
                            pass

                    # -- Process groups first (users may depend on their GID) ---
                    source_groups = {}  # {groupname: (gid, members_csv)}
                    for line in snap_groups:
                        parts = line.split(":", 2)
                        if len(parts) >= 2:
                            gname = parts[0]
                            try:
                                ggid = int(parts[1])
                                members_csv = parts[2] if len(parts) == 3 else ""
                                source_groups[gname] = (ggid, members_csv)
                            except ValueError:
                                pass

                    _, target_groups_raw = self._run_ssh_command(
                        target_id, target_pc,
                        "awk -v OFS=: -F: '{print $1,$3}' /etc/group"
                    )
                    target_group_gids = {}  # {groupname: gid}
                    for line in (target_groups_raw or "").splitlines():
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            try:
                                target_group_gids[parts[0].strip()] = int(parts[1].strip())
                            except ValueError:
                                pass

                    groups_created = 0
                    for gname, (ggid, _) in source_groups.items():
                        if gname in target_group_gids:
                            continue  # already exists
                        ok, _ = self._run_ssh_command(
                            target_id, target_pc,
                            f"sudo groupadd --gid {ggid} {gname} 2>/dev/null || "
                            f"sudo groupadd {gname}"
                        )
                        if ok:
                            groups_created += 1
                            self.log_message(f"[CLONE]   Group created: {gname} (gid {ggid})")
                        else:
                            self.log_message(f"[WARN]  Could not create group: {gname}")

                    # -- Process users -------------------------------------------
                    users_skipped_match   = 0
                    users_skipped_conflict = 0
                    users_skipped_ssh     = 0
                    users_created         = 0
                    created_usernames     = []  # track names for password warning

                    # Compiled once here, used inside the loop below
                    _valid_uname = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
                    _valid_path  = re.compile(r'^/[a-zA-Z0-9_./ -]{0,255}$')
                    _valid_shell = re.compile(r'^/[a-zA-Z0-9_./]{0,127}$')

                    for uname, (uid, gid, home, shell) in source_users.items():
                        # Rule: skip current SSH user
                        if uname == ssh_user:
                            users_skipped_ssh += 1
                            self.log_message(f"[CLONE]   Skipped user '{uname}' — current SSH session user.")
                            continue

                        # ── Input validation: reject entries that could inject shell commands ──
                        if not _valid_uname.match(uname):
                            self.log_message(f"[WARN]  Skipped user '{uname}' — username failed safety check.")
                            users_skipped_conflict += 1
                            continue
                        if not _valid_path.match(home):
                            self.log_message(f"[WARN]  Skipped user '{uname}' — home path '{home}' failed safety check.")
                            users_skipped_conflict += 1
                            continue
                        if not _valid_shell.match(shell):
                            self.log_message(f"[WARN]  Skipped user '{uname}' — shell '{shell}' failed safety check.")
                            users_skipped_conflict += 1
                            continue

                        if uname in target_users:
                            if target_users[uname] == uid:
                                # Perfect match — already correct
                                users_skipped_match += 1
                                self.log_message(f"[CLONE]   User '{uname}' (uid {uid}) already exists — no action needed.")
                            else:
                                # Username exists but UID differs — warn and skip
                                users_skipped_conflict += 1
                                self.log_message(
                                    f"[WARN]  User '{uname}' exists on {target_alias} with uid {target_users[uname]} "
                                    f"(source uid was {uid}) — skipping, manual review recommended."
                                )
                            continue

                        # Username is new — try preferred UID first
                        if uid not in taken_uids:
                            create_cmd = (
                                f"sudo useradd --uid {uid} --gid {gid} "
                                f"--home-dir {home} --shell {shell} "
                                f"--create-home {uname}"
                            )
                            uid_note = f"uid {uid} (matched source)"
                        else:
                            # UID taken by someone else — let system pick next free
                            create_cmd = (
                                f"sudo useradd --gid {gid} "
                                f"--home-dir {home} --shell {shell} "
                                f"--create-home {uname}"
                            )
                            uid_note = f"uid {uid} was taken — system assigned new uid"
                            self.log_message(
                                f"[WARN]  User '{uname}': source uid {uid} already taken on "
                                f"{target_alias} — will be created with a new uid. "
                                f"File ownership may differ if shared storage is used."
                            )

                        ok, err = self._run_ssh_command(target_id, target_pc, create_cmd)
                        if ok:
                            users_created += 1
                            created_usernames.append(uname)
                            # Only mark the UID as taken if we actually used it;
                            # on remap the system chose a different UID we don't know,
                            # but marking the source uid anyway prevents double-use attempts.
                            if uid not in taken_uids:
                                taken_uids.add(uid)
                            self.log_message(f"[CLONE]   User created: {uname}  ({uid_note})")
                            # Lock account and force password change on first login
                            self._run_ssh_command(
                                target_id, target_pc,
                                f"sudo chage -d 0 {uname} 2>/dev/null"
                            )
                        else:
                            self.log_message(f"[WARN]  Could not create user '{uname}': {err}")

                    # -- Add users to their supplementary groups -----------------
                    # Build the set of users confirmed to exist on target:
                    # those that were already there + those we just created.
                    confirmed_on_target = set(target_users.keys()) | set(created_usernames)
                    memberships_added = 0
                    for gname, (_, members_csv) in source_groups.items():
                        if not members_csv:
                            continue
                        for member in members_csv.split(","):
                            member = member.strip()
                            if not member:
                                continue
                            if member not in source_users:
                                continue  # not a user we manage
                            if member not in confirmed_on_target:
                                self.log_message(
                                    f"[WARN]  Skipping group membership {member}→{gname} "
                                    f"— user does not exist on {target_alias}."
                                )
                                continue
                            ok, _ = self._run_ssh_command(
                                target_id, target_pc,
                                f"sudo usermod -aG {gname} {member} 2>/dev/null"
                            )
                            if ok:
                                memberships_added += 1

                    self.log_message(f"[CLONE] {sep}")
                    self.log_message(
                        f"[CLONE] User/group summary: "
                        f"{groups_created} group(s) created, "
                        f"{users_created} user(s) created, "
                        f"{users_skipped_match} already matched, "
                        f"{users_skipped_conflict} conflict(s) skipped, "
                        f"{memberships_added} group membership(s) applied."
                    )
                    if users_skipped_ssh:
                        self.log_message(f"[CLONE]   (SSH session user skipped as expected)")
                    if created_usernames:
                        pw_sep = "═" * 60
                        self.log_message(f"[WARN]  {pw_sep}")
                        self.log_message(f"[WARN]  ACTION REQUIRED — {len(created_usernames)} user(s) created with NO PASSWORD SET:")
                        for _cu in created_usernames:
                            self.log_message(f"[WARN]    →  {_cu}")
                        self.log_message(f"[WARN]  These accounts are LOCKED until a password is set.")
                        self.log_message(f"[WARN]  Each user will be prompted to set one on first console login,")
                        self.log_message(f"[WARN]  or an admin can set it now by running on {target_alias}:")
                        for _cu in created_usernames:
                            self.log_message(f"[WARN]      sudo passwd {_cu}")
                        self.log_message(f"[WARN]  {pw_sep}")

                # ── Snap informational note (best-effort, non-blocking) ───────
                # Only attempt SSH if the source is reachable — it may be offline.
                self.log_message(f"[CLONE] Checking for Snap packages on {source_alias} (informational)...")
                _src_reachable = False
                try:
                    import socket as _sock
                    _sc = _sock.create_connection((source_pc['hostname'], 22), timeout=2)
                    _sc.close()
                    _src_reachable = True
                except Exception:
                    pass

                if _src_reachable:
                    snap_success, snap_out = self._run_ssh_command(
                        source_pc['id'], source_pc,
                        "snap list 2>/dev/null | tail -n +2 | awk '{print $1}'"
                    )
                    # "Success" is the SSH engine's placeholder when stdout is empty
                    snap_real = snap_out.strip() if snap_out and snap_out.strip() != "Success" else ""
                    if snap_success and snap_real:
                        snaps = [s for s in snap_real.splitlines() if s not in ('Name', '', 'Success')]
                        if snaps:
                            self.log_message(
                                f"[CLONE] ⓘ  {source_alias} has {len(snaps)} Snap package(s) that were NOT cloned automatically:"
                            )
                            self.log_message(f"[CLONE]   Snaps: {', '.join(snaps)}")
                            self.log_message(f"[CLONE]   Install these manually on {target_alias} if required.")
                        else:
                            self.log_message(f"[CLONE]   No Snap packages found on {source_alias}.")
                    elif snap_success and not snap_real:
                        self.log_message(f"[CLONE]   No Snap packages found on {source_alias}.")
                    else:
                        self.log_message(f"[CLONE]   Could not read Snap list from {source_alias}.")
                else:
                    self.log_message(f"[CLONE]   {source_alias} is offline — Snap package check skipped (clone used snapshot data only).")
 
                self.log_message(f"[CLONE] {sep}")
                self._clone_finish(target_id, target_pc, final_status, now)
 
            except Exception as e:
                self.log_message(f"[ERROR] Unexpected error during clone: {e}")
                self._clone_finish(target_id, target_pc, "Clone Error", self._get_current_time_str())
            finally:
                self._action_end()
 
        threading.Thread(target=worker, daemon=True).start()
 
    def _clone_finish(self, pc_id, pc_info, status, timestamp):
        """
        Thread-safe helper: updates the DB and refreshes the UI row
        after a clone operation completes (success, partial, or failure).
        """
        pending = self._check_pending_updates_count(pc_info)
        uptime, disk_free = self._check_status_data(pc_info)
        self.db_manager.update_status(pc_id, status, timestamp, pending, uptime, disk_free)
        self.master.after(0, lambda: self._update_pc_row_data(
            pc_id, status, timestamp, pending, uptime, disk_free
        ))
        
    def on_send_file_clicked(self, event=None):
        # Keep your existing selection check
        if len(self.selected_pc_ids) != 1:
            self.log_message("[ERROR] Select exactly ONE PC to send a file to.")
            return

        pc_id = self.selected_pc_ids[0]
        # Use existing pc_info lookup logic
        pc_info = next((p for p in self.pc_list_data if p["id"] == pc_id), None)
        if not pc_info:
            self.log_message(f"[ERROR] PC with ID {pc_id} not found.")
            return

        # Open the updated dialog
        dialog = SendFileDialog(self.master, pc_info["alias"])
        result, local_file_path = dialog.show()

        # Handle the updated result
        if result and local_file_path:
            if not os.path.exists(local_file_path):
                self.log_message(f"[ERROR] Local file not found: {local_file_path}")
                return

            # Extract the checkbox value
            execute_after = result.get('execute_after', False)
            
            self.log_message(f"[TASK] Initiating file transfer to {pc_info['alias']}: {os.path.basename(local_file_path)}")
            self._record_last_action(f"Send File → {pc_info['alias']}")
            
            # Start the thread with the 3 required arguments
            threading.Thread(
                target=self.run_send_file_action,
                args=(pc_info, local_file_path, execute_after),
                daemon=True
            ).start()
            
            self.master.focus()

    def run_send_file_action(self, pc_info, local_path, execute_after):
        """Prepares the transfer and launches the background worker."""
        # Detect if script needs sudo locally first
        needs_sudo = False
        try:
            with open(local_path, 'r') as f:
                if 'sudo ' in f.read():
                    needs_sudo = True
        except:
            pass

        # Launch the actual work in a separate thread so the UI doesn't freeze
        threading.Thread(
            target=self._execute_file_transfer_worker,
            args=(pc_info, local_path, execute_after, needs_sudo),
            daemon=True
        ).start()

    def _execute_file_transfer_worker(self, pc_info, local_path, execute_after, needs_sudo):
        """The actual background work for SFTP and Execution."""
        alias = pc_info["alias"]
        pc_id = pc_info["id"]
        remote_filename = os.path.basename(local_path)
        remote_path = f"./{remote_filename}"
        ssh = None
        success_status = "Send Failed"

        try:
            password = self.encryption_util.decrypt(pc_info["password_encrypted"])
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self._wake_if_needed(pc_info, pc_info["hostname"])
            ssh.connect(pc_info["hostname"], username=pc_info["username"], password=password, timeout=10)

            # Upload
            sftp = ssh.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
            self.log_message(f"[SUCCESS] {alias}: File sent to {remote_path}")
            success_status = "Sent Successfully"

            if execute_after:
                self.log_message(f"[TASK] {alias}: Executing {remote_filename}...")
                ssh.exec_command(f"chmod +x {remote_path}")

                if needs_sudo:
                    self.log_message(f"[INFO] {alias}: Sudo detected. Injecting password...")
                    cmd = f"sudo -S sh -c 'yes | {remote_path}' <<EOF\n{password}\nEOF"
                else:
                    cmd = f"yes | {remote_path}"

                stdin, stdout, stderr = ssh.exec_command(cmd)
                output = stdout.read().decode().strip()
                errors = stderr.read().decode().strip()

                if errors:
                    ignored = ["[sudo] password for", "Hit:", "Get:", "Reading package lists"]
                    clean_err = "\n".join([l for l in errors.splitlines() if not any(p in l for p in ignored)])
                    if clean_err: self.log_message(f"[{alias} ERROR]:\n{clean_err}", alias)
                
                if output: self.log_message(f"[{alias} OUTPUT]:\n{output}", alias)
                success_status = "Executed Successfully"

        except Exception as e:
            msg = str(e).splitlines()[-1] if str(e) else "Connection Error"
            self.log_message(f"[FAIL] {alias}: {msg}")
            success_status = "Send Failed"
        finally:
            if ssh: ssh.close()
            
            # Final UI/DB Update
            end_time = self._get_current_time_str()
            upd = self._check_pending_updates_count(pc_info)
            upt, df = self._check_status_data(pc_info)
            self.db_manager.update_status(pc_id, success_status, end_time, upd, upt, df)
            self.master.after(0, lambda: self._update_pc_row_data(pc_id, success_status, end_time, upd, upt, df))


# --- Main Entry ---
if __name__ == "__main__":
    db_manager = DBManager()
    root = tk.Tk()
    app = PCManager(root, db_manager)
    root.mainloop()