# Signal Processing Toolkit — EOG (Serial_Read_Signal_Toolkit.py)

A Simple Tkinter GUI for reading numeric signals from a serial port (e.g. EOG), visualizing time-domain and FFT plots, applying simple processing (detrend, filters, normalization), and exporting session data to CSV.
Meant to work with EXGPill connected through arduino to a com port.

**File:** `Serial_Read_Signal_Toolkit.py`

**Quick Summary**
- Reads newline-delimited numeric samples from a serial port.
- Live time-domain plot and FFT (windowed) with throttled redraw to reduce CPU load.
- Basic filtering: lowpass, highpass, bandpass, and notch (SciPy IIR filters).
- Session recording: data collected between Start → Stop is saved for CSV export.
- Load CSV support (loads full file and expands buffer accordingly).

**Features**
- Real-time plotting using `matplotlib` embedded in a `tkinter` window.
- Performance controls: adjustable redraw rate and buffer size (Apply to change).
- Signal processing pipeline via `_apply_processing()` (detrend, filter, normalize).
- Export/Load CSV and Save Figure as PNG/PDF.
- Thread-safe serial reading (background thread + `root.after()` UI updates).

**Dependencies**
- pyserial
- numpy
- scipy
- matplotlib

**Usage**
- From command line:
python Serial_Read_Signal_Toolkit.py --port COM5 --baud 115200 --timeout 1.0 --reconnect-delay 2.0

- Or run without args to use defaults:
python Serial_Read_Signal_Toolkit.py

- CLI options:
  - `--port`, `-p`: serial port (e.g. `COM5` or `/dev/ttyUSB0`).
  - `--baud`, `-b`: baud rate (default: `115200`).
  - `--timeout`, `-t`: serial read timeout (seconds).
  - `--reconnect-delay`, `-r`: seconds to wait before reconnecting.

**GUI Controls (left panel)**
- **Serial Connection**: Port, Baud, Fs (sampling frequency), Start/Stop.
- **Visualization**: Toggle Time Domain and FFT, choose window function.
- **Filtering**: Select filter type, order, cutoff(s), notch frequency; Detrend and Normalize checkboxes.
- **Analysis**: Statistics (full-session), Clear Data.
- **Export**: Export CSV (enabled after Stop if session data exists), Load CSV, Save Figure.
- **Performance**: Update Rate (Hz) and Buffer Size (apply to change).

**Data Flow**
- Incoming serial samples → `data_buffer` (deque for display) and `session_data` (list for Start→Stop recording) → throttled redraw loop calls `_draw_graph()` → processing via `_apply_processing()` before visualization or stats.

**Where to look in the code**
- `SerialGUIApp` class: contains UI builders, serial thread, processing, plotting, and I/O.
- `_read_serial()`: background thread reading serial and appending to buffers.
- `_apply_processing(data, fs)`: central processing pipeline used for plotting and statistics.
- `_draw_graph()`: incremental plotting logic (uses persistent Line2D objects).
- `_export_csv()` / `_load_csv()` / `_save_figure()`: file I/O helpers.

**Troubleshooting**
- If the GUI doesn't show data:
  - Verify the serial port and baud are correct.
  - Check that the device sends plain newline-terminated numeric values.
  - Try increasing the `timeout` value or the `reconnect-delay`.
- If filters fail with errors about signal length: increase the buffer size (performance → Buffer Size) and ensure you have sufficient samples before applying heavy filters.
- If CPU usage is high: lower the Update Rate (Hz) under Performance.
