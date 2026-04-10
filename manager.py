#!/usr/bin/env python3
import webbrowser
import warnings
import re
import sys
import sqlite3
import threading
import os
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
                disk_free TEXT DEFAULT 'N/A'
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
        cursor.execute("PRAGMA table_info(pcs)")
        existing_columns = [column[1] for column in cursor.fetchall()]

        # Columns that were added in later versions of the script
        migrations = [
            ("pending_updates", "INTEGER DEFAULT 0"),
            ("uptime", "TEXT DEFAULT 'N/A'"),
            ("disk_free", "TEXT DEFAULT 'N/A'")
        ]

        for col_name, col_type in migrations:
            if col_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE pcs ADD COLUMN {col_name} {col_type}")
                    print(f"[DB] Added missing column: {col_name}")
                except sqlite3.OperationalError:
                    pass 
        self.conn.commit()

    def get_all_pcs(self):
        # Using SELECT * for robust searches
        self.cursor.execute("SELECT * FROM pcs ORDER BY alias")
        return self.cursor.fetchall()

    def add_pc(self, hostname, username, encrypted_password, alias):
        self.cursor.execute(
            "INSERT INTO pcs (hostname, username, password_encrypted, alias, status, last_update, pending_updates, uptime, disk_free) VALUES (?, ?, ?, ?, 'Unknown', 'N/A', 0, 'N/A', 'N/A')",
            (hostname, username, encrypted_password, alias),
        )
        self.conn.commit()
        return self.cursor.lastrowid

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

    def save_snapshot(self, pc_id, package_list_data):
        self.delete_all_snapshots_for_pc(pc_id)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute(
            "INSERT INTO software_snapshots (pc_id, timestamp, package_list) VALUES (?, ?, ?)",
            (pc_id, timestamp, package_list_data),
        )
        self.conn.commit()

    def get_latest_snapshot(self, pc_id):
        self.cursor.execute(
            "SELECT package_list FROM software_snapshots WHERE pc_id=? ORDER BY timestamp DESC LIMIT 1",
            (pc_id,),
        )
        result = self.cursor.fetchone()
        return result[0] if result else None

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


