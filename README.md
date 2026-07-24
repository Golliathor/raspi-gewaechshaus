# Raspberry-Pi-Gewächshaus

Dieses Repository enthält die gemeinsame, hardwareunabhängige Basis für vier
vergleichbare Regelungsvarianten:

1. feste Schwellwerte,
2. Schwellwerte mit Hysterese und Sperrzeiten,
3. adaptive Regelung mit lokaler Sensorik,
4. adaptive Regelung mit lokaler Sensorik und Wetterdaten.

Der Basisstand stellt bewusst nur den bisherigen `legacy`-Regler bereit. Neue
Regler implementieren dieselbe reine Schnittstelle und können dadurch mit
identischen Sensordaten getestet werden.

## Architektur

- `greenhouse.models`: Sensor-, Wetter-, Kontext- und Entscheidungstypen
- `greenhouse.controllers`: reine Regler und Controller-Registry
- `greenhouse.safety`: gemeinsame Hardware- und Pflanzenschutzgrenzen
- `greenhouse.runtime`: deterministische Engine für Livebetrieb und Replay
- `greenhouse.records`, `greenhouse.replay`, `greenhouse.metrics`: einheitliche
  Versuchslogs und Auswertung
- `web/automation_daemon.py`: Raspberry-Pi-Orchestrierung und Hardwareadapter

Ein Controller bekommt einen `SensorSnapshot`, den `ControlContext` und die
Konfiguration. Er liefert ausschließlich eine `ControlDecision`. Datei-, GPIO-
und Netzwerkzugriffe gehören nicht in einen Controller.

Modellspezifische Einstellungen liegen unter `controllers.<controller_id>`.
Der Legacy-Regler liest aus Kompatibilitätsgründen noch die bisherigen
Schwellwerte auf oberster Ebene.

## Installation und Pfade

Auf dem Raspberry Pi:

```bash
python3 -m venv .venv
.venv/bin/pip install -r web/requirements.txt
```

Standardmäßig werden Laufzeitdaten unter `/home/grow/gewaechshaus` erwartet.
Für Entwicklung und Tests lässt sich die Wurzel ohne Codeänderung setzen:

```bash
export GREENHOUSE_BASE_DIR="$PWD/.runtime"
mkdir -p "$GREENHOUSE_BASE_DIR"/{web,logs,images}
```

Eine eindeutige Kennung für einen Praxisversuch wird über
`GREENHOUSE_RUN_ID` gesetzt. Ohne Angabe verwendet der Daemon
`live-YYYY-MM-DD`.

## Tests und Dry-Run

Die hardwareunabhängigen Tests benötigen nur die Python-Standardbibliothek:

```bash
python -m unittest discover -v
```

Ein deterministischer Dry-Run mit dem mitgelieferten Fixture:

```bash
python -m greenhouse.replay \
  --controller legacy \
  --config examples/config.json \
  --input tests/fixtures/replay_snapshots.csv \
  --run-id smoke-test \
  --output-dir /tmp/greenhouse-replay
```

Das Ergebnis enthält `decisions.csv` und `metrics.json`. Derselbe Input,
dieselbe Konfiguration und dieselbe Controller-Version erzeugen bytegleich
dieselben Dateien.

Bestehende Logs werden in das kanonische Format importiert:

```bash
python -m greenhouse.importer \
  --climate logs/klima.csv \
  --sensors logs/sensoren.csv \
  --output /tmp/snapshots.csv
```

## Controller ergänzen

Ein neuer Controller erhält eine eindeutige `controller_id`, implementiert
`decide(snapshot, context, config)` und wird in
`greenhouse.controllers.registry` registriert. Die Auswahl erfolgt über:

```json
{
  "controller": {"active": "legacy", "history_size": 120},
  "controllers": {"legacy": {}}
}
```

Für die vier Varianten werden nach dem gemeinsamen Basis-Commit folgende
Branches verwendet:

```text
model/baseline-fixed
model/baseline-hysteresis
model/adaptive-local
model/adaptive-weather
```

Alle Branches behalten Snapshot-, Entscheidungs- und Metrikformat unverändert.
So können ihre Ergebnisse später ohne Sonderkonvertierung verglichen werden.

## Raspberry-Pi-Abnahme

Vor dem ersten Livebetrieb:

1. `GREENHOUSE_BASE_DIR` und `GREENHOUSE_RUN_ID` setzen.
2. `python -m unittest discover -v` ausführen.
3. ADC-Adresse und Sensorkanäle auf der Konfigurationsseite prüfen.
4. Automatik zunächst deaktivieren und alle drei Relais einzeln über das
   Dashboard prüfen.
5. Automatik aktivieren und `control_snapshots.csv`,
   `control_decisions.csv` sowie Safety-Ereignisse in `actions.csv`
   kontrollieren.

Manuelle Wasserbefehle sind ebenfalls durch maximalen Impuls und Tageslimit
begrenzt. Bei Programmende schaltet der Automationsdaemon alle Relais aus.
