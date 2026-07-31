import ctypes
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from lada_agent.config_manager import (
    load_config,
    merge_config,
    resolve_config_path,
    sanitize_config_update,
)
from lada_agent.config_manager import save_config as write_config
from lada_agent.lada_client import discover_lada_capabilities

# Import the monitor logic
from lada_agent.lada_monitor import LadaMonitor


class LadaGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("LADA Agent - Windows Manager")
        self.root.geometry("1050x820")
        self.root.configure(bg="#f0f2f5")

        # High DPI Support
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        # Initialize Monitor
        self.monitor = None
        self.monitor_thread = None
        self.is_monitoring = False

        # Load Config
        self.config_path = resolve_config_path()

        self.config = self._load_config()
        self.monitor_directories = self.config.get("monitor_directories", [])
        self.lada_options = self.config.get("lada_options", {})
        self.uptime_kuma = self.config.get("uptime_kuma", {})

        # --- Variables for Settings ---
        self.var_scan_interval = tk.IntVar(value=self.config.get("scan_interval", 10))
        self.var_max_concurrent = tk.IntVar(value=self.config.get("max_concurrent_processes", 2))
        self.var_auto_start = tk.BooleanVar(value=self.config.get("auto_start", False))

        # LADA Options
        self.var_cli_path = tk.StringVar(value=self.config.get("lada_cli_path", "lada-cli"))
        self.var_device = tk.StringVar(value=self.lada_options.get("device") or "auto")
        fp16 = self.lada_options.get("fp16")
        self.var_fp16 = tk.StringVar(value="auto" if fp16 is None else ("on" if fp16 else "off"))

        # Restoration
        self.var_restoration_model = tk.StringVar(
            value=self.lada_options.get("mosaic_restoration_model", "basicvsrpp-v1.2")
        )
        self.var_max_clip_length = tk.IntVar(value=self.lada_options.get("max_clip_length", 180))

        # Detection
        self.var_detection_model = tk.StringVar(
            value=self.lada_options.get("mosaic_detection_model", "v4-fast")
        )
        self.var_detect_face = tk.BooleanVar(
            value=self.lada_options.get("detect_face_mosaics", False)
        )

        # Encoding
        self.var_encoding_preset = tk.StringVar(
            value=self.lada_options.get("encoding_preset") or "auto"
        )
        self.var_encoder = tk.StringVar(value=self.lada_options.get("encoder", ""))
        self.var_encoder_options = tk.StringVar(value=self.lada_options.get("encoder_options", ""))
        self.var_fast_start = tk.BooleanVar(value=self.lada_options.get("mp4_fast_start", False))

        # Advanced
        self.var_temp_dir = tk.StringVar(value=self.lada_options.get("temporary_directory", ""))
        self.var_output_pattern = tk.StringVar(
            value=self.lada_options.get("output_file_pattern", "{orig_file_name}.restored.mp4")
        )
        self.var_restoration_config = tk.StringVar(
            value=self.lada_options.get("mosaic_restoration_config_path", "")
        )
        self.var_extra_cli_args = tk.StringVar(value=self.lada_options.get("extra_cli_args", ""))

        # Uptime Kuma
        self.var_uptime_enabled = tk.BooleanVar(value=self.uptime_kuma.get("enabled", False))
        self.var_uptime_url = tk.StringVar(value=self.uptime_kuma.get("push_url", ""))
        self.var_uptime_interval = tk.IntVar(value=self.uptime_kuma.get("interval_seconds", 60))
        self.var_uptime_status = tk.StringVar(value=self.uptime_kuma.get("status", "up"))
        self.var_uptime_message = tk.StringVar(
            value=self.uptime_kuma.get("message", "LADA Agent online")
        )

        # --- UI Layout ---

        # Apply Theme + Scaling
        try:
            self.root.tk.call("tk", "scaling", 1.2)
        except Exception:
            pass

        self.font_base = ("Segoe UI", 11)
        self.font_small = ("Segoe UI", 10)
        self.font_bold = ("Segoe UI", 11, "bold")
        self.font_title = ("Segoe UI", 18, "bold")

        # Apply Theme
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background="#f0f2f5")
        style.configure("TNotebook.Tab", background="#e2e8f0", padding=[12, 6], font=self.font_bold)
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#1e293b")],
            foreground=[("selected", "white")],
        )

        # Header
        header_frame = tk.Frame(root, bg="#1e293b", height=68)
        header_frame.pack(fill=tk.X)
        header_frame.pack_propagate(False)

        title_label = tk.Label(
            header_frame, text="LADA Agent", font=self.font_title, fg="white", bg="#1e293b"
        )
        title_label.pack(side=tk.LEFT, padx=20)

        self.status_var = tk.StringVar(value="Stopped")
        self.status_label = tk.Label(
            header_frame,
            textvariable=self.status_var,
            font=self.font_bold,
            fg="#ef4444",
            bg="#1e293b",
        )
        self.status_label.pack(side=tk.RIGHT, padx=20)

        # Tabs
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.tab_monitor = tk.Frame(self.notebook, bg="#f0f2f5")
        self.tab_settings = tk.Frame(self.notebook, bg="#f0f2f5")

        self.notebook.add(self.tab_monitor, text="Monitor")
        self.notebook.add(self.tab_settings, text="Settings")

        # --- Monitor Tab ---
        self._build_monitor_tab()

        # --- Settings Tab ---
        self._build_settings_tab()

        # Logs Section (Shared at bottom)
        log_frame = tk.LabelFrame(
            root, text="Logs", font=self.font_bold, bg="#f0f2f5", fg="#334155", padx=10, pady=10
        )
        log_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20), ipady=30)

        self.log_area = scrolledtext.ScrolledText(
            log_frame, state="disabled", height=8, font=("Consolas", 10)
        )
        self.log_area.pack(fill=tk.BOTH, expand=True)

        # Pipe stdout/stderr to log area
        sys.stdout = TextRedirector(self.log_area, "stdout")
        sys.stderr = TextRedirector(self.log_area, "stderr")

        # Setup Polling
        self.root.after(100, self.update_status)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if self.config.get("auto_start"):
            self.root.after(250, self.start_monitoring)

    def _build_monitor_tab(self):
        main_frame = tk.Frame(self.tab_monitor, bg="#f0f2f5", padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Controls Section
        control_frame = tk.LabelFrame(
            main_frame,
            text="Controls",
            font=self.font_bold,
            bg="#f0f2f5",
            fg="#334155",
            padx=12,
            pady=12,
        )
        control_frame.pack(fill=tk.X, pady=(0, 20))

        self.start_btn = tk.Button(
            control_frame,
            text="Start Monitor",
            command=self.start_monitoring,
            bg="#10b981",
            fg="white",
            font=self.font_bold,
            padx=18,
            pady=7,
            borderwidth=0,
            cursor="hand2",
        )
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = tk.Button(
            control_frame,
            text="Stop Monitor",
            command=self.stop_monitoring,
            bg="#ef4444",
            fg="white",
            font=self.font_bold,
            padx=18,
            pady=7,
            borderwidth=0,
            cursor="hand2",
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        # Queue Status in Controls
        tk.Label(control_frame, text="Queue:", bg="#f0f2f5", font=self.font_base).pack(
            side=tk.LEFT, padx=(20, 5)
        )
        self.queue_var = tk.StringVar(value="0")
        tk.Label(
            control_frame, textvariable=self.queue_var, bg="#f0f2f5", font=self.font_bold
        ).pack(side=tk.LEFT)

        # Progress Section
        progress_frame = tk.LabelFrame(
            main_frame,
            text="Current Progress",
            font=self.font_bold,
            bg="#f0f2f5",
            fg="#334155",
            padx=12,
            pady=12,
        )
        progress_frame.pack(fill=tk.X, pady=(0, 20))
        self.progress_var = tk.IntVar(value=0)
        self.progress_text_var = tk.StringVar(value="Idle")
        tk.Label(
            progress_frame, textvariable=self.progress_text_var, bg="#f0f2f5", font=self.font_base
        ).pack(anchor="w")
        self.progress_bar = ttk.Progressbar(
            progress_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            variable=self.progress_var,
        )
        self.progress_bar.pack(fill=tk.X, pady=(6, 0))

        # Configuration Section (Folders)
        config_frame = tk.LabelFrame(
            main_frame,
            text="Monitored Folders",
            font=self.font_bold,
            bg="#f0f2f5",
            fg="#334155",
            padx=12,
            pady=12,
        )
        config_frame.pack(fill=tk.BOTH, expand=True)

        # Buttons for config
        btn_frame = tk.Frame(config_frame, bg="#f0f2f5")
        btn_frame.pack(fill=tk.X, pady=(0, 10))

        tk.Button(
            btn_frame,
            text="+ Add Folder",
            command=self.add_folder,
            bg="#3b82f6",
            fg="white",
            relief=tk.FLAT,
            padx=12,
            pady=6,
            font=self.font_small,
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            btn_frame,
            text="- Remove Selected",
            command=self.remove_folder,
            bg="#64748b",
            fg="white",
            relief=tk.FLAT,
            padx=12,
            pady=6,
            font=self.font_small,
        ).pack(side=tk.LEFT, padx=5)

        # Listbox with Scrollbar
        list_frame = tk.Frame(config_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.folder_list = tk.Listbox(
            list_frame, font=self.font_base, selectmode=tk.SINGLE, borderwidth=1, relief="solid"
        )
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=self.folder_list.yview)
        self.folder_list.config(yscrollcommand=scrollbar.set)

        self.folder_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.refresh_folder_list()

    def _build_settings_tab(self):
        main_scroll = tk.Frame(self.tab_settings, bg="#f0f2f5")
        main_scroll.pack(fill=tk.BOTH, expand=True)

        content_frame = tk.Frame(main_scroll, bg="#f0f2f5", padx=20, pady=20)
        content_frame.pack(fill=tk.BOTH, expand=True)
        content_frame.grid_columnconfigure(0, weight=1, uniform="settings_col")
        content_frame.grid_columnconfigure(1, weight=1, uniform="settings_col")

        left_col = tk.Frame(content_frame, bg="#f0f2f5")
        right_col = tk.Frame(content_frame, bg="#f0f2f5")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right_col.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        # --- System Settings ---
        sys_frame = tk.LabelFrame(
            left_col,
            text="System Options",
            font=self.font_bold,
            bg="#f0f2f5",
            fg="#334155",
            padx=12,
            pady=12,
        )
        sys_frame.pack(fill=tk.X, pady=(0, 15))

        self._add_entry(sys_frame, "Scan Interval (sec):", self.var_scan_interval, 0)
        self._add_entry(sys_frame, "Max Concurrent Processes:", self.var_max_concurrent, 1)
        tk.Checkbutton(
            sys_frame,
            text="Auto Start on Launch",
            variable=self.var_auto_start,
            bg="#f0f2f5",
            font=self.font_base,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        self._add_entry(sys_frame, "LADA CLI Path:", self.var_cli_path, 3, width=46)
        self.probe_lada_button = tk.Button(
            sys_frame,
            text="Detect Lada Options",
            command=self.probe_lada_capabilities,
            bg="#dce9e2",
            relief=tk.FLAT,
            font=self.font_small,
            padx=10,
            pady=5,
        )
        self.probe_lada_button.grid(row=4, column=0, columnspan=2, sticky="w", pady=5, padx=6)

        # --- General & Restoration ---
        res_frame = tk.LabelFrame(
            left_col,
            text="General & Restoration",
            font=self.font_bold,
            bg="#f0f2f5",
            fg="#334155",
            padx=12,
            pady=12,
        )
        res_frame.pack(fill=tk.X, pady=(0, 15))

        self.device_combo = self._add_combo(
            res_frame, "Device:", self.var_device, 0, ["auto", "cpu", "cuda:0", "xpu:0", "mps"]
        )
        self.restoration_model_combo = self._add_combo(
            res_frame, "Restoration Model / Path:", self.var_restoration_model, 1, []
        )
        self._add_entry(res_frame, "Max Clip Length:", self.var_max_clip_length, 2)
        self._add_combo(
            res_frame, "FP16 Inference:", self.var_fp16, 3, ["auto", "on", "off"], readonly=True
        )

        # --- Detection ---
        det_frame = tk.LabelFrame(
            left_col,
            text="Detection",
            font=self.font_bold,
            bg="#f0f2f5",
            fg="#334155",
            padx=12,
            pady=12,
        )
        det_frame.pack(fill=tk.X, pady=(0, 15))

        self.detection_model_combo = self._add_combo(
            det_frame, "Detection Model / Path:", self.var_detection_model, 0, []
        )
        tk.Checkbutton(
            det_frame,
            text="Ignore detected face mosaics (model v3+)",
            variable=self.var_detect_face,
            bg="#f0f2f5",
            font=self.font_base,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=4)

        # --- Encoding ---
        enc_frame = tk.LabelFrame(
            right_col,
            text="Export / Encoding",
            font=self.font_bold,
            bg="#f0f2f5",
            fg="#334155",
            padx=12,
            pady=12,
        )
        enc_frame.pack(fill=tk.X, pady=(0, 15))

        self.encoding_preset_combo = self._add_combo(
            enc_frame, "Encoding Preset:", self.var_encoding_preset, 0, ["auto"], width=40
        )
        tk.Label(
            enc_frame,
            text="(OR Use Custom Encoder below)",
            bg="#f0f2f5",
            fg="#64748b",
            font=self.font_small,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=5)

        self.encoder_combo = self._add_combo(
            enc_frame, "Encoder Name (e.g. libx265):", self.var_encoder, 2, []
        )
        self._add_entry(
            enc_frame, "Encoder Options (e.g. -crf 20):", self.var_encoder_options, 3, width=40
        )
        tk.Checkbutton(
            enc_frame,
            text="MP4 Fast Start (Play while downloading)",
            variable=self.var_fast_start,
            bg="#f0f2f5",
            font=self.font_base,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=4)

        # --- Advanced Paths ---
        adv_frame = tk.LabelFrame(
            right_col,
            text="Advanced / Paths",
            font=self.font_bold,
            bg="#f0f2f5",
            fg="#334155",
            padx=12,
            pady=12,
        )
        adv_frame.pack(fill=tk.X, pady=(0, 15))

        self._add_path_entry(
            adv_frame, "Temp Directory (Optional):", self.var_temp_dir, 0, is_dir=True
        )
        self._add_entry(adv_frame, "Output File Pattern:", self.var_output_pattern, 1, width=40)
        self._add_path_entry(
            adv_frame,
            "Restoration Config (Optional):",
            self.var_restoration_config,
            2,
            is_dir=False,
        )
        self._add_entry(adv_frame, "Advanced CLI Override:", self.var_extra_cli_args, 3, width=40)
        tk.Label(
            adv_frame,
            text="Appended last; input/output and list commands stay protected.",
            bg="#f0f2f5",
            fg="#64748b",
            font=self.font_small,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=6)

        # --- Uptime Kuma ---
        uptime_frame = tk.LabelFrame(
            right_col,
            text="Uptime Kuma",
            font=self.font_bold,
            bg="#f0f2f5",
            fg="#334155",
            padx=12,
            pady=12,
        )
        uptime_frame.pack(fill=tk.X, pady=(0, 15))

        tk.Checkbutton(
            uptime_frame,
            text="Enable Push Monitoring",
            variable=self.var_uptime_enabled,
            bg="#f0f2f5",
            font=self.font_base,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=4)
        self._add_entry(uptime_frame, "Push URL:", self.var_uptime_url, 1, width=46)
        self._add_entry(uptime_frame, "Interval (sec):", self.var_uptime_interval, 2)
        self._add_entry(uptime_frame, "Status (up/down):", self.var_uptime_status, 3)
        self._add_entry(uptime_frame, "Message Template:", self.var_uptime_message, 4, width=40)

        # Actions
        action_frame = tk.Frame(content_frame, bg="#f0f2f5", pady=10)
        action_frame.grid(row=1, column=0, columnspan=2, sticky="ew")

        tk.Button(
            action_frame,
            text="Save Configuration",
            command=self.save_config,
            bg="#8b5cf6",
            fg="white",
            font=self.font_bold,
            padx=18,
            pady=10,
            relief=tk.FLAT,
        ).pack(side=tk.RIGHT)

    def _add_entry(self, parent, label_text, variable, row, width=34):
        tk.Label(parent, text=label_text, bg="#f0f2f5", anchor="w", font=self.font_base).grid(
            row=row, column=0, sticky="w", pady=6, padx=6
        )
        entry = tk.Entry(parent, textvariable=variable, width=width, font=self.font_base)
        entry.grid(row=row, column=1, sticky="w", pady=6, padx=6)
        return entry

    def _add_combo(self, parent, label_text, variable, row, values, width=34, readonly=False):
        tk.Label(parent, text=label_text, bg="#f0f2f5", anchor="w", font=self.font_base).grid(
            row=row, column=0, sticky="w", pady=6, padx=6
        )
        combo = ttk.Combobox(
            parent,
            textvariable=variable,
            values=values,
            width=width,
            state="readonly" if readonly else "normal",
            font=self.font_base,
        )
        combo.grid(row=row, column=1, sticky="w", pady=6, padx=6)
        return combo

    def probe_lada_capabilities(self):
        self.probe_lada_button.configure(state=tk.DISABLED, text="Detecting…")

        def worker():
            result = discover_lada_capabilities(self.var_cli_path.get().strip() or "lada-cli")
            self.root.after(0, lambda: self._apply_lada_capabilities(result))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_lada_capabilities(self, result):
        capabilities = result.get("capabilities", {})

        def values(name, include_auto=False):
            discovered = capabilities.get(name, {}).get("values", [])
            return list(dict.fromkeys((["auto"] if include_auto else []) + discovered))

        self.device_combo.configure(values=values("devices", True))
        self.restoration_model_combo.configure(values=values("restoration_models"))
        self.detection_model_combo.configure(values=values("detection_models"))
        self.encoding_preset_combo.configure(values=values("encoding_presets", True))
        self.encoder_combo.configure(values=values("encoders"))
        self.probe_lada_button.configure(state=tk.NORMAL, text="Detect Lada Options")
        if result.get("ok"):
            messagebox.showinfo("Lada detected", result.get("version") or "Options loaded")
        else:
            messagebox.showerror(
                "Lada detection failed",
                result.get("version_error") or "Check the configured Lada CLI path.",
            )

    def _add_path_entry(self, parent, label_text, variable, row, is_dir=True):
        tk.Label(parent, text=label_text, bg="#f0f2f5", anchor="w", font=self.font_base).grid(
            row=row, column=0, sticky="w", pady=6, padx=6
        )
        frame = tk.Frame(parent, bg="#f0f2f5")
        frame.grid(row=row, column=1, sticky="w", pady=6, padx=6)

        entry = tk.Entry(frame, textvariable=variable, width=40, font=self.font_base)
        entry.pack(side=tk.LEFT, padx=(0, 5))

        def browse():
            if is_dir:
                path = filedialog.askdirectory()
            else:
                path = filedialog.askopenfilename()
            if path:
                variable.set(path)

        browse_btn = tk.Button(
            frame,
            text="Browse",
            command=browse,
            bg="#cbd5e1",
            relief=tk.FLAT,
            font=self.font_small,
            padx=10,
            pady=4,
        )
        browse_btn.pack(side=tk.LEFT)
        return entry, browse_btn

    def _load_config(self):
        config, resolved_path = load_config(self.config_path)
        self.config_path = resolved_path
        if not config:
            print(f"Error loading config: {resolved_path}")
            return merge_config({})
        return config

    def save_config(self):
        try:
            # Update config object from variables
            self.config["monitor_directories"] = self.monitor_directories
            self.config["scan_interval"] = self.var_scan_interval.get()
            self.config["max_concurrent_processes"] = self.var_max_concurrent.get()
            self.config["auto_start"] = self.var_auto_start.get()
            self.config["lada_cli_path"] = self.var_cli_path.get().strip()

            # Update LADA Options
            if "lada_options" not in self.config:
                self.config["lada_options"] = {}

            self.config["lada_options"]["device"] = self.var_device.get().strip() or "auto"
            fp16_mode = self.var_fp16.get()
            self.config["lada_options"]["fp16"] = None if fp16_mode == "auto" else fp16_mode == "on"

            # Restoration
            self.config["lada_options"]["mosaic_restoration_model"] = (
                self.var_restoration_model.get().strip()
            )
            self.config["lada_options"]["max_clip_length"] = self.var_max_clip_length.get()

            # Detection
            self.config["lada_options"]["mosaic_detection_model"] = (
                self.var_detection_model.get().strip()
            )
            self.config["lada_options"]["detect_face_mosaics"] = self.var_detect_face.get()

            # Encoding
            self.config["lada_options"]["encoding_preset"] = (
                self.var_encoding_preset.get().strip() or "auto"
            )
            self.config["lada_options"]["encoder"] = self.var_encoder.get().strip()
            self.config["lada_options"]["encoder_options"] = self.var_encoder_options.get().strip()
            self.config["lada_options"]["mp4_fast_start"] = self.var_fast_start.get()

            # Advanced
            self.config["lada_options"]["temporary_directory"] = self.var_temp_dir.get().strip()
            self.config["lada_options"]["output_file_pattern"] = (
                self.var_output_pattern.get().strip()
            )
            self.config["lada_options"]["mosaic_restoration_config_path"] = (
                self.var_restoration_config.get().strip()
            )
            self.config["lada_options"]["extra_cli_args"] = self.var_extra_cli_args.get().strip()

            # Uptime Kuma
            if "uptime_kuma" not in self.config:
                self.config["uptime_kuma"] = {}
            self.config["uptime_kuma"]["enabled"] = self.var_uptime_enabled.get()
            self.config["uptime_kuma"]["push_url"] = self.var_uptime_url.get().strip()
            self.config["uptime_kuma"]["interval_seconds"] = max(
                1, int(self.var_uptime_interval.get() or 60)
            )
            self.config["uptime_kuma"]["status"] = self.var_uptime_status.get().strip() or "up"
            self.config["uptime_kuma"]["message"] = (
                self.var_uptime_message.get().strip() or "LADA Agent online"
            )

            self.config = sanitize_config_update(self.config, self.config)
            write_config(self.config_path, self.config)
            messagebox.showinfo("Success", "Configuration saved!")

            if self.is_monitoring:
                messagebox.showwarning(
                    "Notice",
                    "Monitor is running. Please restart the monitor for changes to take effect.",
                )

        except Exception as e:
            messagebox.showerror("Error", f"Failed to save config: {e}")

    def add_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            folder = os.path.normpath(folder)
            if folder not in self.monitor_directories:
                self.monitor_directories.append(folder)
                self.refresh_folder_list()
            else:
                messagebox.showinfo("Info", "Folder already in list.")

    def remove_folder(self):
        selection = self.folder_list.curselection()
        if not selection:
            return

        idx = selection[0]
        folder = self.folder_list.get(idx)
        if messagebox.askyesno("Confirm", f"Remove folder?\n{folder}"):
            self.monitor_directories.pop(idx)
            self.refresh_folder_list()

    def refresh_folder_list(self):
        self.folder_list.delete(0, tk.END)
        for folder in self.monitor_directories:
            self.folder_list.insert(tk.END, folder)

    def start_monitoring(self):
        if self.is_monitoring:
            return

        print("Starting monitoring...")

        # Create monitor instance
        self.monitor = LadaMonitor(self.config_path)
        if not self.monitor.config:
            messagebox.showerror("Unable to start", "The configuration could not be loaded.")
            return
        if not self.monitor.acquire_instance_lock():
            messagebox.showerror(
                "Unable to start",
                "Another LADA monitor is already using this configuration.",
            )
            return

        # Start thread
        self.monitor_thread = threading.Thread(target=self.monitor.start_monitoring, daemon=True)
        try:
            self.monitor_thread.start()
        except Exception:
            self.monitor.release_instance_lock()
            raise

        self.is_monitoring = True
        self.start_btn.config(state=tk.DISABLED, bg="#94a3b8")
        self.stop_btn.config(state=tk.NORMAL, bg="#ef4444")
        self.status_var.set("Running")
        self.status_label.config(fg="#10b981")

    def stop_monitoring(self):
        if not self.is_monitoring:
            return

        print("Stopping monitoring...")
        if self.monitor:

            def stop_and_finish():
                self.monitor.stop_monitoring(terminate_running=True)
                if self.monitor_thread and self.monitor_thread.is_alive():
                    self.monitor_thread.join(timeout=20)

            force_stop_thread = threading.Thread(target=stop_and_finish, daemon=True)
            force_stop_thread.start()
            self._stop_thread = force_stop_thread
            self.root.after(100, self._poll_stop)

        self.stop_btn.config(state=tk.DISABLED, bg="#94a3b8")
        self.status_var.set("Stopping")
        self.status_label.config(fg="#f59e0b")

    def _poll_stop(self):
        if getattr(self, "_stop_thread", None) and self._stop_thread.is_alive():
            self.root.after(100, self._poll_stop)
            return
        self._finish_stop()

    def _finish_stop(self):
        self.is_monitoring = False
        self.start_btn.config(state=tk.NORMAL, bg="#10b981")
        self.stop_btn.config(state=tk.DISABLED, bg="#94a3b8")
        self.status_var.set("Stopped")
        self.status_label.config(fg="#ef4444")

    def close(self):
        if self.monitor:
            self.monitor.stop_monitoring(terminate_running=True)
        self.root.destroy()

    def update_status(self):
        if self.is_monitoring and self.monitor:
            try:
                if hasattr(self.monitor, "processing_queue"):
                    q_size = len(self.monitor.processing_queue)
                    self.queue_var.set(str(q_size))
                if (
                    hasattr(self.monitor, "processing_progress")
                    and self.monitor.processing_progress
                ):
                    path, percent = next(iter(self.monitor.processing_progress.items()))
                    self.progress_var.set(int(percent))
                    self.progress_text_var.set(f"{os.path.basename(path)} - {int(percent)}%")
                else:
                    self.progress_var.set(0)
                    self.progress_text_var.set("Idle")
            except Exception:
                pass
        else:
            self.queue_var.set("0")
            self.progress_var.set(0)
            self.progress_text_var.set("Idle")

        self.root.after(1000, self.update_status)


class TextRedirector:
    def __init__(self, widget, tag="stdout"):
        self.widget = widget
        self.tag = tag

    def write(self, str):
        self.widget.configure(state="normal")
        self.widget.insert(tk.END, str, (self.tag,))
        self.widget.see(tk.END)
        self.widget.configure(state="disabled")

    def flush(self):
        pass


if __name__ == "__main__":
    root = tk.Tk()
    app = LadaGUI(root)
    root.mainloop()
