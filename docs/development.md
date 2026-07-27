# Entwicklung und neue Controller

## Entwicklungsumgebung

```bash
git clone git@github.com:Golliathor/raspi-gewaechshaus.git
cd raspi-gewaechshaus
git switch model/comparison
python3 -m venv .venv
.venv/bin/pip install -r web/requirements.txt
export GREENHOUSE_BASE_DIR="$PWD/.runtime"
mkdir -p "$GREENHOUSE_BASE_DIR"/{web,logs,images}
python3 -m unittest discover -v
```

Reine Controller-, Safety- und Replay-Tests funktionieren auch ohne
Raspberry-Pi-Bibliotheken. Hardwareimporte gehören in Adapter beziehungsweise
Hardwareprozesse.

## Einen Controller ergänzen

1. Neue Klasse in `greenhouse/controllers/` anlegen.
2. Eindeutige `controller_id` definieren.
3. `decide(snapshot, context, config) -> ControlDecision` implementieren.
4. Keine GPIO-, Datei-, Schlaf- oder Netzwerkzugriffe einbauen.
5. Parameter unter `controllers.<controller_id>` in `DEFAULT_CONFIG` ergänzen.
6. Validierungsregeln ergänzen.
7. Controller in `greenhouse.controllers.registry` registrieren.
8. Konfigurationsseite und Dokumentation erweitern.
9. Unit-, Replay- und Webtests ergänzen.

Controllerzustand, der Neustarts überleben soll, wird über
`ControlDecision.controller_state` zurückgegeben und im nächsten
`ControlContext.controller_state` bereitgestellt.

## Entscheidungsgründe und Diagnosen

Gründe sind stabile, maschinenlesbare Codes, beispielsweise:

```text
exhaust_on_temperature_threshold
watering_blocked_cooldown
```

Diagnosen müssen JSON-serialisierbar sein. Sie sollen eine Entscheidung
erklären, ohne für die Regelung erneut externe Daten zu laden.

## Neue Sensor- oder Wetterquelle

Eine neue Quelle wird vor dem Controller in `SensorSnapshot` abgebildet.
Hardwaredetails dürfen nicht in die Controllerlogik durchsickern. Für einen
weiteren Wetteranbieter das `WeatherProvider.fetch(now)`-Protokoll
implementieren und dieselben kanonischen Felder liefern.

## Tests aufbauen

- Grenzwerte exakt unterhalb, auf und oberhalb testen.
- Zustandsübergänge mit kontrollierten Zeitstempeln testen.
- ungültige und fehlende Sensorwerte testen.
- Safety-Anforderung und angewendetes Ergebnis getrennt prüfen.
- deterministische Replay-Ausgabe testen.
- bei Webänderungen Status-API und Formularspeicherung testen.
- Hardware erst in einem separaten Pi-Smoke-Test prüfen.

Fixtures liegen in `tests/fixtures/`. Tests dürfen nicht vom Internet, der
lokalen Uhr oder realem GPIO abhängen.

## Branches

| Branch | Zweck |
| --- | --- |
| `model/baseline-fixed` | eingefrorene Baseline 1 |
| `model/baseline-hysteresis` | eingefrorene Baseline 2 |
| `model/adaptive-local` | eingefrorener Ansatz A |
| `model/adaptive-weather` | eingefrorener Ansatz B |
| `model/comparison` | gemeinsamer Praxisbetrieb mit Modellwechsel |

Neue gemeinsame Fehlerkorrekturen gehören in `model/comparison` und sollten
gezielt in Einzelstände übernommen werden, wenn die Reproduzierbarkeit des
Versuchsdesigns dies zulässt. Für jeden Versuch Commit-Hash, Controller-ID,
Konfigurationskopie und `run_id` aufbewahren.

## Definition of Done

Eine gemeinsame Änderung ist fertig, wenn:

- komplette Testsuite außerhalb des Pi besteht,
- Replay-Fixture verarbeitet wird,
- `git diff --check` sauber ist,
- Konfigurations- und Datenformat kompatibel bleiben oder migriert werden,
- Safety-Verhalten explizit getestet ist,
- Pi-Smoke-Test bei Hardwareänderungen bestanden ist,
- Dokumentation und Beispielkonfiguration aktualisiert sind.
