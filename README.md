# Raspberry-Pi-Gewächshaus

Eine nachvollziehbare Regelungs- und Versuchsplattform für ein automatisiertes
Gewächshaus. Das System erfasst Innenklima, bis zu drei Bodenfeuchtesensoren,
Licht und optional Wetterdaten. Es steuert Abluft, Umluft und ein Wasserventil,
protokolliert jede Entscheidung und kann dieselben Messreihen reproduzierbar
gegen vier Regelungsvarianten abspielen.

## Regelungsvarianten

| Controller-ID | Variante | Besonderheit |
| --- | --- | --- |
| `baseline_fixed` | Baseline 1 | Feste Schwellwerte ohne Zustandslogik |
| `baseline_hysteresis` | Baseline 2 | Hysterese, Mindestlaufzeiten und Bewässerungssperre |
| `adaptive_local` | Ansatz A | Lokale Trends und Licht passen die Schwellwerte an |
| `adaptive_weather` | Ansatz B | Ansatz A plus aktuelle Wetterdaten und Vorhersage |

Der Branch `model/comparison` enthält alle vier Varianten. Das aktive Modell
kann im Dashboard gewechselt werden; der gemeinsame Safety-Layer bleibt immer
aktiv. Die Einzelbranches sind eingefrorene Versuchsstände.

## Schnellstart

Auf einem Raspberry Pi mit bereits aktivierten Schnittstellen:

```bash
git clone git@github.com:Golliathor/raspi-gewaechshaus.git
cd raspi-gewaechshaus
git switch model/comparison
python3 -m venv .venv
.venv/bin/pip install -r web/requirements.txt
export GREENHOUSE_BASE_DIR=/home/grow/gewaechshaus-data
mkdir -p "$GREENHOUSE_BASE_DIR"/{web,logs,images}
```

Für einen manuellen Funktionstest werden vier Prozesse benötigt:

```bash
.venv/bin/python klima_logger.py
.venv/bin/python web/automation_daemon.py
.venv/bin/python web/camera_daemon.py
.venv/bin/python web/app.py
```

Alle Prozesse müssen dieselbe Variable `GREENHOUSE_BASE_DIR` erhalten. Das
Dashboard ist anschließend unter `http://<raspberry-pi>:8080` erreichbar.
Für Dauerbetrieb wird systemd empfohlen.

> **Sicherheit:** Vor dem ersten Automatikbetrieb Relais, Ventil,
> Durchflussrate, Sensorwerte und Abschaltgrenzen manuell prüfen. Die Web-App
> hat keine Benutzeranmeldung und gehört nicht ungeschützt ins Internet.
> Arbeiten an Netzspannung dürfen nur fachgerecht ausgeführt werden.

## Systemüberblick

```text
DHT22 ──> klima_logger.py ──> klima.csv ─┐
ADS1115 ──────────────────────────────────┼─> automation_daemon.py
Open-Meteo ───────────────────────────────┘          │
                                                    v
                SensorSnapshot -> Controller -> Safety-Layer
                                                    │
                                      Relais + Zustands-/Versuchslogs
                                                    │
                                                    v
                                          Flask-Dashboard :8080
```

Raspberry-Pi-spezifische Bibliotheken werden nur in Hardwareadaptern oder
Hardwareprozessen importiert. Controller, Safety-Layer, Replay und die meisten
Tests laufen deshalb auch auf einem Entwicklungsrechner.

## Dokumentation

- [Installation, Hardware und systemd](docs/installation.md)
- [Architektur und Datenfluss](docs/architecture.md)
- [Regelungsmodelle und Formeln](docs/controllers.md)
- [Vollständige Konfigurationsreferenz](docs/configuration.md)
- [Weboberfläche, API, Logs und laufender Betrieb](docs/operations.md)
- [Replay, Import und Modellvergleich](docs/replay-and-evaluation.md)
- [Tests und Fehlerdiagnose](docs/testing-and-troubleshooting.md)
- [Entwicklung und neue Controller](docs/development.md)

Die ausführliche Beispielkonfiguration liegt in
[`examples/config.json`](examples/config.json).

## Tests und Replay in Kurzform

```bash
python3 -m unittest discover -v

python3 -m greenhouse.replay \
  --controller adaptive_weather \
  --config examples/config.json \
  --input tests/fixtures/replay_snapshots.csv \
  --run-id smoke-test \
  --output-dir /tmp/greenhouse-replay
```

Der Replay erzeugt `decisions.csv` und `metrics.json`. Gleiche Eingaben,
Konfiguration und Softwareversion erzeugen deterministisch dieselben
Entscheidungen.

## Laufzeitdaten

Ohne Umgebungsvariable verwendet das Projekt `/home/grow/gewaechshaus`.
Empfohlen ist ein eigener Datenordner:

```bash
export GREENHOUSE_BASE_DIR=/home/grow/gewaechshaus-data
export GREENHOUSE_RUN_ID=versuch-2026-07-27
```

Unterhalb der Datenwurzel liegen:

```text
web/config.json       gemeinsame Konfiguration
web/state.json        flüchtiger und wiederherstellbarer Laufzeitzustand
web/command.json      kurzlebige Befehlsübergabe vom Web zum Daemon
logs/                 Klima-, Sensor-, Aktions- und Entscheidungslogs
images/               Zeitraffer- und aktuelles Kamerabild
```

`GREENHOUSE_RUN_ID` kennzeichnet Praxisversuche. Ohne Wert wird
`live-YYYY-MM-DD` verwendet.
