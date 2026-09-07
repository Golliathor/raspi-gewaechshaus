# Architektur und Datenfluss

## Ziel der Trennung

Sensorerfassung, Entscheidung, Safety, Hardwareausgabe, Speicherung und
Auswertung sind getrennt. Dadurch verwenden Livebetrieb und Replay dieselbe
Regelungsengine, während GPIO- und Netzwerkzugriffe austauschbar bleiben.

```text
Hardware/CSV/Wetter
       │
       v
SensorSnapshot ──> Controller.decide(...) ──> angeforderte Entscheidung
       │                                            │
       └────────────────────────────────────────────v
                                             Safety-Layer
                                                    │
                                      ausgeführte Entscheidung
                                                    │
                               ControlEngine + RelayOutput + Logs
```

## Prozesse

| Prozess | Aufgabe | Intervall |
| --- | --- | --- |
| `klima_logger.py` | DHT22 lesen und `klima.csv` schreiben | 60 s, fest im Code |
| `web/automation_daemon.py` | Sensoren, Wetter, Controller, Safety, Relais, Logs | Regelzyklus standardmäßig 5 s |
| `web/camera_daemon.py` | drei geplante Bilder pro Tag | Prüfung alle 20 s |
| `web/app.py` | Dashboard, Konfiguration, API, Downloads | HTTP Port 8080 |

Der Daemon liest die lokalen ADC-Sensoren nur alle
`sensor_read_interval_seconds`, verwendet dazwischen aber den aktuellen
Laufzeitzustand. Eine Bewässerungsentscheidung wird nur alle
`watering_check_interval_seconds` freigegeben.

## Domänenmodelle

`greenhouse.models` definiert die gemeinsame Sprache:

- `SensorSnapshot`: Messzeitpunkt, Innenklima, bis zu drei Bodenwerte, Licht,
  Qualität, Fehlerhinweise und optional Wetter.
- `WeatherSnapshot`: Zeitpunkt, Außentemperatur, Außenfeuchte, aggregierter
  Niederschlag, maximale Niederschlagswahrscheinlichkeit und stündliche
  Vorhersage.
- `ActuatorState`: tatsächlicher Zustand von Abluft, Umluft und Wasserventil.
- `ControlContext`: Zustand, Historie, letzte Übergänge, Bewässerungshistorie,
  Tagesmenge und controllerinterner Zustand.
- `ControlDecision`: gewünschte Lüfterzustände, Wasserimpuls, Gründe,
  Diagnosen und nächster controllerinterner Zustand.
- `CycleResult`: Snapshot, angeforderte und angewendete Entscheidung,
  Safety-Eingriffe und Aktorübergänge eines Zyklus.

Alle Controller implementieren:

```python
decide(snapshot, context, config) -> ControlDecision
```

Diese Methode führt keine GPIO-, Datei- oder Netzwerkzugriffe aus.

## Regelzyklus

1. Der Daemon lädt die aktuelle Konfiguration.
2. Bei einem Modellwechsel wird eine Engine mit dem neuen Controller erzeugt.
3. Klima-, ADC- und gegebenenfalls Wetterwerte werden zusammengeführt.
4. `SensorSnapshot.validated()` verwirft Werte außerhalb plausibler Bereiche.
5. Der Controller erzeugt die angeforderte Entscheidung.
6. Der Safety-Layer begrenzt oder verwirft unsichere Anforderungen.
7. `ControlEngine` startet Wasserimpulse nicht blockierend und berechnet
   Zustandsübergänge.
8. Der Hardwareadapter schreibt die Relaiszustände.
9. Snapshot, Entscheidung, Ereignisse und wiederherstellbarer Zustand werden
   gespeichert.

Das Ventil wird über `watering_until` beendet. Der Regelzyklus blockiert
während einer Bewässerung nicht. Der modellspezifische Bewässerungs-Cooldown
beginnt am geplanten Ende des Impulses. Nach seinem Ablauf wird die aktuelle
Bodenfeuchte neu bewertet; ein alter `watering_armed=false`-Zustand kann die
Anlage nicht dauerhaft sperren.