# --- Add PC Dialog Class ---
class AddPCDialog(tk.Toplevel):
    def __init__(self, parent, is_edit=False):
        super().__init__(parent)
        self.title("Edit PC Details" if is_edit else "Add New PC")
        self.transient(parent)
        self.grab_set()
        self.parent = parent
        self.result = None
        self.data = {}
        self._idle_check_timer_id = None
        self._watchdog_timer_id = None

        self.config(bg=BG_PRIMARY)
        self.minsize(600, 250)

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

            entry = tk.Entry(fields_frame, width=40,
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

            self.entries[entry_key] = entry
            entry.grid(row=i, column=1, sticky='we', padx=0, pady=8)

        fields_frame.columnconfigure(1, weight=1)

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
        width = self.winfo_width()
        height = self.winfo_height()
        x = parent.winfo_x() + parent.winfo_width() // 2 - width // 2
        y = parent.winfo_y() + parent.winfo_height() // 2 - height // 2
        self.geometry(f'+{x}+{y}')

    def on_ok(self):
        self.data = {key.replace('_entry', ''): entry.get().strip()
                     for key, entry in self.entries.items()}
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
        self.grab_set()
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
        self.grab_set()
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
        self.grab_set()

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
        

        self.AUTO_CHECK_INTERVAL_MS = 5 * 60 * 1000  # 5 minute autocheck - customise to your tastes
        self.AUTO_CHECK_RETRY_MS = 60 * 1000  # 1 minute retry if busy
        self._idle_check_timer_id = None

        self.master.title(f"🖥️ {APP_NAME}")
        self.master.geometry("1024x650")
        self.master.minsize(1024, 500)
        self.master.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.master.config(bg=BG_PRIMARY)
        self._fleet_sync_in_progress = False  # True only during a full check_status sweep
        self._last_action_label = "App Started"
        self._last_action_time = datetime.now()
        self._active_actions = 0  
        self._active_actions_lock = threading.Lock()

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

        # Auto idle-checker: starts 5 minutes after launch (the startup check covers the first run)
        self._idle_check_timer_id = self.master.after(self.AUTO_CHECK_INTERVAL_MS, self._auto_idle_check)

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

        self.master.geometry("1180x700")
        self.master.minsize(1050, 560)
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
                     danger=False, color=None):
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
            lbl.pack(fill='x', pady=1)
            lbl.bind('<Enter>', lambda e: lbl.config(bg=bg_hot,  fg=fg_hot))
            lbl.bind('<Leave>', lambda e: lbl.config(bg=bg_norm, fg=fg_norm))
            if command:
                lbl.bind('<Button-1>', lambda e: command())
            Tooltip(lbl, tooltip_text)
            return lbl

        def _sb_divider(label_text=None):
            """Thin rule with optional small uppercase group label."""
            tk.Frame(sidebar, bg=BORDER_COLOR, height=1).pack(
                fill='x', padx=6, pady=(6, 0))
            if label_text:
                tk.Label(sidebar, text=label_text.upper(),
                         bg=SB_BG, fg='#3a3d42',
                         font=('Segoe UI', 7)).pack(pady=(2, 0))

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
                 'Create Snapshot — save current installed package list to the database',
                 command=lambda: self._show_confirmation_dialog("create_snapshot", "Create Snapshot"),
                 color='purple')
        _sb_icon(sidebar, '⟲',
                 'Revert to Snapshot — remove packages installed since last snapshot\n⚠ This cannot be undone',
                 command=lambda: self._show_confirmation_dialog("revert", "Revert"),
                 color='purple')

        _sb_divider('Power')

        _sb_icon(sidebar, '↻',
                 'Reboot PC — restart selected PC and monitor reconnection',
                 command=lambda: self._show_confirmation_dialog("reboot", "Reboot"),
                 danger=True)
        _sb_icon(sidebar, '⏻',
                 'Shutdown PC — power off selected PC and monitor connection loss',
                 command=lambda: self._show_confirmation_dialog("shutdown", "Shutdown"),
                 danger=True)

        # Push exit to bottom
        tk.Frame(sidebar, bg=SB_BG).pack(fill='both', expand=True)

        _sb_divider()
        _sb_icon(sidebar, '⏏',
                 'Exit — close the application',
                 command=self._on_closing,
                 danger=True)

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

        # ── STATUS BAR ────────────────────────────────────────────────────────
        tk.Frame(main_area, bg=BORDER_COLOR, height=1).pack(fill='x')
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
                                self.master.after(0, self.reset_auto_idle_timer)
                        else:
                            # --- FAILURE LOGIC ---
                            if "OFFLINE" not in last_known_status:
                                self.log_message(f"[LOST] Connection to {pc_info.get('alias')} ({host}) failed. Marking as OFFLINE.")
                                
                                timestamp = datetime.now().strftime("%H:%M:%S")
                                pc_info['status'] = "OFFLINE"
                                
                                # This updates the actual grid/table view
                                self.master.after(0, lambda p=pc_id, t=timestamp: 
                                    self._update_pc_row_data(p, "OFFLINE", t, "N/A", "N/A", "N/A"))
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

    def reset_auto_idle_timer(self):
        if hasattr(self, '_idle_check_timer_id') and self._idle_check_timer_id:
            try:
                self.master.after_cancel(self._idle_check_timer_id)
            except:
                pass
        # Schedule the next timed autocheck one and save the ID
        self._idle_check_timer_id = self.master.after(self.AUTO_CHECK_INTERVAL_MS, self._auto_idle_check)       

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

            last_snapshot_date = self.db_manager.get_latest_snapshot_timestamp(pc_id)

            data_entry = {
                "id": pc_id, "hostname": host, "username": user, "password_encrypted": enc,
                "alias": alias, "status": status, "last_update": last,
                "pending_updates": pending, "index": idx,
                "uptime": uptime, "disk_free": disk_free,
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

    def _auto_idle_check(self):
        """Fires every 5 minutes. If busy, backs off safely."""

        # 0. Cancel any pending timer to avoid duplicates
        if self._idle_check_timer_id:
            self.master.after_cancel(self._idle_check_timer_id)
            self._idle_check_timer_id = None

        # 1. Prevent overlapping refreshes
        if self._fleet_sync_in_progress:
            self.log_message("[AUTO] Skipped — fleet sync already in progress.")
            self._idle_check_timer_id = self.master.after(
                self.AUTO_CHECK_RETRY_MS, self._auto_idle_check
            )
            return

        # 2. Check if anything else is running
        if self._active_actions > 0:
            self.log_message("[AUTO] Idle check deferred — actions in progress. Retrying in 1 min.")
            self._idle_check_timer_id = self.master.after(
                self.AUTO_CHECK_RETRY_MS, self._auto_idle_check
            )
            return

        # 3. Safety: no PCs configured
        if not self.pc_list_data:
            self._idle_check_timer_id = self.master.after(
                self.AUTO_CHECK_INTERVAL_MS, self._auto_idle_check
            )
            return

        # 4. Safe to run wrap in try/finally so the timer always reschedules
        try:
            self.log_message("[AUTO] System idle — triggering auto refresh.")
            self.on_refresh_clicked(None)
        except Exception as e:
            self.log_message(f"[AUTO] Error during auto refresh: {e}")
        finally:
            # 5. Schedule next normal check 5 minutes from NOW (always runs)
            self._idle_check_timer_id = self.master.after(
                self.AUTO_CHECK_INTERVAL_MS, self._auto_idle_check
            )

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
        dialog.grab_set()
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
        dialog.update_idletasks()
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
                new_id = self.db_manager.add_pc(data['hostname'], data['username'], encrypted_password, data['alias'])
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

                    success, status_msg = self._run_ssh_command(pc['id'], pc, cmd)
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

                    self.db_manager.update_status(pc_id, new_status, current_time, pending_updates, uptime, disk_free)
                    self.master.after(0, lambda p=pc_id, s=new_status, t=current_time, u=pending_updates, up=uptime, df=disk_free: 
                        self._update_pc_row_data(p, s, t, u, up, df))

                # Update the bottom status bar if this was a full fleet check
                if is_fleet_check:
                    self.master.after(0, lambda: self._rotate_fleet_metrics(list(self.pc_list_data)))

            finally:
                # --- CRITICAL FIX START ---
                if is_fleet_check:
                    self._fleet_sync_in_progress = False
                    self.log_message("[SYSTEM] Fleet refresh complete. Scheduling next check in 5 minutes.")
                    # Restart the 5-minute timer only AFTER the work is done
                    self.master.after(0, self.reset_auto_idle_timer)
                
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

    def _run_ssh_command(self, pc_id, pc_info, command):
        """Unified SSH Engine. Returns (success, output_or_error)."""
        
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

    def run_create_snapshot_action(self, target_ids):
        pc_id = target_ids[0]
        pc = next((p for p in self.pc_list_data if p["id"] == pc_id), None)

        def worker():
            self._action_start()
            try:
                self.log_message(f"[TASK] Starting snapshot creation on {pc['alias']}...")

                command = "dpkg --get-selections | awk '{if ($2 == \"install\") print $1}'"

                output, error, success = self._run_ssh_command_with_output(pc, command)

                if success and output:
                    self.db_manager.save_snapshot(pc_id, output)
                    current_time = self._get_current_time_str()
                    self.log_message(f"[SUCCESS] Snapshot created for {pc['alias']} at {current_time}. Saved {len(output.splitlines())} packages.")
                    self.master.after(0, lambda p=pc_id, t=current_time: self._update_pc_row_data(p, pc['status'], pc['last_update'], pc['pending_updates'], pc['uptime'], pc['disk_free'], t))
                else:
                    self.log_message(f"[FAIL] Snapshot creation failed for {pc['alias']}: {error or 'Unknown SSH Error'}")
            finally:
                self._action_end()

        threading.Thread(target=worker).start()
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

                snapshot_set = set(snapshot_packages_raw.splitlines())

                command_current = "dpkg --get-selections | awk '{if ($2 == \"install\") print $1}'"
                current_packages_raw, error, current_success = self._run_ssh_command_with_output(pc, command_current)

                if not current_success:
                    self.log_message(f"[FAIL] Revert failed for {pc['alias']}: Could not fetch current package list ({error}).")
                    return

                current_set = set(current_packages_raw.splitlines())

                packages_to_remove = current_set - snapshot_set

                current_time = self._get_current_time_str()
                if not packages_to_remove:
                    self.log_message(f"[INFO] {pc['alias']} (Revert): System is already at snapshot state. No packages to remove.")
                    new_status = "At Snapshot"
                    self.db_manager.update_status(pc_id, new_status, current_time, 0, pc['uptime'], pc['disk_free'])
                    self.master.after(0, lambda p=pc_id, s=new_status, t=current_time, u=0, up=pc['uptime'], df=pc['disk_free']: self._update_pc_row_data(p, s, t, u, up, df))
                    return

                remove_list_str = " ".join(packages_to_remove)
                num_removed = len(packages_to_remove)

                self.log_message(f"[INFO] Calculated diff: {num_removed} package(s) to remove from {pc['alias']}.")

                removal_script = (
                    f"DEBIAN_FRONTEND=noninteractive apt update && "
                    f"DEBIAN_FRONTEND=noninteractive apt remove --purge -y {remove_list_str} && "
                    f"apt autoremove -y"
                )
                removal_command = f"sudo sh -c '{removal_script}'"

                self.log_message(f"[CMD] {pc['alias']}: Executing removal of {num_removed} packages. The list contains: {' '.join(list(packages_to_remove)[:3])} ...")

                success, status = self._run_ssh_command(pc_id, pc, removal_command)

                current_time = self._get_current_time_str()
                if success:
                    pending_updates = self._check_pending_updates_count(pc)
                    uptime, disk_free = self._check_status_data(pc)
                    new_status = f"Reverted ({num_removed} removed)"
                    self.log_message(f"[SUCCESS] Revert operation finished on {pc['alias']}. Updates pending: {pending_updates}.")
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