# Raspberry-Pi-Gewächshaus

Dieses Repository enthält die gemeinsame, hardwareunabhängige Basis für vier
vergleichbare Regelungsvarianten:

1. feste Schwellwerte,
2. Schwellwerte mit Hysterese und Sperrzeiten,
3. adaptive Regelung mit lokaler Sensorik,
4. adaptive Regelung mit lokaler Sensorik und Wetterdaten.

Der Branch `model/baseline-hysteresis` implementiert die zweite
Vergleichsvariante als `baseline_hysteresis`. Der bisherige `legacy`-Regler
bleibt für Kompatibilitäts- und Charakterisierungstests verfügbar.

## Baseline 2: Hysterese und Sperrzeiten

Die Standardregeln sind:

- Abluft EIN ab `28 °C` oder ab `50 %` Luftfeuchte bei mindestens `18 °C`
- Abluft AUS erst bei höchstens `25 °C` und höchstens `40 %`
- Umluft EIN ab `24 °C` oder `45 %`, AUS erst bei höchstens `22 °C` und `38 %`
- beide Lüfter haben jeweils `120 s` Mindest-EIN- und Mindest-AUS-Zeit
- die harte Abluft-Mindesttemperatur von `18 °C` darf eine Mindest-EIN-Zeit
  sofort beenden
- Bewässerung bei Bodenfeuchte `<= 35 %` mit festem `10-s`-Impuls
- Wiederfreigabe der Bewässerung erst, wenn alle aktivierten Bodensensoren
  mindestens `45 %` erreicht haben
- ein weiterer Impuls benötigt sowohl die Wiederfreigabe als auch eine
  abgelaufene Sperrzeit von `3600 s`

Der Bewässerungs-Freigabestatus und die Aktor-Zeitstempel werden in
`state.json` persistiert. Ein Neustart setzt Hysterese oder Sperrzeiten daher
nicht zurück. Die gemeinsame Safety-Schicht kann Anforderungen weiterhin
begrenzen oder blockieren.

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
  --controller baseline_hysteresis \
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
  "controller": {"active": "baseline_hysteresis", "history_size": 120},
  "controllers": {
    "baseline_hysteresis": {
      "exhaust_temperature_on_c": 28.0,
      "exhaust_temperature_off_c": 25.0,
      "exhaust_humidity_on_percent": 50.0,
      "exhaust_humidity_off_percent": 40.0,
      "exhaust_min_temperature_c": 18.0,
      "exhaust_min_on_seconds": 120.0,
      "exhaust_min_off_seconds": 120.0,
      "circulation_temperature_on_c": 24.0,
      "circulation_temperature_off_c": 22.0,
      "circulation_humidity_on_percent": 45.0,
      "circulation_humidity_off_percent": 38.0,
      "circulation_min_on_seconds": 120.0,
      "circulation_min_off_seconds": 120.0,
      "soil_moisture_on_percent": 35.0,
      "soil_moisture_off_percent": 45.0,
      "watering_seconds": 10.0,
      "watering_cooldown_seconds": 3600.0
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
