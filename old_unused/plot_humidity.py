#!/usr/bin/env python3
import csv
import os
import re
import subprocess
import sys
from datetime import datetime

CSV_FILE = "/home/grow/gewaechshaus/logs/klima.csv"
DATA_FILE = "/tmp/humidity_plot_data.dat"
GNUPLOT_FILE = "/tmp/humidity_plot.gnuplot"
OUTPUT_FILE = "/home/grow/gewaechshaus/humidity_plot.png"


def clean_value(value: str) -> float:
    value = str(value).strip()
    value = re.sub(r"[^0-9,.\-]", "", value)
    value = value.replace(",", ".")
    return float(value)


def parse_row(row: dict):
    """
    Unterstützt zwei Formate:
    1) Neues Format:
       timestamp,temperature_c,humidity_percent
    2) Altes Format:
       Date,Time,Temperature,Humidity
    """
    # Neues Format
    if "timestamp" in row and "temperature_c" in row and "humidity_percent" in row:
        dt = datetime.fromisoformat(row["timestamp"])
        temp = clean_value(row["temperature_c"])
        hum = clean_value(row["humidity_percent"])
        return dt, temp, hum

    # Altes Format
    if "Date" in row and "Time" in row and "Temperature" in row and "Humidity" in row:
        dt = datetime.strptime(f"{row['Date']} {row['Time']}", "%m/%d/%y %H:%M")
        temp = clean_value(row["Temperature"])
        hum = clean_value(row["Humidity"])
        return dt, temp, hum

    raise KeyError(f"Unbekannte CSV-Struktur. Gefundene Spalten: {list(row.keys())}")


def read_csv(csv_file: str):
    rows = []

    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append(parse_row(row))
            except Exception as e:
                print(f"Überspringe fehlerhafte Zeile: {row} ({e})")

    if not rows:
        raise ValueError("Keine gültigen Daten gefunden.")

    rows.sort(key=lambda x: x[0])
    return rows


def write_dat_file(rows, data_file: str):
    with open(data_file, "w", encoding="utf-8") as f:
        for dt, temp, hum in rows:
            f.write(f"{dt.strftime('%Y-%m-%d %H:%M:%S')}\t{temp}\t{hum}\n")


def write_gnuplot_script(data_file: str, output_file: str, script_file: str):
    script = f"""
set terminal pngcairo size 1400,700
set output '{output_file}'

set title 'Temperatur und Luftfeuchtigkeit'
set xdata time
set timefmt '%Y-%m-%d %H:%M:%S'
set format x '%d.%m\\n%H:%M'
set xlabel 'Zeit'
set ylabel 'Wert'
set grid
set key outside
set datafile separator '\\t'

plot '{data_file}' using 1:2 with lines linewidth 2 title 'Temperatur (°C)', \\
     '{data_file}' using 1:3 with lines linewidth 2 title 'Luftfeuchte (%)'
"""
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script)


def run_gnuplot(script_file: str):
    try:
        subprocess.run(["gnuplot", script_file], check=True)
    except FileNotFoundError:
        print("Fehler: gnuplot ist nicht installiert.")
        print("Installieren mit: sudo apt install gnuplot")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Fehler beim Ausführen von gnuplot: {e}")
        sys.exit(1)


def main():
    if not os.path.exists(CSV_FILE):
        print(f"CSV-Datei nicht gefunden: {CSV_FILE}")
        sys.exit(1)

    rows = read_csv(CSV_FILE)
    write_dat_file(rows, DATA_FILE)
    write_gnuplot_script(DATA_FILE, OUTPUT_FILE, GNUPLOT_FILE)
    run_gnuplot(GNUPLOT_FILE)

    print(f"Diagramm erstellt: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
