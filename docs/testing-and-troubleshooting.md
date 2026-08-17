# Tests und Fehlerdiagnose

## Automatisierte Tests

Gesamte Suite:

```bash
python3 -m unittest discover -v
```

Mit vollständigen Webabhängigkeiten:

```bash
.venv/bin/python -m unittest discover -v
```

Die Tests decken unter anderem ab:

- Snapshot-Validierung und Kalibrierung,
- Konfigurations-Merge und Validierung,
- alle vier Controller,
- Hysterese, Sperrzeiten und wiederhergestellten Zustand,
- Safety-Grenzen,
- Wetterclient, Cache und Fallback,
- ADC-Median und EMA-Filter,
- Replay-Reproduzierbarkeit und Kennzahlen,
- Webkonfiguration, Status-API und Befehle.

Syntax- und JSON-Prüfung:

```bash
python3 -m compileall -q greenhouse web tests
python3 -m json.tool examples/config.json >/dev/null
git diff --check
```

## Raspberry-Pi-Smoke-Test

Vor Automatikbetrieb:

1. `i2cdetect -y 1` zeigt den ADS1115.
2. DHT-Logger schreibt plausible Zeilen in `logs/klima.csv`.
3. Dashboard zeigt aktuelle Sensorzeit.
4. Automatik ist deaktiviert.
5. Jedes Lüfterrelais wird einzeln kurz getestet.
6. Ventil wird mit kleinem, kontrolliertem Impuls getestet.
7. Relais sind nach Stoppen des Daemons aus.
8. Kamera-Testaufnahme hat korrektes Datum.
9. Wetterkarte zeigt bei aktiviertem Wetter aktuelle Werte und Vorhersage.
10. Erst danach Automatik aktivieren und `actions.csv` beobachten.

## Bodenfeuchte schwankt stark

Im Dashboard vier Werte vergleichen:

- `ADC-Median`: stabilisierter Rohwert des aktuellen Batches
- `Direkt`: sofort kalibrierter Prozentwert
- `Gefilterter Regelwert`: vom Controller verwendeter EMA-Wert
- `ADC-Spanne`: Maximum minus Minimum im Batch

Interpretation:

- Große ADC-Spanne: elektrische Störung, Versorgung, Kontakt, Kabellänge,
  Korrosion oder instabiler Sensor.
- Kleine ADC-Spanne, aber springender Direktwert: Kalibrierbereich ist zu eng
  oder Sensor/Boden-Kontakt verändert sich.
- Direktwert plausibel, Regelwert zu träge: `soil_filter_alpha` vorsichtig
  erhöhen, beispielsweise von 0,2 auf 0,3.
- Regelwert noch zu unruhig: Alpha vorsichtig senken oder Samplezahl auf 11/15
  erhöhen. Dadurch steigt Reaktions- beziehungsweise Messdauer.

Filterung löst keinen Hardwarefehler. Kapazitive Sensoren, kurze Leitungen,
gemeinsame Masse, saubere 3,3-V-Versorgung und Abstand zu Pumpen-/Relaiskabeln
sind vorzuziehen. Messung während hoher Lasten prüfen.

## Keine Bodenwerte

Prüfen:

```bash
i2cdetect -y 1
journalctl -u greenhouse-automation -n 100
```

Danach:

- `adc_enabled`,
- dezimale `adc_address`,
- Kanal 0 bis 3,
- aktivierte Sensoren,
- I²C-Verkabelung und Masse,
- `valid_samples` in `/api/status`.

Weniger als die strikte Mehrheit gültiger Samples ergibt bewusst keinen
Messwert. Der Safety-Layer verhindert dann Bewässerung.

## Klima fehlt oder ist veraltet

```bash
tail -n 5 "$GREENHOUSE_BASE_DIR/logs/klima.csv"
systemctl status greenhouse-climate
```

