import argparse
import logging
import sys
import time
import serial
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from serial.serialutil import SerialException
from collections import deque
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import numpy as np
from scipy import signal
import csv

# Signal Processing Toolkit for EOG
# Requirements: pyserial, matplotlib, numpy, scipy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")


class SerialGUIApp:
    def __init__(self, root, port="COM5", baud=115200, timeout=1.0, reconnect_delay=2.0):
        self.root = root
        self.root.title("Signal Processing Toolkit - EOG Analysis")
        self.root.geometry("1200x800")
        # Apply a clean ttk theme and small style tweaks
        try:
            style = ttk.Style()
            style.theme_use('clam')
            style.configure('TLabel', padding=2)
            style.configure('TButton', padding=3)
            style.configure('TLabelframe', padding=(6, 4))
        except Exception:
            pass
        
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.reconnect_delay = reconnect_delay
        
        self.listening = False
        self.serial_thread = None
        self.data_buffer = deque(maxlen=500)  # Reduced buffer to lower CPU/memory
        # Session buffer: collect samples from Start -> Stop for exporting
        self.session_data = []

        # View modes
        self.show_time = tk.BooleanVar(value=True)
        self.show_fft = tk.BooleanVar(value=False)

        # Redraw / performance tuning
        self.update_rate_hz = 5  # max redraws per second
        self._redraw_job = None
        self.lines = {}  # persistent Line2D objects for incremental updates

        # UI-bound performance controls (sliders)
        self.redraw_rate_var = tk.IntVar(value=self.update_rate_hz)

        # Signal parameters
        self.sampling_frequency = 100.0
        
        # Filter parameters
        self.filter_type = tk.StringVar(value="none")
        self.filter_order = tk.IntVar(value=4)
        self.filter_low = tk.DoubleVar(value=1.0)
        self.filter_high = tk.DoubleVar(value=40.0)
        self.filter_notch = tk.DoubleVar(value=50.0)
        
        # Window function
        self.window_func = tk.StringVar(value="hann")
        
        # Normalization
        self.normalize = tk.BooleanVar(value=False)
        self.detrend = tk.BooleanVar(value=False)
        
        self._create_widgets()
        
    def _create_widgets(self):
        """Create main GUI layout with organized sections"""
        # Main container
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # LEFT PANEL: Controls (fixed width)
        left_frame = ttk.Frame(main_paned, width=320)
        main_paned.add(left_frame, weight=0)
        # Prevent left frame from shrinking to content
        left_frame.pack_propagate(False)
        
        # RIGHT PANEL: Graphs
        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=1)
        
        # Create scrollable control panel
        canvas = tk.Canvas(left_frame, bg="white")
        scrollbar = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Build control sections in scrollable frame
        self._build_serial_controls(scrollable_frame)
        self._build_view_controls(scrollable_frame)
        self._build_performance_controls(scrollable_frame)
        self._build_filter_controls(scrollable_frame)
        self._build_analysis_controls(scrollable_frame)
        self._build_export_controls(scrollable_frame)
        
        # (graph frame will be created inside graph_container below)
        
        # Initialize graph placeholders (layout will be created after graph_frame exists)
        self.canvas = None
        self.fig = None
        self.axes = {}
        
        # Status (kept on left panel)
        status_frame = ttk.LabelFrame(left_frame, text="Status", height=100)
        status_frame.pack(fill=tk.X, padx=5, pady=5)

        self.status_label = ttk.Label(status_frame, text="Status: Stopped", foreground="red")
        self.status_label.pack(pady=5)

        self.value_label = ttk.Label(status_frame, text="Last value: N/A")
        self.value_label.pack(pady=2)

        # Graph container on right: graphs on top, log below
        self.graph_container = ttk.Frame(right_frame)
        self.graph_container.pack(fill=tk.BOTH, expand=True)

        # Graph frame (top)
        self.graph_frame = ttk.Frame(self.graph_container)
        self.graph_frame.pack(fill=tk.BOTH, expand=True)

        # Now that the graph frame exists, build the initial figure layout
        self._update_layout()

        # Log frame (below graphs)
        log_frame_right = ttk.LabelFrame(self.graph_container, text="Log", height=150)
        log_frame_right.pack(fill=tk.X, padx=5, pady=5)
        
        scrollbar = ttk.Scrollbar(log_frame_right)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text = tk.Text(log_frame_right, height=8, yscrollcommand=scrollbar.set)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.log_text.yview)
        
    def _build_serial_controls(self, parent):
        """Serial port connection controls"""
        frame = ttk.LabelFrame(parent, text="Serial Connection")
        frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Port
        ttk.Label(frame, text="Port:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.port_var = tk.StringVar(value=self.port)
        port_combo = ttk.Combobox(frame, textvariable=self.port_var, width=15)
        port_combo.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=3)
        
        # Baud
        ttk.Label(frame, text="Baud:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self.baud_var = tk.StringVar(value=str(self.baud))
        baud_combo = ttk.Combobox(frame, textvariable=self.baud_var, 
                                   values=["9600", "19200", "38400", "57600", "115200"], width=15)
        baud_combo.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=3)
        
        # Sampling frequency
        ttk.Label(frame, text="Fs (Hz):").grid(row=2, column=0, sticky=tk.W, padx=5, pady=3)
        self.fs_var = tk.StringVar(value="100.0")
        fs_entry = ttk.Entry(frame, textvariable=self.fs_var, width=17)
        fs_entry.grid(row=2, column=1, sticky=tk.EW, padx=5, pady=3)
        
        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=2, sticky=tk.EW, padx=5, pady=5)
        
        self.start_btn = ttk.Button(btn_frame, text="Start", command=self._start_listening)
        self.start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        
        self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self._stop_listening, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        
        frame.columnconfigure(1, weight=1)
        
    def _build_view_controls(self, parent):
        """View/visualization controls"""
        frame = ttk.LabelFrame(parent, text="Visualization")
        frame.pack(fill=tk.X, padx=5, pady=5)
        
        time_check = ttk.Checkbutton(frame, text="Time Domain", variable=self.show_time, command=self._update_layout)
        time_check.pack(anchor=tk.W, padx=5, pady=2)
        
        fft_check = ttk.Checkbutton(frame, text="FFT", variable=self.show_fft, command=self._update_layout)
        fft_check.pack(anchor=tk.W, padx=5, pady=2)
        
        # spectrogram removed to reduce CPU load
        
        # Window function
        ttk.Label(frame, text="Window:").pack(anchor=tk.W, padx=5, pady=(5, 2))
        window_combo = ttk.Combobox(frame, textvariable=self.window_func, 
                                     values=["hann", "hamming", "blackman", "bartlett", "rectangular"],
                                     state="readonly", width=20)
        window_combo.pack(fill=tk.X, padx=5, pady=2)
        
    def _build_filter_controls(self, parent):
        """Signal filtering controls"""
        frame = ttk.LabelFrame(parent, text="Filtering")
        frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Filter type
        ttk.Label(frame, text="Type:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        filter_combo = ttk.Combobox(frame, textvariable=self.filter_type,
                                     values=["none", "lowpass", "highpass", "bandpass", "notch"],
                                     state="readonly", width=18)
        filter_combo.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=3)
        # redraw when filter type changes
        try:
            filter_combo.bind('<<ComboboxSelected>>', lambda e: self._draw_graph())
        except Exception:
            pass
        
        # Order
        ttk.Label(frame, text="Order:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=3)
        order_spin = ttk.Spinbox(frame, from_=1, to=10, textvariable=self.filter_order, width=20)
        order_spin.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=3)
        try:
            self.filter_order.trace_add('write', lambda *a: self._draw_graph())
        except Exception:
            try:
                self.filter_order.trace('w', lambda *a: self._draw_graph())
            except Exception:
                pass
        
        # Low frequency
        ttk.Label(frame, text="Low (Hz):").grid(row=2, column=0, sticky=tk.W, padx=5, pady=3)
        low_entry = ttk.Entry(frame, textvariable=self.filter_low, width=20)
        low_entry.grid(row=2, column=1, sticky=tk.EW, padx=5, pady=3)
        low_entry.bind('<Return>', lambda e: self._draw_graph())
        low_entry.bind('<FocusOut>', lambda e: self._draw_graph())
        
        # High frequency
        ttk.Label(frame, text="High (Hz):").grid(row=3, column=0, sticky=tk.W, padx=5, pady=3)
        high_entry = ttk.Entry(frame, textvariable=self.filter_high, width=20)
        high_entry.grid(row=3, column=1, sticky=tk.EW, padx=5, pady=3)
        high_entry.bind('<Return>', lambda e: self._draw_graph())
        high_entry.bind('<FocusOut>', lambda e: self._draw_graph())
        
        # Notch frequency
        ttk.Label(frame, text="Notch (Hz):").grid(row=4, column=0, sticky=tk.W, padx=5, pady=3)
        notch_entry = ttk.Entry(frame, textvariable=self.filter_notch, width=20)
        notch_entry.grid(row=4, column=1, sticky=tk.EW, padx=5, pady=3)
        notch_entry.bind('<Return>', lambda e: self._draw_graph())
        notch_entry.bind('<FocusOut>', lambda e: self._draw_graph())
        
        # Checkboxes
        normalize_check = ttk.Checkbutton(frame, text="Normalize", variable=self.normalize, command=self._draw_graph)
        normalize_check.grid(row=5, column=0, columnspan=2, sticky=tk.W, padx=5, pady=3)
        
        detrend_check = ttk.Checkbutton(frame, text="Detrend", variable=self.detrend, command=self._draw_graph)
        detrend_check.grid(row=6, column=0, columnspan=2, sticky=tk.W, padx=5, pady=3)
        
        frame.columnconfigure(1, weight=1)
        
    def _build_analysis_controls(self, parent):
        """Signal analysis and statistics"""
        frame = ttk.LabelFrame(parent, text="Analysis")
        frame.pack(fill=tk.X, padx=5, pady=5)
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(btn_frame, text="Statistics", command=self._show_statistics).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Clear Data", command=self._clear_data).pack(fill=tk.X, pady=2)
        
    def _build_export_controls(self, parent):
        """Data export controls"""
        frame = ttk.LabelFrame(parent, text="Export")
        frame.pack(fill=tk.X, padx=5, pady=5)
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Export button starts disabled; enabled only after a listening session stops
        self.export_btn = ttk.Button(btn_frame, text="Export CSV", command=self._export_csv, state=tk.DISABLED)
        self.export_btn.pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Load CSV", command=self._load_csv).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Save Figure", command=self._save_figure).pack(fill=tk.X, pady=2)
        
    def _build_performance_controls(self, parent):
        """Performance tuning controls: redraw rate and buffer size (sliders)"""
        frame = ttk.LabelFrame(parent, text="Performance")
        frame.pack(fill=tk.X, padx=5, pady=5)
        # Update rate slider (Hz) with numeric readout
        ttk.Label(frame, text="Update Rate (Hz):").pack(anchor=tk.W, padx=5, pady=(4, 0))
        rate_row = ttk.Frame(frame)
        rate_row.pack(fill=tk.X, padx=6, pady=2)
        rate_scale = tk.Scale(rate_row, from_=1, to=30, orient=tk.HORIZONTAL,
                              variable=self.redraw_rate_var, command=self._on_update_rate_change)
        rate_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.rate_value_label = ttk.Label(rate_row, text=str(self.redraw_rate_var.get()), width=5)
        self.rate_value_label.pack(side=tk.RIGHT, padx=(6,0))

        # Buffer size with Apply button
        ttk.Label(frame, text="Buffer Size:").pack(anchor=tk.W, padx=5, pady=(8, 0))
        buf_row = ttk.Frame(frame)
        buf_row.pack(fill=tk.X, padx=6, pady=2)
        self.buffer_size_entry = ttk.Entry(buf_row, width=15)
        self.buffer_size_entry.insert(0, str(self.data_buffer.maxlen))
        self.buffer_size_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(buf_row, text="Apply", command=self._apply_buffer_size).pack(side=tk.LEFT, padx=(4, 0))

        # Small note
        ttk.Label(frame, text="Adjust to trade performance vs. resolution", foreground="gray").pack(anchor=tk.W, padx=5, pady=(6,4))
        
    def _start_listening(self):
        self.port = self.port_var.get()
        self.baud = int(self.baud_var.get())
        
        if self.listening:
            return
        
        self.listening = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_label.config(text="Status: Listening", foreground="green")
        # clear session data for a fresh recording session and disable export
        try:
            self.session_data.clear()
        except Exception:
            self.session_data = []
        try:
            self.export_btn.config(state=tk.DISABLED)
        except Exception:
            pass
        
        self.serial_thread = threading.Thread(target=self._read_serial, daemon=True)
        self.serial_thread.start()
        # start periodic redraws (throttled)
        self._start_redraw_loop()
        
    def _stop_listening(self):
        self.listening = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_label.config(text="Status: Stopped", foreground="red")
        self._log("Listening stopped")
        # stop redraw loop
        self._stop_redraw_loop()
        # enable export now that the session has ended (if we have data)
        try:
            if len(self.session_data) > 0:
                self.export_btn.config(state=tk.NORMAL)
        except Exception:
            pass
        
    def _read_serial(self):
        """Read from serial port in background thread"""
        while self.listening:
            try:
                self._log(f"Opening serial port {self.port} @ {self.baud} baud")
                with serial.Serial(self.port, baudrate=self.baud, timeout=self.timeout) as ser:
                    self._log("Serial port opened, reading...")
                    while self.listening:
                        try:
                            raw = ser.readline()
                        except SerialException as e:
                            self._log(f"Serial read error: {e}")
                            break
                        
                        if not raw:
                            continue
                        
                        try:
                            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                        except Exception:
                            line = repr(raw)
                        
                        try:
                            value = float(line)
                            self.data_buffer.append(value)
                            # record into session buffer (Start->Stop)
                            try:
                                self.session_data.append(value)
                            except Exception:
                                # ensure session_data exists
                                self.session_data = [value]
                            self.root.after(0, self._update_graph_and_label, value, line)
                        except ValueError:
                            self._log(f"Data: {line}")
                            self.root.after(0, self._update_label_text, line)
                            
            except SerialException as e:
                self._log(f"Could not open serial port {self.port}: {e}")
            except Exception as e:
                self._log(f"Unexpected error: {e}")
            
            if self.listening:
                self._log(f"Waiting {self.reconnect_delay} seconds before retry...")
                time.sleep(self.reconnect_delay)
    
    def _update_graph_and_label(self, value, line):
        """Update graph and value label"""
        self.value_label.config(text=f"Last value: {value:.4f} (Buf: {len(self.data_buffer)})")
        self._log(f"Data: {line}")
        # do not draw on every sample; redraw loop will update at throttled rate
    
    def _update_label_text(self, line):
        """Update label with text"""
        self._log(f"Data: {line}")

    # --- Performance control handlers ---
    def _on_update_rate_change(self, value):
        try:
            new_rate = int(float(value))
        except Exception:
            return
        self.update_rate_hz = max(1, new_rate)
        # If a redraw loop is active, restart it so the new interval takes effect
        if self._redraw_job is not None:
            self._stop_redraw_loop()
            self._start_redraw_loop()
        # update numeric label
        try:
            self.rate_value_label.config(text=str(self.update_rate_hz))
        except Exception:
            pass
        self._log(f"Update rate set to {self.update_rate_hz} Hz")

    def _apply_buffer_size(self):
        """Apply new buffer size from the textbox"""
        try:
            new_size = int(self.buffer_size_entry.get())
        except ValueError:
            messagebox.showwarning("Invalid Input", "Buffer size must be an integer")
            return

        new_size = max(10, new_size)  # minimum 10 samples
        
        # Preserve current data and create new deque with new maxlen
        old_data = list(self.data_buffer)
        new_deque = deque(old_data[-new_size:], maxlen=new_size)
        self.data_buffer = new_deque
        
        # Update the entry field to reflect the actual applied value
        self.buffer_size_entry.delete(0, tk.END)
        self.buffer_size_entry.insert(0, str(new_size))
        
        self._log(f"Buffer size changed to {new_size}")
        self._draw_graph()

    
    
    def _update_layout(self):
        """Update figure layout based on selected views"""
        if self.canvas:
            self.canvas.get_tk_widget().destroy()
            if self.fig:
                self.fig.clear()

        # Clear persistent lines to avoid stale references when rebuilding axes
        self.lines = {}

        num_plots = sum([self.show_time.get(), self.show_fft.get()])
        
        if num_plots == 0:
            self.show_time.set(True)
            num_plots = 1
        
        if num_plots == 1:
            rows, cols = 1, 1
        elif num_plots == 2:
            rows, cols = 1, 2
        else:
            rows, cols = 2, 2
        
        figsize_width = 12 if cols == 2 else 6
        figsize_height = 5 if rows == 1 else 10
        self.fig = Figure(figsize=(figsize_width, figsize_height), dpi=100)
        
        self.axes = {}
        plot_idx = 1
        
        if self.show_time.get():
            ax = self.fig.add_subplot(rows, cols, plot_idx)
            ax.set_title("Time Domain")
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Value")
            ax.grid(True)
            # create an empty persistent Line2D for fast updates
            line, = ax.plot([], [], linewidth=1)
            self.axes['time'] = ax
            self.lines['time'] = line
            plot_idx += 1
        
        if self.show_fft.get():
            ax = self.fig.add_subplot(rows, cols, plot_idx)
            ax.set_title("FFT")
            ax.set_xlabel("Frequency (Hz)")
            ax.set_ylabel("Magnitude")
            ax.grid(True)
            # create persistent Line2D for FFT
            line, = ax.plot([], [], linewidth=1)
            self.axes['fft'] = ax
            self.lines['fft'] = line
            plot_idx += 1
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.graph_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        self._draw_graph()
    
    def _get_processed_data(self):
        # Keep this for backward-compatibility: return processed data for current display buffer
        if not self.data_buffer:
            return None

        try:
            fs = float(self.fs_var.get())
        except ValueError:
            fs = self.sampling_frequency

        data = np.array(list(self.data_buffer))
        processed = self._apply_processing(data, fs)
        return processed, fs

    def _apply_processing(self, data, fs):
        """Apply detrend, filtering, and normalization to an arbitrary numpy array and return processed array."""
        if data is None or len(data) == 0:
            return data

        d = np.array(data, copy=True)

        # Detrend
        if self.detrend.get():
            try:
                d = signal.detrend(d)
            except Exception as e:
                self._log(f"Detrend error: {e}")

        # Apply filter
        filter_type = self.filter_type.get()
        if filter_type != "none":
            try:
                order = self.filter_order.get()

                if filter_type == "lowpass":
                    freq = self.filter_high.get()
                    nyquist = fs / 2
                    normalized_freq = freq / nyquist
                    if 0 < normalized_freq < 1:
                        b, a = signal.butter(order, normalized_freq, btype='low')
                        padlen = 3 * (max(len(a), len(b)) - 1)
                        if len(d) > padlen:
                            d = signal.filtfilt(b, a, d)
                        else:
                            self._log(f"Filter skipped: need >{padlen} samples, have {len(d)}")

                elif filter_type == "highpass":
                    freq = self.filter_low.get()
                    nyquist = fs / 2
                    normalized_freq = freq / nyquist
                    if 0 < normalized_freq < 1:
                        b, a = signal.butter(order, normalized_freq, btype='high')
                        padlen = 3 * (max(len(a), len(b)) - 1)
                        if len(d) > padlen:
                            d = signal.filtfilt(b, a, d)
                        else:
                            self._log(f"Filter skipped: need >{padlen} samples, have {len(d)}")

                elif filter_type == "bandpass":
                    low = self.filter_low.get()
                    high = self.filter_high.get()
                    nyquist = fs / 2
                    if 0 < low < high < nyquist:
                        b, a = signal.butter(order, [low/nyquist, high/nyquist], btype='band')
                        padlen = 3 * (max(len(a), len(b)) - 1)
                        if len(d) > padlen:
                            d = signal.filtfilt(b, a, d)
                        else:
                            self._log(f"Filter skipped: need >{padlen} samples, have {len(d)}")

                elif filter_type == "notch":
                    freq = self.filter_notch.get()
                    Q = 30
                    b, a = signal.iirnotch(freq, Q, fs)
                    padlen = 3 * (max(len(a), len(b)) - 1)
                    if len(d) > padlen:
                        d = signal.filtfilt(b, a, d)
                    else:
                        self._log(f"Notch skipped: need >{padlen} samples, have {len(d)}")
            except Exception as e:
                self._log(f"Filter error: {e}")

        # Normalize
        if self.normalize.get() and len(d) > 0:
            try:
                d = (d - np.mean(d)) / (np.std(d) + 1e-10)
            except Exception as e:
                self._log(f"Normalize error: {e}")

        return d
    
    def _draw_graph(self):
        """Redraw all visible graphs"""
        # Use incremental updates and only compute heavy transforms when needed
        if not self.data_buffer or not self.axes:
            return

        result = self._get_processed_data()
        if result is None:
            return

        data, fs = result

        # Time domain incremental update
        if 'time' in self.axes and 'time' in self.lines:
            ax = self.axes['time']
            line = self.lines['time']
            time_axis = np.arange(len(data)) / fs
            line.set_data(time_axis, data)
            ax.relim()
            ax.autoscale_view()
            ax.set_title(f"Time Domain (Fs = {fs} Hz, N = {len(data)})")

        # FFT incremental update (compute only if FFT view is visible)
        if 'fft' in self.axes and 'fft' in self.lines:
            ax = self.axes['fft']
            line = self.lines['fft']
            if len(data) > 0:
                window = signal.get_window(self.window_func.get(), len(data))
                windowed_data = data * window
                fft = np.abs(np.fft.fft(windowed_data))
                frequencies = np.fft.fftfreq(len(data), 1 / fs)
                positive_freq_idx = len(frequencies) // 2
                freqs = frequencies[:positive_freq_idx]
                mags = fft[:positive_freq_idx]
                line.set_data(freqs, mags)
                ax.relim()
                ax.autoscale_view()
                ax.set_title(f"FFT ({self.window_func.get()} window)")

        # draw idle (throttled loop schedules these updates)
        try:
            self.canvas.draw_idle()
        except Exception:
            # fallback to draw if idle isn't available
            self.canvas.draw()
    
    def _show_statistics(self):
        """Show signal statistics"""
        # Use full-session data if available, otherwise use the current data buffer
        if hasattr(self, 'session_data') and len(self.session_data) > 0:
            raw = np.array(self.session_data)
        else:
            raw = np.array(list(self.data_buffer))

        if raw is None or len(raw) == 0:
            messagebox.showwarning("Info", "No data to analyze")
            return

        try:
            fs = float(self.fs_var.get())
        except ValueError:
            fs = self.sampling_frequency

        data = self._apply_processing(raw, fs)
        
        # Calculate statistics
        mean = np.mean(data)
        std = np.std(data)
        min_val = np.min(data)
        max_val = np.max(data)
        rms = np.sqrt(np.mean(data**2))
        peak_to_peak = max_val - min_val
        
        # Frequency analysis
        if len(data) > 0:
            fft = np.abs(np.fft.fft(data))
            frequencies = np.fft.fftfreq(len(data), 1/fs)
            dominant_idx = np.argmax(fft[:len(fft)//2])
            dominant_freq = frequencies[dominant_idx]
        else:
            dominant_freq = 0.0
        
        stats_text = f"""
Signal Statistics:
━━━━━━━━━━━━━━━━━━━━━━━━
Samples: {len(data)}
Duration: {len(data)/fs:.3f} s
Sampling Rate: {fs} Hz

Time Domain:
  Mean: {mean:.6f}
  Std Dev: {std:.6f}
  Min: {min_val:.6f}
  Max: {max_val:.6f}
  Peak-to-Peak: {peak_to_peak:.6f}
  RMS: {rms:.6f}

Frequency Domain:
  Dominant Freq: {dominant_freq:.2f} Hz
"""
        
        # Create popup window
        stats_window = tk.Toplevel(self.root)
        stats_window.title("Signal Statistics")
        stats_window.geometry("350x350")
        
        text_widget = tk.Text(stats_window, font=("Courier", 10))
        text_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        text_widget.insert(tk.END, stats_text)
        text_widget.config(state=tk.DISABLED)
        
        self._log("Statistics calculated and displayed")

    # --- Redraw scheduling to reduce CPU usage ---
    def _start_redraw_loop(self):
        if self._redraw_job is None:
            interval = int(1000 / max(1, self.update_rate_hz))
            self._redraw_job = self.root.after(interval, self._redraw_tick)

    def _stop_redraw_loop(self):
        if self._redraw_job is not None:
            try:
                self.root.after_cancel(self._redraw_job)
            except Exception:
                pass
            self._redraw_job = None

    def _redraw_tick(self):
        # Perform a single redraw tick and re-schedule if still listening
        try:
            self._draw_graph()
        except Exception as e:
            self._log(f"Redraw error: {e}")
        
        if self.listening:
            interval = int(1000 / max(1, self.update_rate_hz))
            self._redraw_job = self.root.after(interval, self._redraw_tick)
        else:
            self._redraw_job = None
    
    def _clear_data(self):
        """Clear data buffer"""
        self.data_buffer.clear()
        self._log("Data buffer cleared")
        self._draw_graph()
    
    def _export_csv(self):
        """Export data to CSV"""
        # Export only the data collected during the last Start->Stop session
        if not hasattr(self, 'session_data') or len(self.session_data) == 0:
            messagebox.showwarning("Info", "No session data to export. Start and Stop a recording first.")
            return
        
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="signal_data.csv"
        )
        
        if not file_path:
            return
        
        try:
            fs = float(self.fs_var.get())
        except ValueError:
            fs = self.sampling_frequency

        # Use raw session data (Start->Stop)
        data = np.array(self.session_data)
        time_axis = np.arange(len(data)) / fs
        
        with open(file_path, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['Time (s)', 'Value'])
            for t, v in zip(time_axis, data):
                writer.writerow([f'{t:.6f}', f'{v:.6f}'])
        
        self._log(f"Data exported to: {file_path}")
        messagebox.showinfo("Export", "Data exported successfully")
    
    def _load_csv(self):
        """Load data from CSV"""
        file_path = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if not file_path:
            return
        
        try:
            # Read entire file into a list first
            loaded_samples = []
            with open(file_path, 'r') as csvfile:
                reader = csv.reader(csvfile)
                next(reader)  # Skip header
                
                for row in reader:
                    if len(row) >= 2:
                        try:
                            loaded_samples.append(float(row[1]))
                        except ValueError:
                            pass
            
            # Replace data_buffer with a deque sized to hold all loaded samples
            self.data_buffer = deque(loaded_samples, maxlen=len(loaded_samples))
            
            self._log(f"Loaded {len(self.data_buffer)} samples from {file_path}")
            self._draw_graph()
            messagebox.showinfo("Load", f"Loaded {len(self.data_buffer)} samples")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file: {e}")
            self._log(f"Load error: {e}")
    
    def _save_figure(self):
        """Save current figure as image"""
        if not self.fig:
            messagebox.showwarning("Info", "No figure to save")
            return
        
        file_path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG files", "*.png"), ("PDF files", "*.pdf"), ("All files", "*.*")],
            initialfile="signal_figure.png"
        )
        
        if file_path:
            try:
                self.fig.savefig(file_path, dpi=150, bbox_inches='tight')
                self._log(f"Figure saved to: {file_path}")
                messagebox.showinfo("Save", "Figure saved successfully")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save figure: {e}")
                self._log(f"Save error: {e}")
    
    def _log(self, message):
        """Add message to log"""
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()


def main_gui(port="COM5", baud=115200, timeout=1.0, reconnect_delay=2.0):
    """Launch the GUI"""
    root = tk.Tk()
    app = SerialGUIApp(root, port=port, baud=baud, timeout=timeout, reconnect_delay=reconnect_delay)
    root.mainloop()


def parse_args():
    p = argparse.ArgumentParser(description="Signal Processing Toolkit for EOG Analysis")
    p.add_argument("--port", "-p", default="COM5", help="Serial port (e.g. COM5 or /dev/ttyUSB0)")
    p.add_argument("--baud", "-b", type=int, default=115200, help="Baud rate (default: 115200)")
    p.add_argument("--timeout", "-t", type=float, default=1.0, help="Read timeout in seconds")
    p.add_argument("--reconnect-delay", "-r", type=float, default=2.0, help="Seconds to wait before reconnecting")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        main_gui(port=args.port, baud=args.baud, timeout=args.timeout, reconnect_delay=args.reconnect_delay)
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received, exiting.")
        sys.exit(0)
