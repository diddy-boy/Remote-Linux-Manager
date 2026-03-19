#!/usr/bin/env python3
import warnings
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
from cryptography.hazmat.backends import default_backend
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

# --- Professional Styling Configuration ---
BG_PRIMARY = '#1e1e1e'          # Deep charcoal background
BG_SECONDARY = '#2d2d2d'        # Slightly lighter for contrast
BG_TERTIARY = '#3d3d3d'         # Widget backgrounds
BG_LOG = '#0d0d0d'              # Deepest black for log area
FG_PRIMARY = '#e8e8e8'          # Crisp white text
FG_SECONDARY = '#b0b0b0'        # Muted text for secondary elements
ACCENT_BLUE = '#0078d4'         # Modern Microsoft-style blue
BORDER_COLOR = '#404040'        # Subtle borders

# Button Color Palette (Modern, Professional)
BTN_PRIMARY = '#0078d4'         # Primary action blue
BTN_PRIMARY_HOVER = '#1084d8'
BTN_SUCCESS = '#107c10'         # Success green
BTN_SUCCESS_HOVER = '#13a313'
BTN_DANGER = '#d13438'          # Danger red
BTN_DANGER_HOVER = '#e13c40'
BTN_WARNING = '#ff8c00'         # Warning orange
BTN_WARNING_HOVER = '#ffa732'
BTN_INFO = '#5c2d91'            # Info purple
BTN_INFO_HOVER = '#6c3d9f'

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
APP_NAME = "Remote Linux Manager"
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

    def _create_table(self):
        self.cursor.execute(
            """
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
        """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS software_snapshots (
                id INTEGER PRIMARY KEY,
                pc_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                package_list TEXT NOT NULL,
                FOREIGN KEY (pc_id) REFERENCES pcs(id)
            )
        """
        )
        self.conn.commit()

        try:
            self.cursor.execute("SELECT pending_updates FROM pcs LIMIT 1")
        except sqlite3.OperationalError:
            self.cursor.execute("ALTER TABLE pcs ADD COLUMN pending_updates INTEGER DEFAULT 0")
            self.conn.commit()

        try:
            self.cursor.execute("SELECT uptime FROM pcs LIMIT 1")
        except sqlite3.OperationalError:
            self.cursor.execute("ALTER TABLE pcs ADD COLUMN uptime TEXT DEFAULT 'N/A'")
            self.conn.commit()

        try:
            self.cursor.execute("SELECT disk_free FROM pcs LIMIT 1")
        except sqlite3.OperationalError:
            self.cursor.execute("ALTER TABLE pcs ADD COLUMN disk_free TEXT DEFAULT 'N/A'")
            self.conn.commit()

        print("[INFO] Database table ensured.")

    def get_all_pcs(self):
        self.cursor.execute(
            "SELECT id, hostname, username, password_encrypted, alias, status, last_update, pending_updates, uptime, disk_free FROM pcs ORDER BY alias, hostname"
        )
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
        self.cursor.execute(
            "UPDATE pcs SET status=?, last_update=?, pending_updates=?, uptime=?, disk_free=? WHERE id=?",
            (status, last_update, pending_updates, uptime, disk_free, pc_id),
        )
        self.conn.commit()

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
    POPULAR_COMMANDS = {
        "Show Disk Usage": "df -h",
        "View System Uptime": "uptime",
        "Check Free Memory (RAM)": "free -h",
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

        # --- CORRECT POSITION: Command Section Container (Grid for Listbox + Textbox) ---
        command_section = tk.Frame(main_frame, bg=BG_PRIMARY)
        command_section.pack(fill='both', expand=True, pady=(0, 15))

        # Configure columns: Listbox (weight 2) and Textbox (weight 1)
        command_section.columnconfigure(0, weight=2)
        command_section.columnconfigure(1, weight=1)

        # --- 1. Popular Commands Listbox ---
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

        # Use ttk.Scrollbar here (assuming it's imported at the top of manager.py)
        list_vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.command_listbox.yview)
        self.command_listbox.configure(yscrollcommand=list_vsb.set)

        list_vsb.pack(side='right', fill='y')
        self.command_listbox.pack(side='left', fill='both', expand=True, padx=1, pady=1)
        # Bind the selection event to the handler method
        self.command_listbox.bind('<<ListboxSelect>>', self._on_command_select)

        # --- 2. Manual Command Text Widget ---
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

        # Line 656 - Now correctly indented with 8 spaces
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
                                   bg=BG_SECONDARY, fg=FG_PRIMARY, borderless=1)
        browse_btn.pack(side='right')

        # --- EXECUTE CHECKBOX (The new feature) ---
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
                                   bg=BG_SECONDARY, fg=FG_PRIMARY, borderless=1)
        cancel_btn.pack(side='right', padx=5)

        send_btn = AdaptiveButton(btn_frame, text="Send File", command=self.on_send,
                                 bg=BTN_PRIMARY, fg=FG_PRIMARY, borderless=1)
        send_btn.pack(side='right')

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
    # This command runs 'apt update' to refresh the package list,
    # AND THEN checks the count of upgradable packages.
    UPDATE_CHECK_CMD = "sudo apt update && sudo apt list --upgradable 2>/dev/null | grep -c '/'"
    STATUS_CHECK_CMD = "uptime -p && df -h / | awk 'NR==2 {print $4}'"

    def __init__(self, master, db_manager):
        self.master = master
        self.db_manager = db_manager
        self.pc_list_data = []
        self.monitoring_pcs = {}
        self.encryption_util = EncryptionUtility()
        self.selected_pc_ids = []
        self.pc_colors_map = {}

        self.master.title(f"🖥️ {APP_NAME}")
        self.master.geometry("1024x650")
        self.master.minsize(1024, 500)
        self.master.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.master.config(bg=BG_PRIMARY)

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

        # Treeview styling
        style.configure("Custom.Treeview",
                       background=BG_TERTIARY,
                       foreground=FG_PRIMARY,
                       fieldbackground=BG_TERTIARY,
                       borderwidth=0,
                       rowheight=28,
                       font=('Segoe UI', 10))
        style.map('Custom.Treeview',
                 background=[('selected', ACCENT_BLUE)],
                 foreground=[('selected', 'white')])
        style.configure("Custom.Treeview.Heading",
                       background=BG_SECONDARY,
                       foreground=FG_PRIMARY,
                       relief="flat",
                       borderwidth=0,
                       font=('Segoe UI', 10, 'bold'))
        style.map("Custom.Treeview.Heading",
                 background=[('active', BG_TERTIARY)])

    def setup_ui(self):
        # Main container
        main_container = tk.Frame(self.master, bg=BG_PRIMARY)
        main_container.pack(fill='both', expand=True, padx=15, pady=15)

        # Header
        header_frame = tk.Frame(main_container, bg=BG_PRIMARY)
        header_frame.pack(fill='x', pady=(0, 20))

        title = tk.Label(header_frame, text=APP_NAME,
                        bg=BG_PRIMARY, fg=FG_PRIMARY,
                        font=('Segoe UI', 18, 'bold'))
        title.pack(side='left')

        # Hackaday link button on the right
        link_btn = tk.Label(header_frame,
                           text="📘 Check for updates",
                           bg=BG_PRIMARY, fg=ACCENT_BLUE,
                           font=('Segoe UI', 9, 'underline'),
                           cursor='hand2')
        link_btn.pack(side='right')

        def open_hackaday(e):
            import webbrowser
            webbrowser.open('https://hackaday.io/project/204282-remote-linux-manager')

        link_btn.bind('<Button-1>', open_hackaday)
        link_btn.bind('<Enter>', lambda e: link_btn.config(fg=BTN_PRIMARY_HOVER))
        link_btn.bind('<Leave>', lambda e: link_btn.config(fg=ACCENT_BLUE))

        # PC List Section
        # MODIFIED: Removed hardcoded height and pack_propagate(False) to enable dynamic sizing
        list_frame = tk.Frame(main_container, bg=BG_PRIMARY)
        list_frame.pack(fill='x', expand=False, pady=(0, 15))

        # Treeview with border
        tree_container = tk.Frame(list_frame, bg=BORDER_COLOR)
        tree_container.pack(fill='both', expand=True)

        tree_inner = tk.Frame(tree_container, bg=BG_TERTIARY)
        tree_inner.pack(fill='both', expand=True, padx=1, pady=1)

        columns = ("Alias", "Status", "Pending Updates", "Uptime", "Disk Space Free", "Last Update", "Last Snapshot Created")

        # MODIFIED: Initial height set to minimum (4 rows)
        self.pc_list_view = ttk.Treeview(tree_inner, columns=columns, show='headings',
                                         selectmode='extended', style='Custom.Treeview', height=4)

        width_map = {
            "Alias": 140,
            "Status": 120,
            "Pending Updates": 110,
            "Uptime": 110,
            "Disk Space Free": 120,
            "Last Update": 150,
            "Last Snapshot Created": 150
        }

        for col in columns:
            self.pc_list_view.heading(col, text=col)
            self.pc_list_view.column(col, width=width_map.get(col, 100), anchor='center')

        vsb = ttk.Scrollbar(tree_inner, orient="vertical", command=self.pc_list_view.yview)
        vsb.pack(side='right', fill='y')
        self.pc_list_view.configure(yscrollcommand=vsb.set)
        self.pc_list_view.pack(side='left', fill='both', expand=True)

        self.pc_list_view.bind('<<TreeviewSelect>>', self.on_selection_changed)

        # Add click binding to allow deselection
        self.pc_list_view.bind('<Button-1>', self.on_treeview_click)

        # Control Panel
        control_panel = tk.Frame(main_container, bg=BG_PRIMARY)
        control_panel.pack(fill='x', pady=(0, 15))

        # Button configurations
        button_configs = [
            # Row 1: PC Management
            [
                ("Register PC", self.on_add_pc_clicked, BTN_PRIMARY, BTN_PRIMARY_HOVER),
                ("Edit Registered PC", self.on_edit_pc_clicked, BTN_PRIMARY, BTN_PRIMARY_HOVER),
                ("De-Register PC", lambda: self._show_confirmation_dialog("delete", "Delete"), BTN_WARNING, BTN_WARNING_HOVER),
                ("Send File", self.on_send_file_clicked, BTN_INFO, BTN_INFO_HOVER),
            ],
            # Row 2: Maintenance
            [
                ("Check Status", self.on_refresh_clicked, BTN_SUCCESS, BTN_SUCCESS_HOVER),
                ("Run Update", lambda: self._show_confirmation_dialog("update", "Run Update"), BTN_PRIMARY, BTN_PRIMARY_HOVER),
                ("Deploy Software", self.on_deploy_software_clicked, BTN_PRIMARY, BTN_PRIMARY_HOVER),
                ("Run Command", self.on_run_command_clicked, BTN_PRIMARY, BTN_PRIMARY_HOVER),
            ],
            # Row 3: System Control
            [
                ("Create Snapshot", lambda: self._show_confirmation_dialog("create_snapshot", "Create Snapshot"), BTN_INFO, BTN_INFO_HOVER),
                ("Revert to snapshot", lambda: self._show_confirmation_dialog("revert", "Revert"), BTN_INFO, BTN_INFO_HOVER),
                ("Reboot PC", lambda: self._show_confirmation_dialog("reboot", "Reboot"), BTN_DANGER, BTN_DANGER_HOVER),
                ("Shutdown PC", lambda: self._show_confirmation_dialog("shutdown", "Shutdown"), BTN_DANGER, BTN_DANGER_HOVER),
            ]
        ]

        for row_configs in button_configs:
            row_frame = tk.Frame(control_panel, bg=BG_PRIMARY)
            row_frame.pack(fill='x', pady=4)

            # Make all 4 columns equal width
            for col in range(4):
                row_frame.columnconfigure(col, weight=1, uniform="button")

            # Create buttons with grid for perfect alignment
            for i, (text, command, bg_color, hover_color) in enumerate(row_configs):
             btn = AdaptiveButton(row_frame,
                             text=text,
                             command=command,
                             bg=bg_color,
                             fg='white',
                             font=('Segoe UI', 10, 'bold'),
                             relief='raised',
                             bd=1,
                             pady=10,
                             cursor='hand2',
                             activebackground=bg_color,
                             highlightthickness=0,
                             overrelief='flat') # <--- ADD THIS LINE
             btn.grid(row=0, column=i, sticky='ew', padx=3)

             # Hover effects
             btn.bind('<Enter>', lambda e, b=btn, c=hover_color: b.config(bg=c))
             btn.bind('<Leave>', lambda e, b=btn, c=bg_color: b.config(bg=c))
        # Log Section
        log_section = tk.Frame(main_container, bg=BG_PRIMARY)
        log_section.pack(fill='both', expand=True)

        log_header = tk.Label(log_section, text="Log Output",
                             bg=BG_PRIMARY, fg=FG_PRIMARY,
                             font=('Segoe UI', 11, 'bold'))
        log_header.pack(anchor='w', pady=(0, 8))

        log_container = tk.Frame(log_section, bg=BORDER_COLOR)
        log_container.pack(fill='both', expand=True)

        log_inner = tk.Frame(log_container, bg=BG_LOG)
        log_inner.pack(fill='both', expand=True, padx=1, pady=1)

        self.log_view = tk.Text(log_inner, state='disabled', wrap='word',
                               bg=BG_LOG, fg=FG_PRIMARY,
                               insertbackground=FG_PRIMARY,
                               relief='flat', bd=0,
                               font=('Consolas', 10),
                               padx=10, pady=8)

        log_vsb = ttk.Scrollbar(log_inner, orient="vertical", command=self.log_view.yview)
        self.log_view.configure(yscrollcommand=log_vsb.set)

        log_vsb.pack(side='right', fill='y')
        self.log_view.pack(side='left', fill='both', expand=True)

        self._setup_log_tags()

    def _setup_treeview_tags(self):
        """Sets up the Treeview tags for coloring rows based on PC alias, using the log colors."""
        for alias, color_hex in self.pc_colors_map.items():
            tag_name = f'pc_color_{alias}'
            # Configure a tag to set the foreground (text) color for the row.
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

            # 1. Prioritize the explicitly provided tag (this is the fix)
            tag = tag_override if tag_override else 'default'
            
            # 2. If no override is provided, use the old string parsing logic
            if not tag_override and hasattr(self, 'pc_colors_map'):
                for pc_alias in self.pc_colors_map.keys():
                    # Your existing alias-finding logic
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
        self.pc_list_data.clear()
        self.pc_list_view.delete(*self.pc_list_view.get_children())

        self.pc_colors_map.clear()
        raw_pcs = self.db_manager.get_all_pcs()

        unique_aliases = sorted(list(set(row[4] for row in raw_pcs)))
        palette_size = len(PC_COLOR_PALETTE)
        for i, alias in enumerate(unique_aliases):
            self.pc_colors_map[alias] = PC_COLOR_PALETTE[i % palette_size]

        # MODIFIED: Calculate and apply dynamic height
        num_pcs = len(raw_pcs)
        min_rows = 4
        max_rows = 15

        # Calculate height: max(min_rows, min(max_rows, num_pcs))
        treeview_height = max(min_rows, min(max_rows, num_pcs))
        self.pc_list_view.config(height=treeview_height)

        # Setup log and treeview tags BEFORE inserting data
        self._setup_log_tags()
        self._setup_treeview_tags()

        for idx, row in enumerate(raw_pcs):
            if len(row) >= 10:
                pc_id, host, user, enc, alias, status, last, pending, uptime, disk_free = row[:10]
            elif len(row) == 8:
                pc_id, host, user, enc, alias, status, last, pending = row
                uptime, disk_free = 'N/A', 'N/A'
            elif len(row) == 7:
                pc_id, host, user, enc, alias, status, last = row
                pending, uptime, disk_free = 0, 'N/A', 'N/A'
            else:
                self.log_message(f"[ERROR] Skipping PC with malformed data row (length {len(row)}).")
                continue

            last_snapshot_date = self.db_manager.get_latest_snapshot_timestamp(pc_id)

            data_entry = dict(id=pc_id, hostname=host, username=user, password_encrypted=enc,
                            alias=alias, status=status, last_update=last,
                            pending_updates=pending, index=idx,
                            uptime=uptime, disk_free=disk_free,
                            last_snapshot_date=last_snapshot_date)

            self.pc_list_data.append(data_entry)

            tree_values = (alias, status, pending, uptime, disk_free, last, last_snapshot_date)
            # Apply the color tag when inserting the row
            tag = f'pc_color_{alias}'
            self.pc_list_view.insert('', 'end', iid=pc_id, values=tree_values, tags=(tag,))

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

        elif action == "clone":
            if len(target_ids) != 2:
                self.log_message(f"[ERROR] Cloning requires selecting exactly TWO PCs: the Source and the Target.")
                return
            source_pc = next((p for p in self.pc_list_data if p["id"] == target_ids[0]), {"alias": f"ID {target_ids[0]}"})
            target_pc = next((p for p in self.pc_list_data if p["id"] == target_ids[1]), {"alias": f"ID {target_ids[1]}"})
            highlight_text = f"{source_pc['alias'].upper()} → {target_pc['alias'].upper()}"
            msg = f"Are you sure you want to clone from PC"
            sec = "WARNING: This will deploy all packages from the SOURCE PC to the TARGET PC. This cannot be easily undone and you will still need to copy config files over."
            dialog_title = "Confirm PC Clone Deployment"

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
                self.on_delete_pc_clicked(target_ids)
            elif action == "clone":
                self.run_clone_action(target_ids)
            elif action == "revert":
                self.run_revert_action(target_ids)
            elif action == "create_snapshot":
                self.run_create_snapshot_action(target_ids)
            else:
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

        # Highlighted PC name (bigger and colored)
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
                self.db_manager.add_pc(data['hostname'], data['username'], encrypted_password, data['alias'])
                self.log_message(f"[INFO] Added new PC: {data['alias']}")
                self.load_pc_data()
            except Exception as e:
                self.log_message(f"[ERROR] Failed to add PC: {e}")
                self.master.focus()

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
                self.load_pc_data()
            except Exception as e:
                self.log_message(f"[ERROR] Failed to update PC: {e}")
                self.master.focus()

    def on_delete_pc_clicked(self, target_ids):
        for pc_id in target_ids:
            try:
                self.db_manager.delete_pc(pc_id)
                self.pc_list_data = [p for p in self.pc_list_data if p["id"] != pc_id]
                self.pc_list_view.delete(str(pc_id))
                self.log_message(f"[INFO] Deleted PC ID: {pc_id}")
                self.load_pc_data() # Reload data to recalculate list height
            except Exception as e:
                self.log_message(f"[ERROR] Failed to delete PC ID {pc_id}: {e}")
                self.master.focus()

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
        pc_id = pc_info["id"]
        alias = pc_info["alias"]

        cmd = "sudo reboot" if action == "reboot" else "sudo shutdown now"

        self.log_message(f"[TASK] Executing {action} command on {alias}...")
        success, status = self._run_ssh_command(pc_info, cmd)

        current_time = self._get_current_time_str()

        if not success:
            self.log_message(f"[FAIL] Initial {action} command failed on {alias}: {status}")
            fail_status = f"{action.capitalize()} Cmd Failed"
            self.db_manager.update_status(pc_id, fail_status, current_time, 0, pc_info.get('uptime', 'N/A'), pc_info.get('disk_free', 'N/A'))
            self.master.after(0, lambda p=pc_id, s=fail_status, t=current_time, u=0, up=pc_info.get('uptime', 'N/A'), df=pc_info.get('disk_free', 'N/A'): self._update_pc_row_data(p, s, t, u, up, df))
            return

        monitoring_status = f"{action.capitalize()}ing..."
        self.log_message(f"[MONITOR] {alias} is now {monitoring_status.lower()}. Status set to {monitoring_status}.")
        self.db_manager.update_status(pc_id, monitoring_status, current_time, 0, 'N/A', 'N/A')
        self.master.after(0, lambda p=pc_id, s=monitoring_status, t=current_time, u=0, up='N/A', df='N/A': self._update_pc_row_data(p, s, t, u, up, df))

        time.sleep(MONITOR_POLL_INTERVAL_SECONDS / 2)

        self.log_message(f"[MONITOR] Waiting for {alias} to go offline...")

        offline_time_start = time.time()
        while self._can_connect(pc_info, timeout=1):
            self.log_message(f"[MONITOR] {alias} is still connected. Waiting for drop...")
            time.sleep(MONITOR_POLL_INTERVAL_SECONDS)

            if time.time() - offline_time_start > 300:
                self.log_message(f"[WARN] {alias} failed to go offline after 5 mins. Assuming failed {action} or stuck state.")
                final_status = f"{action.capitalize()} Stuck"
                self.db_manager.update_status(pc_id, final_status, self._get_current_time_str(), 0, 'N/A', 'N/A')
                self.master.after(0, lambda p=pc_id, s=final_status, t=self._get_current_time_str(), u=0, up='N/A', df='N/A': self._update_pc_row_data(p, s, t, u, up, df))
                return

        self.log_message(f"[MONITOR] {alias} is offline.")

        if action == "shutdown":
            final_status = "Offline (Shutdown)"
            self.db_manager.update_status(pc_id, final_status, self._get_current_time_str(), 0, 'N/A', 'N/A')
            self.master.after(0, lambda p=pc_id, s=final_status, t=self._get_current_time_str(), u=0, up='N/A', df='N/A': self._update_pc_row_data(p, s, t, u, up, df))
            self.log_message(f"[SUCCESS] {alias} has shut down.")
            return

        self.log_message(f"[MONITOR] Polling every {MONITOR_POLL_INTERVAL_SECONDS}s for {alias} to reconnect.")
        reconnect_time_start = time.time()
        while not self._can_connect(pc_info):
            self.log_message(f"[MONITOR] {alias} is still offline.")
            time.sleep(MONITOR_POLL_INTERVAL_SECONDS)

            if time.time() - reconnect_time_start > 600:
                self.log_message(f"[CRITICAL] {alias} failed to come back online after 10 minutes.")
                final_status = "Reboot Failed"
                self.db_manager.update_status(pc_id, final_status, self._get_current_time_str(), 0, 'N/A', 'N/A')
                self.master.after(0, lambda p=pc_id, s=final_status, t=self._get_current_time_str(), u=0, up='N/A', df='N/A': self._update_pc_row_data(p, s, t, u, up, df))
                return

        pending_updates = self._check_pending_updates_count(pc_info)
        uptime, disk_free = self._check_status_data(pc_info)
        final_status = "OK (Rebooted)"
        current_time = self._get_current_time_str()

        self.log_message(f"[SUCCESS] {alias} is back online! Status: {final_status}. Updates pending: {pending_updates}. Uptime: {uptime}. Disk: {disk_free}")
        self.db_manager.update_status(pc_id, final_status, current_time, pending_updates, uptime, disk_free)
        self.master.after(0, lambda p=pc_id, s=final_status, t=current_time, u=pending_updates, up=uptime, df=disk_free: self._update_pc_row_data(p, s, t, u, up, df))

    def run_mass_action_thread(self, pc_list, action, command=None):
        def action_worker():
            for pc in pc_list:
                if action in ("reboot", "shutdown"):
                    threading.Thread(target=self._reboot_monitor, args=(pc, action)).start()
                    continue

                host = pc["hostname"]
                alias = pc["alias"]
                pc_id = pc["id"]

                self.log_message(f"[TASK] Starting {action} on {alias} ({host})...")
                self.master.after(0, lambda p=pc_id: self._update_pc_row_status(p, "In Progress", self._get_current_time_str(), pc.get('uptime', 'N/A'), pc.get('disk_free', 'N/A')))

                if action == "run_command":
                    output, error, success = self._run_ssh_command_with_output(pc, command)

                    current_time = self._get_current_time_str()
                    pending_updates = self._check_pending_updates_count(pc) if success else 0

                    if success:
                        self.log_message(f"[OUTPUT: {alias}] ** Success **.\n{output or '(No output)'}")
                        final_status = next((p["status"] for p in self.pc_list_data if p["id"] == pc_id), "OK")
                    else:
                        self.log_message(f"[OUTPUT: {alias}] Failed. Error:\n{error or 'Unknown Error'}")
                        final_status = f"Cmd Failed"

                    uptime = pc.get('uptime', 'N/A')
                    disk_free = pc.get('disk_free', 'N/A')
                    if success:
                        uptime, disk_free = self._check_status_data(pc)

                    self.db_manager.update_status(pc_id, final_status, current_time, pending_updates, uptime, disk_free)
                    self.master.after(0, lambda p=pc_id, s=final_status, t=current_time, u=pending_updates, up=uptime, df=disk_free: self._update_pc_row_data(p, s, t, u, up, df))

                    self.log_message(f"[COMPLETED TASK] {alias} ({host}) command finished.")
                    continue

                if action == "update":
                    update_script = "DEBIAN_FRONTEND=noninteractive apt update && DEBIAN_FRONTEND=noninteractive apt upgrade -y"
                    cmd = f"sudo sh -c '{update_script}'"
                elif action == "deploy":
                    cmd = command
                elif action == "check_status":
                    cmd = "echo OK"
                else:
                    self.log_message(f"[ERROR] Unknown action: {action}")
                    continue

                success, status = self._run_ssh_command(pc, cmd)

                new_status = "OK" if success else f"Failed: {status}"
                current_time = self._get_current_time_str()

                pending_updates = 0
                uptime = pc.get('uptime', 'N/A')
                disk_free = pc.get('disk_free', 'N/A')

                if success:
                    pending_updates = self._check_pending_updates_count(pc)
                    uptime, disk_free = self._check_status_data(pc)

                self.db_manager.update_status(pc_id, new_status, current_time, pending_updates, uptime, disk_free)
                self.master.after(0, lambda p=pc_id, s=new_status, t=current_time, u=pending_updates, up=uptime, df=disk_free: self._update_pc_row_data(p, s, t, u, up, df))

                self.log_message(f"[COMPLETED TASK] {alias} ({host}) status: {new_status}")

        threading.Thread(target=action_worker).start()

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

    def _check_status_data(self, pc_info):
        command = self.STATUS_CHECK_CMD

        output, error, success = self._run_ssh_command_with_output(pc_info, command)

        uptime = 'N/A'
        disk_free = 'N/A'

        if success and output:
            lines = output.splitlines()
            if len(lines) >= 2:
                raw_uptime = lines[0].strip()
                if raw_uptime.startswith("up "):
                    uptime = raw_uptime.split(" ", 1)[1].strip()
                else:
                    self.log_message(f"[WARN] Failed to parse Uptime for {pc_info['alias']}: '{lines[0]}'")

                disk_free = lines[-1].strip()
                if not disk_free:
                    disk_free = 'N/A'

                self.log_message(f"[INFO] Metrics for {pc_info['alias']}: Uptime={uptime}, DiskFree={disk_free}")
        else:
            self.log_message(f"[WARN] Failed to fetch status data for {pc_info['alias']}: {error or 'SSH Error'}")

        return uptime, disk_free

    def _run_ssh_command(self, pc_info, command):
        host = pc_info["hostname"]
        user = pc_info["username"]
        alias = pc_info["alias"]

        connect_host = host if '.' in host else f"{host}.local"
        password = self.encryption_util.decrypt(pc_info["password_encrypted"])

        if not isinstance(password, str):
            self.log_message(f"[FATAL] Auth failed for '{alias}'. Invalid password key.")
            return False, "Authentication Error"

        original_command = command

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

                stdout_data = stdout.read().decode().strip()
                stderr_data = stderr.read().decode().strip()

                exit_status = stdout.channel.recv_exit_status()

            else:
                stdin, stdout, stderr = ssh.exec_command(command)
                stdout_data = stdout.read().decode().strip()
                stderr_data = stderr.read().decode().strip()
                exit_status = stdout.channel.recv_exit_status()

            warning_text = 'WARNING: apt does not have a stable CLI interface. Use with caution in scripts.'

            clean_stderr = stderr_data.replace(warning_text, '').strip()

            prompt_text = f"[sudo] password for {user}:"
            clean_stderr = clean_stderr.replace(prompt_text, '').strip()

            if "password is required" in clean_stderr.lower() or "authentication failure" in clean_stderr.lower():
                self.log_message(f"[SSH FAIL] {alias}: Authentication or Sudo password failed (Exit {exit_status}).")
                return False, clean_stderr if clean_stderr else "Authentication Failed (Sudo)"

            combined_output = f"{stdout_data}\n{clean_stderr}".strip()

            if exit_status == 0:
                self.log_message(f"[SSH OK] {alias} ({original_command.split()[0]}): ** Success **")
                return True, combined_output

            if exit_status == 1 and original_command == self.UPDATE_CHECK_CMD and not clean_stderr:
                self.log_message(f"[SSH OK] {alias} (update check): Success ( 0 updates found).")
                return True, combined_output

            else:
                if 'reboot' in original_command or 'shutdown' in original_command:
                    self.log_message(f"[SSH OK] {alias}: Connection dropped after command execution (Expected).")
                    return True, "Connection dropped (Expected)"

                if clean_stderr:
                    self.log_message(f"[SSH FAIL] {alias}: Command failed (Exit {exit_status}). Error: {clean_stderr.splitlines()[-1]}")
                    return False, combined_output

                self.log_message(f"[SSH FAIL] {alias}: Command failed (Exit {exit_status}). No output error.")
                return False, f"Command failed with exit code {exit_status}. No output error.\n{combined_output}"

        except paramiko.AuthenticationException:
            self.log_message(f"[SSH FAIL] {alias}: Authentication failed.")
            return False, "Authentication Failed"
        except paramiko.SSHException as e:
            self.log_message(f"[SSH FAIL] {alias}: SSH Error ({e})")
            return False, f"SSH Error: {e}"
        except Exception as e:
            if 'reboot' in original_command or 'shutdown' in original_command:
                if 'connection reset by peer' in str(e).lower() or 'timed out' in str(e).lower() or 'was not established' in str(e).lower():
                    self.log_message(f"[SSH OK] {alias}: Connection dropped after command execution (Expected for {original_command.split()[1]}).")
                    return True, "Connection dropped (Expected)"

            self.log_message(f"[SSH FAIL] {alias}: Connection Error ({e})")
            return False, f"Connection Error: {e}"
        finally:
            try:
                ssh.close()
            except:
                pass

    def _check_pending_updates_count(self, pc_info):
        command = self.UPDATE_CHECK_CMD
        success, output = self._run_ssh_command(pc_info, command)

        if success:
            try:
                count_line = output.splitlines()[-1]
                count = int(count_line.strip())
                return count
            except (ValueError, IndexError):
                self.log_message(f"[WARN] Failed to parse update count for {pc_info['alias']}. Output: {output}. Assuming 0.")
                return 0
        else:
            self.log_message(f"[WARN] Failed to count updates for {pc_info['alias']}. Assuming No Updates.")
            return 0

    def on_refresh_clicked(self, event=None):
        self.log_message("[INFO] Checking status, updates, and metrics for all PCs...")
        pc_list = self.pc_list_data
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

        threading.Thread(target=worker).start()
        self.master.focus()

    def run_revert_action(self, target_ids):
        pc_id = target_ids[0]
        pc = next((p for p in self.pc_list_data if p["id"] == pc_id), None)

        def worker():
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

            success, status = self._run_ssh_command(pc, removal_command)

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

        threading.Thread(target=worker).start()
        self.master.focus()
        
    def on_send_file_clicked(self, event=None):
        # 1. Keep your existing selection check
        if len(self.selected_pc_ids) != 1:
            self.log_message("[ERROR] Select exactly ONE PC to send a file to.")
            return

        pc_id = self.selected_pc_ids[0]
        # Use your existing pc_info lookup logic
        pc_info = next((p for p in self.pc_list_data if p["id"] == pc_id), None)
        if not pc_info:
            self.log_message(f"[ERROR] PC with ID {pc_id} not found.")
            return

        # 2. Open the updated dialog
        dialog = SendFileDialog(self.master, pc_info["alias"])
        result, local_file_path = dialog.show()

        # 3. Handle the updated result (which is now a dictionary)
        if result and local_file_path:
            if not os.path.exists(local_file_path):
                self.log_message(f"[ERROR] Local file not found: {local_file_path}")
                return

            # Extract the checkbox value
            execute_after = result.get('execute_after', False)
            
            self.log_message(f"[TASK] Initiating file transfer to {pc_info['alias']}: {os.path.basename(local_file_path)}")
            
            # 4. Start the thread with the 3 required arguments
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