Zeitstempel, DHT-Verkabelung an GPIO 4 und Dienstlog prüfen. Bei ungültigen
Luftwerten fordert der Safety-Layer beide Lüfter AUS. Bei veraltetem Snapshot
wird zuerst Bewässerung, später auch Lüftung abgeschaltet.

## Wetter fehlt

Prüfen:

- `weather.enabled=true`,
- Provider exakt `open_meteo`,
- Koordinaten,
- DNS und Internetzugang,
- `last_weather_error` in `/api/status`,
- Alter des Wetterzeitpunkts,
- Systemzeit und Zeitzone.

Ansatz B fällt ohne brauchbare Wetterdaten auf Ansatz A zurück. Das ist kein
unkontrollierter Zwischenzustand; `weather_status` erklärt den Grund.

## Relais reagieren umgekehrt

Die Software erwartet active-low. Verdrahtung und Relaiskarte prüfen. Nicht
blind `ACTIVE_LOW` ändern, solange die sichere AUS-Stellung beim Booten,
Programmstart und Programmende nicht getestet wurde.

## Ventil startet nicht

Mögliche Ursachen:

- `watering_enabled=false`,
- Bewässerungsprüfung noch nicht fällig,
- Bodenwert oberhalb der Gießgrenze,
- Hysterese nicht wieder freigegeben,
- Sperrzeit läuft,
- Ventil bereits aktiv,
- ungültiger/fehlender Bodensensor,
- Tageslimit erreicht,
- Snapshot veraltet.

`last_decision_reasons`, `lock_remaining_seconds` und
`last_safety_overrides` im Dashboard oder in `control_decisions.csv` nennen
den konkreten Grund.

## Wasserimpuls endet früher als eingestellt

Die eingetragene Gießdauer ist eine Anforderung, keine Umgehung der
Sicherheitslimits. Die ausgeführte Dauer ist das Minimum aus Anforderung,
maximalem Einzelimpuls und verbleibendem Tageslimit. Wenn beispielsweise stets
100 statt 600 Sekunden geschaltet werden, steht der maximale Impuls oder die
verbleibende Tagesdauer auf 100 Sekunden.

Auf der Konfigurationsseite im gemeinsamen Abschnitt „Bewässerung“ prüfen:

- Legacy-/manuelle Gießdauer,
- maximaler Wasserimpuls,
- maximale Bewässerung pro Tag.

Das Dashboard zeigt die aktuelle Kapazität sowie angeforderte und ausgeführte
Dauer getrennt. Das Tageskonto wird um Mitternacht zurückgesetzt und liegt im
`control_runtime` von `state.json`.

## Kamera zeigt falsches Datum oder kein Bild

```bash
date
timedatectl status
rpicam-still -o /tmp/test.jpg --timeout 1000
systemctl status greenhouse-camera
```

Die Anzeigezeit des letzten Bildes stammt aus der Dateiänderungszeit von
`images/latest.jpg`. Falsche Systemzeit beim Fotografieren erzeugt falsche
Zeitstempel und Dateinamen.

## Diagrammfehler im Browser

Nach einem Update Browser hart neu laden (`Strg+F5`) oder Cache leeren. Der
aktuelle Code zerstört vorhandene Chart.js-Instanzen vor Wiederverwendung des
Canvas. Bei fortbestehendem Fehler Entwicklerkonsole und geladene Commitversion
prüfen.

## Web-App nicht erreichbar

```bash
systemctl status greenhouse-web
journalctl -u greenhouse-web -n 100
ss -ltn | grep 8080
curl -s http://127.0.0.1:8080/api/status
```

Die App bindet an `0.0.0.0:8080`. Firewall, WLAN-Adresse und gemeinsam gesetztes
`GREENHOUSE_BASE_DIR` prüfen.

## Speicher wächst

CSV-Logs und Bilder werden nicht automatisch langfristig gelöscht. Regelmäßig
archivieren und anschließend kontrolliert rotieren. Vor dem Löschen die
Versuchs-ID, Konfiguration und den verwendeten Commit sichern.
