import serial
import serial.tools.list_ports
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from collections import deque
import re
import argparse
import threading
import time

# ── Konfiguration ────────────────────────────────────────────────────────────
HISTORY_LEN    = 100      # Frames in der Heatmap
SMOOTHING      = 0.25     # Glättung des Rohsignals (0=keine, 1=nur neu)

# Gültige Subcarrier: Subcarrier 0 (first_word_invalid) und Null-SC 28-35 raus
VALID_SUBCARRIERS = list(range(1, 28)) + list(range(36, 64))
N_VALID = len(VALID_SUBCARRIERS)  # 55

# Initiale Kalibrierung
CALIB_FRAMES = 80

# Gleitende Baseline: wie schnell passt sie sich an (0=nie, 1=sofort)
# 0.002 = sehr langsam, passt sich über ~500 Frames an
BASELINE_DRIFT = 0.002

# Debouncing: wie viele aufeinanderfolgende Frames müssen die Schwelle
# überschreiten bevor "ERKANNT" gilt
DEBOUNCE_FRAMES = 8

# Schwelle als Vielfaches der Baseline-Standardabweichung
# Wird automatisch gesetzt, kann per --threshold überschrieben werden
THRESHOLD_FACTOR = 3.5

# ── Globaler State ────────────────────────────────────────────────────────────
diff_history    = deque(maxlen=HISTORY_LEN)   # Differenz-Frames für Heatmap
for _ in range(HISTORY_LEN):
    diff_history.append(np.zeros(N_VALID))

baseline        = None
baseline_buffer = []
threshold       = None
last_raw        = np.zeros(N_VALID)
presence_history = deque(maxlen=60)
debounce_count  = 0        # aufeinanderfolgende Frames über Schwelle
state_present   = False    # bestätigter Zustand nach Debouncing
lock            = threading.Lock()
running         = True
calib_done      = False


# ── Parser ────────────────────────────────────────────────────────────────────
def parse_csi_line(line):
    match = re.search(r'"?\[([^\]]+)\]"?', line)
    if not match:
        return None
    try:
        values = list(map(int, match.group(1).split(',')))
        if len(values) < 64:
            return None
        if len(values) >= 128:
            imag = np.array(values[0::2], dtype=float)
            real = np.array(values[1::2], dtype=float)
        else:
            real = np.array(values[0::2], dtype=float)
            imag = np.array(values[1::2], dtype=float)
        return np.sqrt(real**2 + imag**2)[VALID_SUBCARRIERS]
    except Exception:
        return None


# ── Serial-Thread ─────────────────────────────────────────────────────────────
def serial_reader(port, baud, manual_threshold):
    global baseline, baseline_buffer, threshold, last_raw
    global running, calib_done, debounce_count, state_present

    try:
        ser = serial.Serial(port, baud, timeout=1)
        print(f"[OK] Verbunden mit {port} @ {baud} Baud")
        print(f"[INFO] Kalibrierung laeuft ({CALIB_FRAMES} Frames) ...")
        print(f"       Bitte NICHT bewegen!")
    except Exception as e:
        print(f"[FEHLER] {e}")
        running = False
        return

    while running:
        try:
            raw  = ser.readline()
            line = raw.decode('utf-8', errors='ignore').strip()
            if 'CSI_DATA' not in line:
                continue
            amp = parse_csi_line(line)
            if amp is None:
                continue

            with lock:
                # ── Phase 1: Kalibrierung ──────────────────────────────────
                if not calib_done:
                    baseline_buffer.append(amp.copy())
                    if len(baseline_buffer) >= CALIB_FRAMES:
                        bl_arr   = np.array(baseline_buffer)
                        baseline = np.mean(bl_arr, axis=0)
                        bl_std   = np.std(bl_arr, axis=0)

                        if manual_threshold is not None:
                            threshold = manual_threshold
                            print(f"[OK] Kalibrierung fertig. Manueller Threshold: {threshold:.2f}")
                        else:
                            # Schwelle = mittlere Baseline-STD * Faktor
                            threshold = float(np.mean(bl_std) * THRESHOLD_FACTOR)
                            threshold = max(threshold, 0.5)
                            print(f"[OK] Kalibrierung fertig.")
                            print(f"     Baseline-STD (Mittel): {np.mean(bl_std):.2f}")
                            print(f"     Automatische Schwelle: {threshold:.2f}")
                            print(f"     --> Falls zu empfindlich: --threshold {threshold*2:.1f}")
                            print(f"     --> Falls zu traege:      --threshold {threshold*0.5:.1f}")

                        last_raw   = baseline.copy()
                        calib_done = True

                # ── Phase 2: Laufbetrieb ───────────────────────────────────
                else:
                    # Rohsignal glätten
                    smoothed = SMOOTHING * amp + (1 - SMOOTHING) * last_raw
                    last_raw = smoothed

                    # Differenz zur Baseline berechnen
                    diff_frame = np.abs(smoothed - baseline)
                    diff_history.append(diff_frame.copy())

                    # Gleitende Baseline: nur wenn kein Objekt erkannt
                    # (verhindert dass Handsignal in Baseline einfliesst)
                    if not state_present:
                        baseline = (1 - BASELINE_DRIFT) * baseline + BASELINE_DRIFT * smoothed

                    # Erkennungs-Score: Median der Top-10-Subcarrier-Differenzen
                    # robuster als Mittelwert, da einzelne Ausreisser ignoriert
                    top_diffs = np.sort(diff_frame)[-10:]
                    score = float(np.median(top_diffs))

                    # Debouncing
                    if score > threshold:
                        debounce_count = min(debounce_count + 1, DEBOUNCE_FRAMES + 5)
                    else:
                        debounce_count = max(debounce_count - 2, 0)  # schneller abfallen

                    state_present = debounce_count >= DEBOUNCE_FRAMES
                    presence_history.append((score, state_present))

        except Exception:
            continue
    ser.close()


