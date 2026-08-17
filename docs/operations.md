# Weboberfläche, API, Logs und laufender Betrieb

## Dashboard

Die Startseite zeigt:

- Innenklima und Verlauf,
- Bodenfeuchte und Licht,
- ADC-Median, ungefilterten Wert, EMA-Regelwert, ADC-Spanne und Samplequalität,
- aktuelle Relaiszustände,
- aktives Modell, Entscheidungsgründe und Safety-Eingriffe,
- aktuelle Wetterwerte und stündliche Vorhersage,
- letztes Kamerabild,
- Tagesübersichten für Klima, Licht, Wasser und Lüfter.

Die Modellkarte erlaubt den Wechsel zwischen allen Hauptmodellen. Eine
Bestätigung verhindert versehentliche Wechsel.

## Konfiguration und Kalibrierung

`/config` bearbeitet gemeinsame, modellbezogene, Safety-, Wetter-, Kamera- und
Sensorparameter. Jede Änderung wird validiert und in
`config_aenderungen.csv` protokolliert.

`/calibration` übernimmt den aktuellen ADC-Median als Trocken- oder
Nassreferenz. Empfohlenes Vorgehen:

1. Sensor in einen reproduzierbaren trockenen Referenzzustand bringen.
2. Warten, bis Rohwert und ADC-Spanne stabil sind.
3. „Trocken“ übernehmen.
4. Sensor in nassen Referenzzustand bringen, nicht nur kurz benetzen.
5. Erneut stabilisieren lassen und „Nass“ übernehmen.
6. Plausibilität bei einem Zwischenzustand prüfen.

Nach einer Kalibrierungsänderung startet der EMA-Filter dieses Sensors neu.

## Manuelle Steuerung

Relaisbefehle und Wasserimpulse werden von der Web-App nicht direkt auf GPIO
geschrieben. Die Web-App legt `web/command.json` an; der Automationsdaemon
verarbeitet und entfernt die Datei im nächsten Zyklus.

Manuelle Wasseranforderungen unterliegen:

- maximaler Impulsdauer,
- verbleibendem Tageslimit,
- Schutz vor einem bereits laufenden Impuls.

Das Dashboard zeigt deshalb direkt unter der Bewässerung die aktuell maximal
mögliche Dauer und die verbleibende Tagesdauer. Nach der Verarbeitung steht
bei „Letzter manueller Impuls“ sowohl die angeforderte als auch die tatsächlich
ausgeführte Zeit. Eine Anzeige wie „100 s ausgeführt von 600 s angefordert“ ist
ein Safety-Eingriff und kein Zeitgeberfehler.

Bei aktivierter Automatik kann der nächste Regelzyklus einen manuell gesetzten
Lüfterzustand wieder ändern. Für einen kontrollierten manuellen Test zuerst die
Automatik deaktivieren.

## HTTP-API

Die API besitzt derzeit keine Authentifizierung. Nur in einem vertrauenswürdigen
LAN verwenden.

| Methode und Pfad | Zweck |
| --- | --- |
| `GET /` | Dashboard |
| `GET, POST /config` | Konfiguration |
| `GET /calibration` | Kalibrierungsseite |
| `GET /api/status` | Gesamtstatus einschließlich Controller und Wetter |
| `GET /api/chart?points=200` | Klima-Diagrammdaten |
| `GET /api/sensor_chart?points=200` | Boden-/Licht-Diagrammdaten |
| `GET /api/daily_summary?days=14` | Tageskennzahlen, 1 bis 365 Tage |
| `POST /api/relays/<name>` | Relaisanforderung, JSON `{"on":true}` |
| `POST /api/automation/toggle` | JSON `{"enabled":false}` |
| `POST /api/controller` | JSON `{"controller_id":"adaptive_local"}` |
| `POST /api/water_pulse` | JSON `{"seconds":10}`; Antwort enthält aktuelle Limits |
| `POST /api/calibrate/<index>` | JSON `{"calibration_type":"dry"}` |
| `POST /api/camera/test_capture` | unmittelbares Testbild |
| `GET /latest.jpg` | aktuelles Zeitrafferbild |
| `GET /test_capture.jpg` | letztes Testbild |
| `GET /download/images` | Bilder als ZIP |
| `GET /download/logs` | Logs als ZIP |

Beispiel:

