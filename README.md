# esp32-csi-heatmap

A real-time WiFi Channel State Information (CSI) visualizer for the ESP32, featuring a live difference heatmap, automatic baseline calibration, debounced presence detection, and a rolling score graph.

Built for presence and motion sensing experiments using a standard ESP32 WROOM-32 and a home router — no modified firmware, no special hardware required beyond the ESP32 itself.

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

---

## What it does

- Reads CSI data from an ESP32 over USB serial in real time
- Filters out invalid subcarriers (Subcarrier 0 `first_word_invalid` bug, null subcarriers 28–35)
- Performs automatic baseline calibration on startup (no hand/movement during first ~8 seconds)
- Displays a **rolling difference heatmap** (deviation from baseline, not raw amplitude)
- Shows a **score graph** with the auto-calculated detection threshold
- Detects presence using a **debounce mechanism** (N consecutive frames above threshold required)
- Implements a **slowly drifting baseline** to compensate for environmental changes (temperature, channel drift)

![esp32-csi-heatmap](https://github.com/Michdo93/test2/blob/main/esp32_csi.jpeg?raw=true)

---

## Hardware requirements

| Component | Notes |
|---|---|
| ESP32 WROOM-32 | Any ESP32 with CSI support works. ESP32-S3 / C6 recommended for better quality |
| USB cable (data) | Micro-USB for WROOM-32, USB-C for S3/C6 |
| 2.4 GHz WiFi router | Any standard home router |
| PC running Windows / Linux / macOS | For running this visualizer |

> **Note:** For better CSI quality, the **ESP32-S3-DevKitC-1U** or **ESP32-C6-DevKitC-1** with an external antenna are recommended. These provide more subcarriers (128–256 vs 64) and a more uniform antenna pattern.

---

## ESP32 firmware setup

This visualizer reads data from the `csi_recv_router` example from the official [esp-csi](https://github.com/espressif/esp-csi) repository. You need to flash this firmware to your ESP32 first.

### 1. Install ESP-IDF

Follow the official guide for your OS:
[https://docs.espressif.com/projects/esp-idf/en/latest/esp32/get-started/](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/get-started/)

On Windows, use the offline installer available at [https://dl.espressif.com/dl/esp-idf/](https://dl.espressif.com/dl/esp-idf/).

### 2. Clone esp-csi and configure

```bash
git clone https://github.com/espressif/esp-csi.git
cd esp-csi/examples/get-started/csi_recv_router

# Set target (use esp32s3 or esp32c6 if applicable)
idf.py set-target esp32

# Configure WiFi credentials
idf.py menuconfig
# Navigate to: Example Connection Configuration
# Set your WiFi SSID and password
# Save with S, exit with Q
```

### 3. Flash the firmware

```bash
idf.py -p COM3 flash
```

On Windows with a German keyboard: if `Connecting......` appears, hold **BOOT**, briefly press **EN**, release **BOOT**.

### 4. Verify CSI data

```bash
idf.py -p COM3 monitor
```

You should see lines like:
```
CSI_DATA,0,aa:bb:cc:dd:ee:ff,-55,11,...,"[-121,113,...]"
```

Note the baud rate shown in the monitor header (usually `921600`). Exit with **Ctrl+Ü** on German keyboards, **Ctrl+]** on US keyboards.

---

## Visualizer installation

### Requirements

- Python 3.9 or newer
- pip

### Setup

```bash
git clone https://github.com/Michdo93/esp32-csi-heatmap.git
cd esp32-csi-heatmap

python -m venv .

# Windows
.\Scripts\activate

# Linux / macOS
source bin/activate

pip install -r requirements.txt
```

---

## Usage

Make sure the ESP32 is connected via USB and the monitor is **not** running (only one program can access the serial port at a time).

```bash
python csi_heatmap.py --port COM3 --baud 921600
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `--port` / `-p` | `COM3` | Serial port of the ESP32 |
| `--baud` / `-b` | `921600` | Baud rate (check your monitor output) |
| `--threshold` / `-t` | auto | Manual detection threshold (overrides auto-calibration) |
| `--factor` / `-f` | `3.5` | Multiplier for auto-threshold calculation |
| `--list` | — | List available serial ports and exit |

### Examples

```bash
# List available ports
python csi_heatmap.py --list

# Auto-calibration with default settings
python csi_heatmap.py --port COM3 --baud 921600

# More sensitive detection
python csi_heatmap.py --port COM3 --baud 921600 --factor 2.5

# Manual threshold
python csi_heatmap.py --port COM3 --baud 921600 --threshold 5.0

# Linux
python csi_heatmap.py --port /dev/ttyUSB0 --baud 921600
```

---

## How it works

### Calibration phase (~8 seconds)

On startup, the script collects `CALIB_FRAMES` (default: 80) frames while the environment is static. During this time **do not move** and **keep hands away from the ESP32**. The baseline and detection threshold are calculated automatically from this data.

### Detection logic

Each incoming CSI frame is compared to the current baseline. The **score** is the median of the 10 subcarriers with the highest deviation — this is more robust than a simple mean since individual noisy subcarriers are ignored.

The presence state is only changed after **8 consecutive frames** above (or below) the threshold. This debounce mechanism prevents false positives from single WiFi packet anomalies.

### Drifting baseline

While no presence is detected, the baseline slowly adapts to the current environment (`BASELINE_DRIFT = 0.002` per frame). This compensates for slow changes like temperature drift or router channel adjustments, without allowing the hand signal itself to corrupt the baseline.

### Subcarrier filtering

The ESP32 WROOM-32 has two known issues with its CSI output:

- **Subcarrier 0** is affected by the `first_word_invalid` hardware bug and always contains garbage data
- **Subcarriers 28–35** are null subcarriers (guard bands) with no signal

Both are removed, leaving **55 usable subcarriers** out of 64.

---

## Display panels

| Panel | Description |
|---|---|
| **Top left — Difference Heatmap** | Rolling heatmap of `|amplitude - baseline|`. Blue/black = calm, red/yellow = deviation. Time flows upward. |
| **Bottom left — Score Graph** | Green line: detection score over time. Red dashed line: current threshold. |
| **Right — Detection Circle** | Shows calibration progress, then ERKANNT (detected) / LEER (empty) with debounce counter and current score. |

---

## Tuning guide

| Symptom | Solution |
|---|---|
| Triggers constantly (too sensitive) | Increase `--factor` (e.g. `5.0`) or `--threshold` |
| Never triggers | Decrease `--factor` (e.g. `2.0`) or lower `--threshold` |
| Triggers on random WiFi packets | Increase `DEBOUNCE_FRAMES` in the script |
| Baseline drifts too fast | Decrease `BASELINE_DRIFT` in the script |
| No data / black screen | Wrong port or baud rate — check with `--list` |
| `0 CSI_DATA` in monitor | ESP32 not connected to WiFi — reflash with correct SSID/password |

---

## Tested hardware

| Board | Subcarriers | Notes |
|---|---|---|
| ESP32 WROOM-32 | 55 (after filtering) | Works, lower quality |
| ESP32-S3-DevKitC-1U | 128+ | Recommended, better signal |
| ESP32-C6-DevKitC-1 | 256 | Best quality, WiFi 6 |

---

## Known limitations

- A single ESP32 with one antenna cannot localize objects in space — it only detects that *something* changed in the signal path
- Individual fingers cannot be resolved (WiFi wavelength ~12 cm at 2.4 GHz, finger width ~2 cm)
- Detection quality depends heavily on room size, furniture, and router placement
- The ESP32 WROOM-32 has relatively noisy CSI compared to S3/C6 chips

---

## Related projects

- [esp-csi](https://github.com/espressif/esp-csi) — Official Espressif CSI examples (firmware source)
- [ESP32-Realtime-System](https://github.com/RS2002/ESP32-Realtime-System) — Full sensing system with fall/breath/gesture detection
- [nexmon_csi](https://github.com/seemoo-lab/nexmon_csi) — CSI extraction for Raspberry Pi and Asus routers
- [ESPectre](https://github.com/francescopace/espectre) — MQTT-based presence detection for Home Assistant / openHAB

---

## License

MIT License — feel free to use, modify and share.