# ── Ports auflisten ───────────────────────────────────────────────────────────
def list_ports():
    for p in serial.tools.list_ports.comports():
        print(f"  {p.device} - {p.description}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global running, THRESHOLD_FACTOR

    parser = argparse.ArgumentParser(description='ESP32 CSI Heatmap v3')
    parser.add_argument('--port',      '-p', type=str,   default='COM3')
    parser.add_argument('--baud',      '-b', type=int,   default=921600)
    parser.add_argument('--list',            action='store_true')
    parser.add_argument('--threshold', '-t', type=float, default=None,
                        help='Schwelle manuell setzen (ueberschreibt Auto-Kalibrierung)')
    parser.add_argument('--factor',    '-f', type=float, default=THRESHOLD_FACTOR,
                        help='Faktor fuer Auto-Schwelle (Standard: 3.5)')
    args = parser.parse_args()

    THRESHOLD_FACTOR = args.factor

    if args.list:
        list_ports()
        return

    t = threading.Thread(target=serial_reader,
                         args=(args.port, args.baud, args.threshold), daemon=True)
    t.start()
    time.sleep(1.5)
    if not running:
        return

    # ── Figure ────────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(14, 8), facecolor='#0d0d0d')
    fig.suptitle(f'ESP32 CSI  |  {args.port}  |  Differenz-Heatmap  |  Debounce: {DEBOUNCE_FRAMES} Frames',
                 color='#aaaaaa', fontsize=10, y=0.98)

    ax_hm  = fig.add_axes([0.05, 0.38, 0.62, 0.52])
    ax_amp = fig.add_axes([0.05, 0.07, 0.62, 0.24])
    ax_pr  = fig.add_axes([0.73, 0.07, 0.24, 0.83])

    for ax in [ax_hm, ax_amp, ax_pr]:
        ax.set_facecolor('#0d0d0d')
        for sp in ax.spines.values():
            sp.set_color('#2a2a2a')

    # Differenz-Heatmap (nicht Rohsignal)
    hm_init = np.zeros((HISTORY_LEN, N_VALID))
    im = ax_hm.imshow(hm_init, aspect='auto', origin='lower',
                      cmap='plasma', vmin=0, vmax=15,
                      interpolation='bilinear')
    ax_hm.set_title('Differenz zur Baseline (Rot/Gelb = Abweichung, Blau = Ruhe)',
                    color='#cccccc', fontsize=9, pad=5)
    ax_hm.set_xlabel(f'Subcarrier ({N_VALID} gueltige)', color='#666666', fontsize=8)
    ax_hm.set_ylabel('Zeit (rollt)', color='#666666', fontsize=8)
    ax_hm.tick_params(colors='#444444', labelsize=7)
    cbar = fig.colorbar(im, ax=ax_hm, pad=0.01, fraction=0.025)
    cbar.set_label('|Amp - Baseline|', color='#666666', fontsize=7)
    cbar.ax.tick_params(colors='#444444', labelsize=6)

    # Score-Verlauf (ersetzt statische Amplitudenkurve)
    x_time = np.arange(HISTORY_LEN)
    score_buf = deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN)
    line_score, = ax_amp.plot(x_time, list(score_buf), color='#00ff88', lw=1.0, label='Score')
    line_thr,   = ax_amp.plot([0, HISTORY_LEN], [0, 0], color='#ff4444', lw=1.0,
                              linestyle='--', label='Schwelle')
    ax_amp.set_xlim(0, HISTORY_LEN - 1)
    ax_amp.set_ylim(0, 30)
    ax_amp.set_title('Erkennungs-Score (Top-10-Subcarrier-Median) vs. Schwelle',
                     color='#cccccc', fontsize=9, pad=5)
    ax_amp.set_xlabel('Zeit (Frames)', color='#666666', fontsize=8)
    ax_amp.set_ylabel('Score', color='#666666', fontsize=8)
    ax_amp.tick_params(colors='#444444', labelsize=7)
    ax_amp.grid(True, color='#1a1a1a', lw=0.4)
    ax_amp.legend(fontsize=7, facecolor='#111111', labelcolor='white',
                  loc='upper right', framealpha=0.7)

    # Erkennungs-Panel
    circle = plt.Circle((0.5, 0.56), 0.27, color='#0d0d0d', ec='#555500', lw=2.5)
    ax_pr.add_patch(circle)
    txt_state = ax_pr.text(0.5, 0.56, 'KALI-\nBRIERUNG', ha='center', va='center',
                           fontsize=13, fontweight='bold', color='#ffaa00',
                           transform=ax_pr.transAxes)
    txt_pct   = ax_pr.text(0.5, 0.22, '', ha='center', fontsize=8,
                           color='#555555', transform=ax_pr.transAxes)
    txt_score = ax_pr.text(0.5, 0.14, '', ha='center', fontsize=7,
                           color='#444444', transform=ax_pr.transAxes)
    txt_thr2  = ax_pr.text(0.5, 0.08, '', ha='center', fontsize=7,
                           color='#333333', transform=ax_pr.transAxes)
    txt_db    = ax_pr.text(0.5, 0.02, '', ha='center', fontsize=6,
                           color='#2a2a2a', transform=ax_pr.transAxes)
    ax_pr.set_xlim(0, 1)
    ax_pr.set_ylim(0, 1)
    ax_pr.axis('off')
    ax_pr.set_title('Erkennung', color='#cccccc', fontsize=9, pad=5)

    # Verlaufsbalken (Erkennungshistorie)
    n_bars = 14
    bars = ax_pr.bar(np.linspace(0.04, 0.96, n_bars), np.zeros(n_bars),
                     width=0.055, bottom=0.76, color='#1a1a1a', alpha=0.9,
                     transform=ax_pr.transAxes)

    def update(frame):
        with lock:
            dh      = np.array(list(diff_history))
            raw     = last_raw.copy()
            bl      = baseline.copy() if baseline is not None else np.zeros(N_VALID)
            thr     = threshold
            done    = calib_done
            db      = debounce_count
            present = state_present
            ph      = list(presence_history)

        # Differenz-Heatmap
        im.set_data(dh)
        vmax = max(float(dh.max()), 2.0)
        im.set_clim(0, min(vmax, 40))

        if not done:
            pct = int(100 * len(baseline_buffer) / CALIB_FRAMES)
            txt_state.set_text(f'KALI-\nBRIERUNG')
            txt_state.set_color('#ffaa00')
            circle.set_facecolor('#0d0d0d')
            circle.set_edgecolor('#554400')
            txt_pct.set_text(f'{pct}% gesammelt')
            txt_score.set_text('')
            txt_thr2.set_text('')
            txt_db.set_text('')
        else:
            # Score aus den letzten Frames
            if ph:
                score = ph[-1][0]
            else:
                score = 0.0

            score_buf.append(score)
            line_score.set_ydata(list(score_buf))
            if thr:
                line_thr.set_ydata([thr, thr])
                score_top = max(score * 1.5, thr * 2, 5)
                ax_amp.set_ylim(0, score_top)

            if present:
                circle.set_facecolor('#00c8ff')
                circle.set_edgecolor('#00ddff')
                txt_state.set_text('ERKANNT')
                txt_state.set_color('#001a22')
            else:
                circle.set_facecolor('#0d0d0d')
                circle.set_edgecolor('#1a3a1a')
                txt_state.set_text('LEER')
                txt_state.set_color('#2a6a2a')

            txt_pct.set_text('Anwesenheit')
            txt_score.set_text(f'Score:    {score:.1f}')
            txt_thr2.set_text(f'Schwelle: {thr:.1f}' if thr else '')
            txt_db.set_text(f'Debounce: {db}/{DEBOUNCE_FRAMES}')

            # Verlaufsbalken
            step = max(1, len(ph) // n_bars)
            for i, bar in enumerate(bars):
                idx = min(i * step, len(ph) - 1)
                s, p = ph[idx]
                bar.set_height(0.09 * (1 if p else 0))
                bar.set_color('#00c8ff' if p else '#1a2a1a')

        return [im, line_score, line_thr, circle,
                txt_state, txt_pct, txt_score, txt_thr2, txt_db] + list(bars)

    ani = FuncAnimation(fig, update, interval=60, blit=False, cache_frame_data=False)

    print(f"\n[INFO] Fenster schliessen zum Beenden.")
    print(f"       Schwelle manuell setzen: --threshold 5.0")
    print(f"       Faktor anpassen:         --factor 2.0\n")
    plt.show()
    running = False


if __name__ == '__main__':
    main()