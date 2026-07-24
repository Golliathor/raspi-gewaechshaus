# Raspberry-Pi-Gewächshaus

Dieses Repository enthält die gemeinsame, hardwareunabhängige Basis für vier
vergleichbare Regelungsvarianten:

1. feste Schwellwerte,
2. Schwellwerte mit Hysterese und Sperrzeiten,
3. adaptive Regelung mit lokaler Sensorik,
4. adaptive Regelung mit lokaler Sensorik und Wetterdaten.

Der Branch `model/comparison` enthält alle vier Varianten gleichzeitig. Das
aktive Modell kann im Dashboard nach einer Bestätigung gewechselt werden. Die
Auswahl wird in der gemeinsamen Konfiguration gespeichert und vom Daemon im
nächsten Regelzyklus ohne Neustart übernommen. Der Safety-Layer bleibt bei
jedem Modellwechsel aktiv.

`adaptive_weather` baut direkt auf `adaptive_local` auf, ergänzt
Wetterkorrekturen und protokolliert jeden verwendeten Wetterwert, Score und
Schwellwerteingriff. Ohne hinreichend aktuelle Wetterdaten verhält er sich
deterministisch wie Ansatz A.

## Ansatz A: adaptive lokale Regelung

Aus den letzten 15 Minuten werden lineare Trends für Temperatur,
Luftfeuchtigkeit und mittlere Bodenfeuchtigkeit berechnet. Doppelte
Sensorzeitstempel werden zusammengefasst; bei weniger als zwei Minuten
Messspanne wird kein Trend verwendet. Die Standard-Vorausschau beträgt zehn
Minuten.

Mit `L = clamp((Licht - 50) / 50, 0, 1)` gelten standardmäßig:

```text
Temperaturabsenkung =
  clamp(max(0, Temperaturtrend) × 10 + 1,5 × L, 0, 3 °C)

Feuchteabsenkung =
  clamp(max(0, Feuchtetrend) × 10, 0, 10 %)
```

Die EIN-Grenzen beider Lüfter werden um die volle Absenkung reduziert, die
AUS-Grenzen um die Hälfte. Dadurch bleibt eine Hysterese erhalten. Zusätzlich
gelten je 120 Sekunden Mindest-EIN- und Mindest-AUS-Zeit sowie die harte
Abluft-Mindesttemperatur von 18 °C.

Für die Bewässerung werden der projizierte Bodenfeuchteverlust `D`, die
Temperaturbelastung oberhalb 25 °C und die Lichtbelastung verwendet:

```text
Erhöhung der Gießgrenze =
  clamp(0,5 × D + 0,3 × Temperaturbelastung + L, 0, 5 %)

Gießdauer =
  clamp(
    10 + 0,5 × Feuchtedefizit + 0,5 × D
       + 0,5 × Temperaturbelastung + 2 × L,
    5,
    30 Sekunden
  )
```

Nach einer Bewässerung müssen alle aktivierten Bodensensoren mindestens 45 %
erreichen und die 3600-Sekunden-Sperrzeit muss ablaufen. Dieser Zustand bleibt
über Daemon-Neustarts erhalten. Wetterfelder werden ausdrücklich ignoriert;
jedes Entscheidungslog enthält deshalb `weather_used=false`.

## Ansatz B: lokale Regelung mit Wetterdaten

Ansatz B verwendet exakt die lokalen Parameter von Ansatz A und ergänzt
erklärbare Korrekturen:

- Kühlere Außenluft senkt die Abluft-Temperaturgrenze, heißere Außenluft erhöht
  sie. Die Standardkorrektur ist auf ±1,5 °C begrenzt.
- Die aus Temperatur und relativer Feuchte berechnete absolute Luftfeuchte
  entscheidet, ob Außenluft tatsächlich trockener ist. Geeignete Außenluft
  senkt die Abluft-Feuchtegrenze, ungeeignete erhöht sie; die Korrektur ist auf
  ±5 Prozentpunkte begrenzt.
- Heiße Außenbedingungen erhöhen Bodenfeuchtegrenze und Gießdauer begrenzt.
- Eine belastbare Regenprognose senkt den erwarteten Verdunstungsbedarf
  moderat. Sie nimmt nicht an, dass Regen die Pflanzen im Gewächshaus direkt
  erreicht. Bei kritischer Bodenfeuchte wird diese Reduktion vollständig
  ignoriert.