## Sensoraufbereitung

Der ADS1115 liest pro Kanal standardmäßig neun Einzelwerte mit 40 ms Abstand.
Nur wenn eine strikte Mehrheit gültig ist, wird der Median verwendet.

Die Bodenfeuchte entsteht in zwei Stufen:

```text
Rohwerte -> Median -> lineare Trocken/Nass-Kalibrierung -> EMA
```

Für den exponentiellen gleitenden Mittelwert gilt:

```text
gefiltert_neu = alpha × direkt_neu + (1 - alpha) × gefiltert_alt
```

Standardmäßig ist `alpha = 0.2`. Ein kleinerer Wert glättet stärker, reagiert
aber langsamer. Controller und Safety erhalten ausschließlich den gefilterten
Prozentwert. Der Filter wird zurückgesetzt, wenn ADC-Adresse, Kanal oder
Kalibrierung geändert werden. Nach einem kurzen Daemon-Neustart wird ein
hinreichend aktueller Filterzustand wiederhergestellt.

Die ADC-Spanne, Zahl gültiger Samples, direkte Prozentzahl und der gefilterte
Regelwert bleiben im Dashboard sichtbar. Dadurch verdeckt die Glättung keine
Hardwareprobleme.

## Wetterpfad

`greenhouse.weather` verwendet die Open-Meteo Forecast API ohne zusätzlichen
API-Schlüssel. Der Client holt aktuelle Außenwerte und stündliche Daten.
Innerhalb des Vorhersagefensters werden:

- Niederschlagsmengen summiert,
- Niederschlagswahrscheinlichkeiten als Maximum übernommen,
- stündliche Punkte für die Anzeige bewahrt.

`CachedWeatherProvider` begrenzt Abrufe durch `refresh_seconds`. Bei kurzen
Netzfehlern darf der letzte Wert bis `max_stale_seconds` verwendet werden.
Der Wettercontroller besitzt zusätzlich `weather_max_age_seconds`; zu alte
Daten führen deterministisch zum lokalen Verhalten.

## Safety-Layer

Der gemeinsame Safety-Layer läuft nach jedem Controller:

- begrenzt einen Wasserimpuls,
- begrenzt die tägliche Ventillaufzeit,
- verhindert Bewässerung ohne gültigen aktivierten Bodensensor,
- verhindert Bewässerung bei veraltetem Snapshot,
- schaltet Lüfter bei ungültigen Luftwerten aus,
- schaltet Lüfter bei dauerhaft veraltetem Snapshot aus.

Angeforderte und ausgeführte Entscheidung werden getrennt protokolliert.
Safety-Eingriffe erscheinen als maschinenlesbare Codes.

## Modulübersicht

| Modul | Verantwortung |
| --- | --- |
| `greenhouse.config` | Defaults, Pfade, Deep-Merge und Validierung |
| `greenhouse.models` | Domänenobjekte |
| `greenhouse.calibration` | Rohwert-zu-Prozent-Abbildung |
| `greenhouse.trends` | lineare Trends |
| `greenhouse.controllers` | reine Regelungsvarianten und Registry |
| `greenhouse.safety` | gemeinsame Grenzen |
| `greenhouse.runtime` | Zustandsmaschine für Live und Replay |
| `greenhouse.hardware` | Relais- und ADS1115-Adapter |
| `greenhouse.weather` | Wetterclient und Cache |
| `greenhouse.records` | kanonische CSV-Serialisierung |
| `greenhouse.metrics` | getrennte Klima- und Ressourcenkennzahlen |
| `greenhouse.importer` | Import älterer Logs |
| `greenhouse.replay` | deterministische Wiedergabe |
| `web/*` | Pi-Orchestrierung, Kamera und Weboberfläche |