```bash
curl -s http://localhost:8080/api/status
curl -X POST http://localhost:8080/api/automation/toggle \
  -H 'Content-Type: application/json' \
  -d '{"enabled":false}'
```

## Laufzeitdateien

| Datei | Inhalt |
| --- | --- |
| `web/config.json` | persistente Konfiguration |
| `web/state.json` | Zustände, letzte Werte, Wettercache, Regelzustand |
| `web/command.json` | genau ein noch nicht verarbeiteter Web-Befehl |

`state.json` wird von Automations- und Kameradaemon verwendet. Schreibvorgänge
erfolgen über temporäre Datei und atomisches Ersetzen.

## Logdateien

| Datei | Inhalt |
| --- | --- |
| `logs/klima.csv` | DHT-Zeitpunkt, Temperatur, Luftfeuchte |
| `logs/sensoren.csv` | ADC-Mediane, gefilterte Bodenwerte, Licht |
| `logs/actions.csv` | Webbefehle, Aktorübergänge, Safety-Ereignisse |
| `logs/control_snapshots.csv` | kanonischer Snapshot jedes Regelzyklus |
| `logs/control_decisions.csv` | Anforderung, Anwendung, Gründe, Diagnosen |
| `logs/config_aenderungen.csv` | alter und neuer Konfigurationswert |
| `logs/daily_summary.json` | Cache der Dashboard-Tagesauswertung |

### `sensoren.csv`

Felder:

```text
timestamp,
soil1_raw,soil1_percent,
soil2_raw,soil2_percent,
soil3_raw,soil3_percent,
light_raw,light_percent,light_class
```

`soilN_raw` ist der Median des ADC-Batches. `soilN_percent` ist der gefilterte
Regelwert. Zusätzliche Batchdiagnosen stehen in `state.json` und `/api/status`,
nicht in dieser CSV.

### `control_snapshots.csv`

Felder:

```text
timestamp,temperature_c,humidity_percent,
soil1_percent,soil2_percent,soil3_percent,light_percent,
quality,issues,
weather_timestamp,outside_temperature_c,outside_humidity_percent,
precipitation_mm,precipitation_probability_percent,weather_provider
```

### `control_decisions.csv`

Enthält unter anderem:

- `run_id`, `controller_id` und Snapshot-Zeitpunkt,
- angeforderte und angewendete Lüfterzustände,
- angeforderte und angewendete Bewässerungsdauer,
- Ventilzustand und tatsächlich gestartete Dauer,
- Gründe mit `|` getrennt,
- Diagnoseobjekt als sortiertes JSON,
- Safety-Eingriffe und Aktorübergänge.

## Kameradaten

Der Kameradaemon erzeugt zu den drei konfigurierten Uhrzeiten jeweils maximal
ein Bild. `images/latest.jpg` ist ein Hardlink oder eine Kopie der letzten
Aufnahme. Die Website bestimmt den angezeigten Aufnahmezeitpunkt aus der
Dateiänderungszeit dieses tatsächlichen Bildes.

## Routinebetrieb

Täglich prüfen:

- Sensoralter und Datenqualität,
- ungewöhnlich große ADC-Spannen,
- Safety-Eingriffe,
- letzte Wetteraktualisierung bei Ansatz B,
- Ventillaufzeit und Wasserverbrauch,
- verfügbare Speicherkapazität.

Vor jedem Versuch:

1. eindeutige `GREENHOUSE_RUN_ID` setzen,
2. aktive Controller-ID dokumentieren,
3. Konfiguration sichern,
4. Wasserbehälter und Durchfluss prüfen,
5. Uhrzeit und Zeitzone prüfen,
6. Logs entweder archivieren oder den Versuchszeitraum eindeutig notieren.

## Sicherung und Wiederherstellung

Mindestens diese Inhalte sichern:

```text
web/config.json
web/state.json
logs/
images/
```

`config.json` ist zwingend. `state.json` erhält Sperrzeiten, Tagesverbrauch,
Filter- und Controllerzustand. Ohne diese Datei startet der Daemon mit sicheren
Standardzuständen, verliert aber die laufende Versuchskontinuität.

## Geordnetes Stoppen

Bei systemd:

```bash
sudo systemctl stop greenhouse-automation
```

Danach Relaiszustände physisch prüfen. Vor Wartung an Ventil, Pumpe oder
Netzspannung die Leistungsversorgung trennen.