Die Hysterese wird nach jeder Wetterkorrektur erhalten. Wetterdaten, die älter
als `weather_max_age_seconds` sind, fehlende Werte und Netzfehler führen zum
lokalen Fallback. Liveabrufe werden gecacht; ein alter Cache wird nach
`weather.max_stale_seconds` nicht mehr verwendet.

Der mitgelieferte Anbieter ist Open-Meteo. Er liefert aktuelle
Außentemperatur/-feuchte sowie stündlichen Niederschlag und
Niederschlagswahrscheinlichkeit. Der Daemon verdichtet die Niederschlagswerte
über `weather.forecast_horizon_hours` in den bereits gemeinsamen kanonischen
`WeatherSnapshot`; das Snapshot- und Entscheidungsformat bleibt damit zwischen
allen Modellbranches identisch.
API-Dokumentation: <https://open-meteo.com/en/docs>

## Architektur

- `greenhouse.models`: Sensor-, Wetter-, Kontext- und Entscheidungstypen
- `greenhouse.controllers`: reine Regler und Controller-Registry
- `greenhouse.safety`: gemeinsame Hardware- und Pflanzenschutzgrenzen
- `greenhouse.runtime`: deterministische Engine für Livebetrieb und Replay
- `greenhouse.records`, `greenhouse.replay`, `greenhouse.metrics`: einheitliche
  Versuchslogs und Auswertung
- `greenhouse.weather`: Open-Meteo-Client, zeitbegrenzter Cache und
  serialisierbarer Wetterzustand
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
  --controller adaptive_weather \
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
  "controller": {"active": "adaptive_weather", "history_size": 720},
  "weather": {
    "enabled": true,
    "provider": "open_meteo",
    "latitude": 50.766778,
    "longitude": 12.979194,
    "forecast_horizon_hours": 6,
    "refresh_seconds": 900,
    "max_stale_seconds": 3600
  },
  "controllers": {
    "adaptive_local": {
      "trend_window_seconds": 900,
      "trend_minimum_span_seconds": 120,
      "trend_lookahead_minutes": 10,
      "soil_moisture_on_percent": 35,
      "soil_moisture_off_percent": 45,
      "watering_base_seconds": 10,
      "watering_min_seconds": 5,
      "watering_max_seconds": 30,
      "watering_cooldown_seconds": 3600
    },
    "adaptive_weather": {
      "weather_max_age_seconds": 3600,
      "max_outdoor_temperature_adjustment_c": 1.5,
      "max_outdoor_humidity_adjustment_percent": 5,
      "rain_probability_threshold_percent": 60,
      "critical_soil_moisture_percent": 20
    }
  }
}
```

Alle Modellparameter stehen in `examples/config.json` und auf der
Konfigurationsseite. Ein weiterer Controller erhält eine eindeutige
`controller_id`, implementiert `decide(snapshot, context, config)` und wird in
`greenhouse.controllers.registry` registriert. Die Vergleichsbranches sind:

```text
model/baseline-fixed
model/baseline-hysteresis
model/adaptive-local
model/adaptive-weather
model/comparison
```

Die vier Einzelbranches bleiben eingefrorene, reproduzierbare Versuchsstände.
`model/comparison` ist für den praktischen Betrieb und den Wechsel über die
Website vorgesehen. Alle Branches behalten Snapshot-, Entscheidungs- und
Metrikformat unverändert.

## Raspberry-Pi-Abnahme

Vor dem ersten Livebetrieb:

1. Für den Website-Wechsel `git switch model/comparison` und `git pull`
   ausführen.
2. `GREENHOUSE_BASE_DIR` und `GREENHOUSE_RUN_ID` setzen.
3. `python -m unittest discover -v` ausführen.
4. ADC-Adresse und Sensorkanäle auf der Konfigurationsseite prüfen.
5. Für Ansatz B Wetterabruf, Breiten- und Längengrad konfigurieren und im
   Status `weather_available` sowie mögliche Abruffehler prüfen.
6. Automatik zunächst deaktivieren und alle drei Relais einzeln über das
   Dashboard prüfen.
7. Automatik aktivieren und `control_snapshots.csv`,
   `control_decisions.csv` sowie Safety-Ereignisse in `actions.csv`
   kontrollieren.

Manuelle Wasserbefehle sind ebenfalls durch maximalen Impuls und Tageslimit
begrenzt. Bei Programmende schaltet der Automationsdaemon alle Relais aus.
