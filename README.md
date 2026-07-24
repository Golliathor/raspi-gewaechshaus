# Raspberry-Pi-Gewächshaus

Dieses Repository enthält die gemeinsame, hardwareunabhängige Basis für vier
vergleichbare Regelungsvarianten:

1. feste Schwellwerte,
2. Schwellwerte mit Hysterese und Sperrzeiten,
3. adaptive Regelung mit lokaler Sensorik,
4. adaptive Regelung mit lokaler Sensorik und Wetterdaten.

Der Branch `model/baseline-fixed` implementiert die erste Vergleichsvariante
als `baseline_fixed`. Der bisherige `legacy`-Regler bleibt für
Kompatibilitäts- und Charakterisierungstests verfügbar.

## Baseline 1: feste Schwellwerte

Der Regler ist zustandslos. Er berücksichtigt weder den vorherigen
Relaiszustand noch vergangene Bewässerungen:

- Abluft an bei `Temperatur >= 28 °C` oder bei
  `Temperatur >= 18 °C` und `Luftfeuchte >= 50 %`
- Umluft an bei `Temperatur >= 24 °C` oder
  `Luftfeuchte >= 45 %`
- fester Wasserimpuls von `10 s`, wenn bei einer fälligen Prüfung mindestens
  ein aktivierter Bodensensor `<= 35 %` meldet
- Lüfter sofort aus, sobald ihre feste Einschaltbedingung nicht mehr erfüllt ist
- keine Hysterese und keine modellspezifische Sperrzeit

Die Werte sind unter `controllers.baseline_fixed` konfigurierbar. Messintervall,
Safety-Limits und das Bewässerungs-Prüfintervall gehören zur gemeinsamen
Versuchsumgebung und bleiben für alle Modelle gleich.

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
  --controller baseline_fixed \
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

## Controller auswählen

Die Auswahl für Livebetrieb und Dashboard erfolgt über:

```json
{
  "controller": {"active": "baseline_fixed", "history_size": 120},
  "controllers": {
    "baseline_fixed": {
      "exhaust_temperature_threshold_c": 28.0,
      "exhaust_humidity_threshold_percent": 50.0,
      "exhaust_min_temperature_c": 18.0,
      "circulation_temperature_threshold_c": 24.0,
      "circulation_humidity_threshold_percent": 45.0,
      "soil_moisture_threshold_percent": 35.0,
      "watering_seconds": 10.0
    }
  }
}
```

Ein weiterer Controller erhält eine eindeutige `controller_id`, implementiert
`decide(snapshot, context, config)` und wird in
`greenhouse.controllers.registry` registriert. Die vier Vergleichsbranches
sind:

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
