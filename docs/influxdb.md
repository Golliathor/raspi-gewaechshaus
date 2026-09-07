# InfluxDB-2-Telemetrie

## Zweck und Ausfallverhalten

InfluxDB ergänzt die lokalen CSV-Dateien für Langzeitreihen und Grafana. Die
CSV-Protokollierung bleibt die maßgebliche lokale Aufzeichnung. Der
Automationsdaemon setzt zuerst die Relais, schreibt anschließend Snapshot und
Entscheidung in CSV und reiht erst danach die Influx-Telemetrie ein.

Der Writer verwendet direkt das InfluxDB-2-Line-Protocol über HTTP aus der
Python-Standardbibliothek. Damit kommt auf dem Pi Zero W keine schwere
Clientbibliothek hinzu. Genau ein Daemon-Thread bündelt bis zu zwölf
Regelzyklen beziehungsweise zehn Sekunden. Der Regelthread wartet dadurch nie
auf ein Netzwerk-Timeout.

Bei einem Ausfall gilt:

- Die Steuerung, Safety-Logik, Relais und lokalen CSV-Dateien laufen weiter.
- Fehlermeldungen erscheinen höchstens einmal pro Minute in `actions.csv`.
- Nach einem fehlgeschlagenen Request wird frühestens nach 60 Sekunden erneut
  geschrieben; Telemetriedaten können während des Ausfalls verworfen werden.
- Die Warteschlange ist auf 256 Regelzyklen begrenzt. Eine volle Warteschlange
  blockiert nicht, sondern verwirft nur den neuen Influx-Datensatz.
- Es gibt keine endlosen Wiederholungsversuche. Die lokalen CSV-Dateien können
  später weiterhin für Replay und Auswertung verwendet werden.

## Konfiguration

Die Live-Konfiguration liegt unter
`$GREENHOUSE_BASE_DIR/web/config.json`. Alle Parameter sind auch auf der
Konfigurationsseite verfügbar:

```json
"influxdb": {
  "enabled": true,
  "url": "http://100.88.152.72:8086",
  "org": "greenhouse",
  "bucket": "greenhouse",
  "token_file": "/etc/gewaechshaus/influx-token",
  "timeout_seconds": 3.0,
  "source": "growpi"
}
```

`timeout_seconds` darf höchstens 30 Sekunden betragen; für den Pi ist der
Standard von drei Sekunden empfohlen. `source` trennt Daten mehrerer Geräte.
Das Feld `token_file` enthält nur einen Dateipfad. Der Token selbst gehört
weder in die JSON-Datei noch in Umgebungsvariablen, Git oder Logs.

## Schreib-Token auf dem GrowPi einrichten

In InfluxDB einen eigenen API-Token anlegen, der ausschließlich Schreibzugriff
auf den Bucket `greenhouse` in der Organisation `greenhouse` besitzt. Auf dem
GrowPi läuft der Automationsdienst laut systemd-Beispiel als Benutzer und
Gruppe `grow`.

Die Datei einmalig vorbereiten:

```bash
sudo install -d -m 0750 -o root -g grow /etc/gewaechshaus
sudo touch /etc/gewaechshaus/influx-token
sudo chown root:grow /etc/gewaechshaus/influx-token
sudo chmod 0640 /etc/gewaechshaus/influx-token
sudoedit /etc/gewaechshaus/influx-token
```

In `sudoedit` genau den Schreib-Token eintragen. Lesbarkeit ohne Ausgabe des
Geheimnisses prüfen:

```bash
sudo -u grow test -r /etc/gewaechshaus/influx-token && echo "Token-Datei lesbar"
```

Danach InfluxDB über die Webseite aktivieren oder `enabled` in der Live-Datei
setzen und den Dienst neu starten:

```bash
sudo systemctl restart greenhouse-automation.service
```

Eine beim Start fehlende oder leere Token-Datei deaktiviert nur InfluxDB. Nach
dem späteren Anlegen der Datei den Dienst neu starten, damit der Token neu
eingelesen wird.

## Datenmodell

Jeder Feldwert wird mit dem ursprünglichen `SensorSnapshot`- oder
`CycleResult`-Zeitpunkt in Nanosekundenpräzision geschrieben, nicht mit dem
HTTP-Sendezeitpunkt. Naive Live-Zeitstempel werden anhand der Systemzeitzone
des GrowPi interpretiert. Deshalb müssen Uhr und Zeitzone vor einem Versuch
korrekt sein.

### Measurement `sensor`

Tags mit kontrollierter Kardinalität:

- `source`
- `quality`

Numerische Fields, sofern vorhanden:

- `temperature_c`, `humidity_percent`
- `soil1_percent`, `soil2_percent`, `soil3_percent`
- `light_percent`
- `outside_temperature_c`, `outside_humidity_percent`
- `precipitation_mm`, `precipitation_probability_percent`

`None`, `NaN` und unendliche Werte werden nicht als Strings gespeichert. Wenn
alle Zahlen fehlen, hält `valid_numeric_field_count=0` den Qualitätspunkt
dennoch abfragbar.

### Measurement `control`

Tags:

- `source`
- `controller_id`
- `run_id`

Kern-Fields:

- angeforderte und angewendete Zustände von Abluft und Umluft
- angeforderte und angewendete Bewässerungssekunden
- `water_valve`, `watering_started_seconds`, `weather_available`
- `estimated_water_ml` aus gestarteter Ventillaufzeit mal
  `water_flow_ml_per_second`
- Anzahlen für Reasons, Sensorprobleme, Safety-Eingriffe und Übergänge
- je ein numerisches Übergangsfeld für Abluft, Umluft und Wasserventil

Numerische und boolesche Controllerdiagnosen werden mit dem Präfix
`diagnostic_` als Fields abgelegt. Freie Diagnosetexte werden bewusst nicht in
`control` übernommen.

### Measurement `event`

Das optionale Measurement entsteht, sobald mindestens ein Grund, Sensorfehler,
Safety-Eingriff, Aktorübergang oder Bewässerungsstart vorliegt. Es verwendet
nur `source`, `controller_id` und `run_id` als Tags. Die variablen Codes stehen
als String-Fields `controller_reasons`, `sensor_issues`, `safety_overrides` und
`transitions`. So erzeugen wechselnde Texte keine unkontrollierte
Tag-Kardinalität.

## Verbindung und ersten Datensatz prüfen

Zuerst vom GrowPi nur die Servergesundheit prüfen:

```bash
curl -s http://100.88.152.72:8086/health
```

Nach Aktivierung und Neustart mindestens zehn Sekunden warten. In
`logs/actions.csv` darf kein aktueller `influx_disabled`- oder
`influx_write_failed`-Eintrag stehen. In der InfluxDB Data Explorer- oder
Grafana-Flux-Abfrage zeigt folgendes Beispiel die neuesten Punkte:

```flux
from(bucket: "greenhouse")
  |> range(start: -15m)
  |> filter(fn: (r) =>
    r._measurement == "sensor" or
    r._measurement == "control" or
    r._measurement == "event"
  )
  |> filter(fn: (r) => r.source == "growpi")
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 30)
```

Für den Modellvergleich zusätzlich nach `controller_id` und der beim Versuch
gesetzten `run_id` filtern. Ein erfolgreicher Test zeigt mindestens
`sensor`- und `control`-Punkte; `event` ist zustandsabhängig.
