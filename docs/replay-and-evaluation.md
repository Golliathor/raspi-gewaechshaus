# Replay, Import und Modellvergleich

## Zweck

Replay wendet einen Controller auf eine kanonische Messreihe an, ohne GPIO,
Kamera oder Netzwerk zu verwenden. Damit lassen sich alle Modelle auf exakt
denselben Eingangsdaten vergleichen.

## Kanonisches Snapshot-CSV

Pflicht ist `timestamp`; die übrigen Werte dürfen leer sein:

```text
timestamp
temperature_c
humidity_percent
soil1_percent
soil2_percent
soil3_percent
light_percent
quality
issues
weather_timestamp
outside_temperature_c
outside_humidity_percent
precipitation_mm
precipitation_probability_percent
weather_provider
```

Zeitstempel sind ISO-8601-Werte wie `2026-07-27T13:00:00`. `issues` verwendet
`|` als Trenner. Wetter wird nur erzeugt, wenn `weather_timestamp` gesetzt ist.

## Einzelner Replay

```bash
python3 -m greenhouse.replay \
  --controller baseline_fixed \
  --config examples/config.json \
  --input tests/fixtures/replay_snapshots.csv \
  --run-id replay-fixed-001 \
  --output-dir /tmp/replay-fixed
```

Ausgaben:

- `decisions.csv`: jede angeforderte und angewendete Entscheidung
- `metrics.json`: getrennte Klima-, Ressourcen- und Qualitätskennzahlen

## Alle vier Modelle vergleichen

```bash
for model in baseline_fixed baseline_hysteresis adaptive_local adaptive_weather
do
  python3 -m greenhouse.replay \
    --controller "$model" \
    --config examples/config.json \
    --input tests/fixtures/replay_snapshots.csv \
    --run-id "vergleich-001-$model" \
    --output-dir "/tmp/vergleich-001/$model"
done
```

Für einen fairen Vergleich müssen identisch sein:

- Snapshot-Datei,
- gemeinsame Konfiguration und Zielbereiche,
- Software-Commit,
- Startzustand,
- Messzeitraum.

Nur Controller-ID und modellspezifische Parameter dürfen gezielt variieren.

## Bestehende Logs importieren

Klima- und Sensor-CSV werden zeitlich über den jeweils nächsten Messpunkt
verbunden:

```bash
python3 -m greenhouse.importer \
  --climate logs/klima.csv \
  --sensors logs/sensoren.csv \
  --tolerance-seconds 120 \
  --output /tmp/snapshots.csv
```

Findet sich innerhalb der Toleranz kein Sensorwert, erhält der Snapshot
`sensor_log_not_matched`. Der Import ergänzt keine Wetterdaten.

## Kennzahlen

Es gibt bewusst keinen willkürlichen Gesamtscore.

### Klimaqualität

- Anteil der beobachteten Zeit im Temperaturziel
- Anteil der Zeit im Luftfeuchteziel
- Anteil der Zeit im Bodenfeuchteziel
- Temperaturabweichungsintegral in Grad-Minuten
- Feuchteabweichungsintegral in Prozentpunkt-Minuten
- Bodenfeuchtedefizit in Prozentpunkt-Minuten

Für Bodenkennzahlen wird der Mittelwert aller gültigen Bodenwerte verwendet.

### Ressourcen

- tatsächlich gestartete Bewässerungssekunden
- geschätztes Wasser in ml:

```text
Ventillaufzeit × water_flow_ml_per_second
```

- Laufzeit von Abluft und Umluft
- Schaltanzahl beider Lüfter

### Datenqualität

- ungültige Snapshots
- Anzahl einzelner Safety-Eingriffe

Die Zeitintegration verwendet den Abstand zum jeweils folgenden Snapshot.
Der letzte Datensatz trägt deshalb keine zusätzliche Beobachtungsdauer bei.

## Praxis- und Replay-Ergebnisse verbinden

Im Livebetrieb `GREENHOUSE_RUN_ID` setzen:

```bash
export GREENHOUSE_RUN_ID=praxis-001-adaptive-local
```

Diese ID wird in jede Zeile von `control_decisions.csv` geschrieben. Für einen
Replay derselben Daten eine erkennbar zugehörige ID verwenden und zusätzlich
Commit, Konfiguration, Zeitraum und Controller notieren.

## Reproduzierbarkeit

Der Controller führt keine Datei-, GPIO- oder Netzwerkzugriffe aus. Wetterwerte
müssen bereits im Snapshot stehen. JSON-Diagnosen werden sortiert geschrieben.
Bei identischem Input, gleicher Konfiguration und gleichem Commit sind die
Entscheidungsdateien bytegleich reproduzierbar.

Aktuelle Live-Snapshots können direkt als Replay-Eingabe verwendet werden:

```bash
cp "$GREENHOUSE_BASE_DIR/logs/control_snapshots.csv" /tmp/versuch.csv
```
