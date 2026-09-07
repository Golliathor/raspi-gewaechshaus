# Vollständige Konfigurationsreferenz

## Laden und Speichern

Die aktive Datei ist:

```text
$GREENHOUSE_BASE_DIR/web/config.json
```

Fehlende Einträge werden rekursiv aus `greenhouse.config.DEFAULT_CONFIG`
ergänzt. Die Weboberfläche speichert atomar und protokolliert Änderungen nach
`logs/config_aenderungen.csv`. `examples/config.json` ist eine
Replay-Beispielkonfiguration, nicht automatisch die Live-Datei.

## Allgemeine Einstellungen

| Schlüssel | Standard | Bedeutung |
| --- | ---: | --- |
| `greenhouse_name` | `Raspi-Gewächshaus` | Anzeigename |
| `automation_enabled` | `true` | automatische Controllerentscheidungen |
| `watering_enabled` | `true` | automatische Bewässerungsanforderungen |
| `watering_seconds` | `10` | manuelle/Legacy-Standarddauer |
| `watering_fallback_seconds` | `40` | Legacy-Fallback bei fehlenden Bodenwerten |
| `water_flow_ml_per_second` | `25` | Durchfluss für Verbrauchsschätzung |
| `sensor_read_interval_seconds` | `30` | Abstand lokaler ADC-Messungen |
| `watering_check_interval_seconds` | `300` | Abstand von Bewässerungsprüfungen |
| `control_loop_interval_seconds` | `5` | Abstand der Regelzyklen |

Die folgenden alten Top-Level-Schlüssel gehören ausschließlich zum
`legacy`-Controller:

| Schlüssel | Standard |
| --- | ---: |
| `exhaust_temp_on_c` | `28` |
| `exhaust_temp_off_c` | `25` |
| `exhaust_humidity_on` | `50` |
| `exhaust_humidity_off` | `40` |
| `exhaust_min_temp_c` | `18` |
| `circulation_temp_on_c` | `24` |
| `circulation_temp_off_c` | `22` |
| `circulation_humidity_on` | `45` |
| `circulation_humidity_off` | `38` |

## Dashboard und Auswertung

| Schlüssel | Standard | Bedeutung |
| --- | ---: | --- |
| `refresh_seconds` | `15` | Statusaktualisierung im Browser |
| `chart_refresh_seconds` | `60` | Diagrammaktualisierung |
| `chart_points` | `200` | Anzahl geladener Messpunkte |
| `daily_summary_days` | `14` | Standardzeitraum Tagesübersicht |
| `daily_summary_cache_max_age_seconds` | `300` | Cachealter der Tagesübersicht |
| `daily_summary_row_safety_factor` | `2` | Reserve beim begrenzten Loglesen |

## InfluxDB-Telemetrie

| Schlüssel | Standard | Bedeutung |
| --- | --- | --- |
| `influxdb.enabled` | `false` | zusätzliche zentrale Speicherung aktivieren |
| `influxdb.url` | `http://100.88.152.72:8086` | InfluxDB-2-Basisadresse |
| `influxdb.org` | `greenhouse` | Organisation |
| `influxdb.bucket` | `greenhouse` | Ziel-Bucket |
| `influxdb.token_file` | `/etc/gewaechshaus/influx-token` | Datei mit reinem Write-Token |
| `influxdb.timeout_seconds` | `3` | kurzer HTTP-Timeout, maximal 30 s |
| `influxdb.source` | `growpi` | stabile Gerätekennung für Tags |

Der Token selbst ist kein Konfigurationswert. Einrichtung, Messschema,
Ausfallverhalten und Prüfquery stehen in
[InfluxDB-2-Telemetrie](influxdb.md).

## ADC und Filter

| Schlüssel | Standard | Bedeutung |
| --- | ---: | --- |
| `adc_enabled` | `true` | ADS1115 verwenden |
| `adc_type` | `ADS1115` | Anzeigename/Typ |
| `adc_address` | `72` | I²C-Adresse, dezimal (`0x48`) |
| `adc_sample_count` | `9` | ungerade Samplezahl von 1 bis 31 |
| `adc_sample_interval_ms` | `40` | Abstand der Samples, 0 bis 1000 ms |
| `soil_filter_alpha` | `0.2` | EMA-Faktor, größer 0 bis 1 |

`alpha = 1` deaktiviert die zeitliche Glättung praktisch. Ein kleinerer Wert
glättet stärker. Änderungen an Adresse, Kanal oder Kalibrierung setzen den
zugehörigen Filter zurück.

## Bodenfeuchtesensoren

`soil_sensors` enthält genau bis zu drei Einträge:

| Feld | Standard Sensor 1 | Bedeutung |
| --- | ---: | --- |
| `name` | `Sensor 1` | Anzeigename |
| `enabled` | `true` | in Messung und Regelung verwenden |
| `channel` | `0` | ADS1115-Kanal 0 bis 3 |
| `dry_below_percent` | `35` | nur Legacy-Grenze |
| `calibration_raw_dry` | `26000` | Rohwert im trockenen Referenzzustand |
| `calibration_raw_wet` | `12000` | Rohwert im nassen Referenzzustand |

Sensor 2 und 3 sind standardmäßig deaktiviert und nutzen Kanal 1 bzw. 2. Die
Kalibrierung darf auch umgekehrt verlaufen; die Abbildung berücksichtigt die
Richtung. Identische Trocken- und Nasswerte sind ungültig.

## Lichtsensor

| Feld | Standard | Bedeutung |
| --- | ---: | --- |
| `light_sensor.name` | `Lichtsensor` | Anzeigename |
| `light_sensor.enabled` | `true` | Messung aktiv |
| `light_sensor.channel` | `3` | ADS1115-Kanal |
| `light_sensor.calibration_raw_dark` | `26000` | 0-%-Referenz |
| `light_sensor.calibration_raw_bright` | `2000` | 100-%-Referenz |

## Kamera

| Schlüssel | Standard | Bedeutung |
| --- | ---: | --- |
| `camera_enabled` | `true` | geplante Aufnahmen |
| `camera_image_dir` | `images` | absolut oder relativ zur Datenwurzel |
| `camera_filename_pattern` | `%Y-%m-%d_%H-%M-%S.jpg` | `strftime`-Muster |
| `camera_width` | `1920` | Bildbreite |
| `camera_height` | `1080` | Bildhöhe |
| `camera_quality` | `93` | JPEG-Qualität |
| `camera_rotation` | `0` | unterstützt: 0 oder 180 |
| `camera_hflip` | `false` | horizontal spiegeln |
| `camera_vflip` | `false` | vertikal spiegeln |
| `camera_timeout_ms` | `1000` | Einregelzeit der Kamera |
| `timelapse_morning` | `08:00` | Morgenslot |
| `timelapse_noon` | `13:00` | Mittagsslot |
| `timelapse_evening` | `19:00` | Abendslot |

## Wetter

| Schlüssel | Standard | Bedeutung |
| --- | ---: | --- |
| `weather.enabled` | `true` | Wetterabruf aktiv |
| `weather.provider` | `open_meteo` | aktuell unterstützter Anbieter |
| `weather.latitude` | `50.766778` | 50°46'00.4"N |
| `weather.longitude` | `12.979194` | 12°58'45.1"E |
| `weather.forecast_horizon_hours` | `6` | Verdichtungsfenster, intern 1 bis 48 h |
| `weather.refresh_seconds` | `900` | Mindestabstand der Abrufe |
| `weather.max_stale_seconds` | `3600` | maximale Cache-Nutzung |
| `weather.request_timeout_seconds` | `10` | Netzwerk-Timeout |
| `weather.base_url` | Open-Meteo Forecast API | Endpunkt |

## Aktiver Controller

| Schlüssel | Standard | Bedeutung |
| --- | ---: | --- |
| `controller.active` | `adaptive_weather` | aktive Controller-ID |
| `controller.history_size` | `720` | maximale Snapshot-Historie im Speicher |

Gültige IDs: `baseline_fixed`, `baseline_hysteresis`, `adaptive_local`,
`adaptive_weather` und `legacy`.

## Parameter `baseline_fixed`

Alle Schlüssel liegen unter `controllers.baseline_fixed`.

| Schlüssel | Standard |
| --- | ---: |
| `exhaust_temperature_threshold_c` | `28` |
| `exhaust_humidity_threshold_percent` | `50` |
| `exhaust_min_temperature_c` | `18` |
| `circulation_temperature_threshold_c` | `24` |
| `circulation_humidity_threshold_percent` | `45` |
| `soil_moisture_threshold_percent` | `35` |
| `watering_seconds` | `10` |

## Parameter `baseline_hysteresis`

Alle Schlüssel liegen unter `controllers.baseline_hysteresis`.

| Gruppe | Schlüssel und Standard |
| --- | --- |
| Abluft | `exhaust_temperature_on_c=28`, `exhaust_temperature_off_c=25`, `exhaust_humidity_on_percent=50`, `exhaust_humidity_off_percent=40`, `exhaust_min_temperature_c=18` |
| Abluftzeiten | `exhaust_min_on_seconds=120`, `exhaust_min_off_seconds=120` |
| Umluft | `circulation_temperature_on_c=24`, `circulation_temperature_off_c=22`, `circulation_humidity_on_percent=45`, `circulation_humidity_off_percent=38` |
| Umluftzeiten | `circulation_min_on_seconds=120`, `circulation_min_off_seconds=120` |
| Wasser | `soil_moisture_on_percent=35`, `soil_moisture_off_percent=45`, `watering_seconds=10`, `watering_cooldown_seconds=3600` |

Die Lüfter-EIN-Werte müssen oberhalb ihrer AUS-Werte liegen. Für Bodenfeuchte
muss `soil_moisture_on_percent` unter `soil_moisture_off_percent` liegen; der
zweite Wert ist eine obere Zielreferenz. Die erneute Pulsfreigabe hängt vom
Cooldown ab und nicht vom Erreichen dieses Werts.

## Parameter `adaptive_local`

Alle Schlüssel liegen unter `controllers.adaptive_local`.

| Gruppe | Schlüssel und Standard |
| --- | --- |
| Abluft | dieselben 7 Abluftwerte wie Hysterese |
| Umluft | dieselben 6 Umluftwerte wie Hysterese |
| Trends | `trend_window_seconds=900`, `trend_minimum_span_seconds=120`, `trend_lookahead_minutes=10` |
| Trendgrenzen | `max_abs_temperature_trend_per_minute=2`, `max_abs_humidity_trend_per_minute=10`, `max_abs_soil_trend_per_minute=10` |
| Licht/Klima | `light_adaptation_start_percent=50`, `light_temperature_reduction_c=1.5`, `max_temperature_reduction_c=3`, `max_humidity_reduction_percent=10` |
| Boden | `soil_moisture_on_percent=35`, `soil_moisture_off_percent=45`, `max_soil_threshold_increase_percent=5` |
| Wasser | `watering_temperature_reference_c=25`, `watering_base_seconds=10`, `watering_min_seconds=5`, `watering_max_seconds=30`, `watering_cooldown_seconds=3600` |

`soil_moisture_off_percent` begrenzt außerdem die maximal mögliche adaptive
Einschaltschwelle. Es verriegelt keine Folgeimpulse.

## Parameter `adaptive_weather`

Ansatz B liest zuerst die lokalen Parameter aus `adaptive_local`. Unter
`controllers.adaptive_weather` liegen nur Ergänzungen:

| Schlüssel | Standard | Wirkung |
| --- | ---: | --- |
| `weather_max_age_seconds` | `3600` | maximale Datenaktualität im Controller |
| `outdoor_temperature_difference_scale_c` | `10` | Normierung Innen/Außen-Differenz |
| `absolute_humidity_difference_scale_g_m3` | `5` | Normierung absoluter Feuchte |
| `max_outdoor_temperature_adjustment_c` | `1.5` | maximale Temperaturkorrektur |
| `max_outdoor_humidity_adjustment_percent` | `5` | maximale Feuchtekorrektur |
| `outdoor_heat_reference_c` | `28` | Beginn Außenhitzestress |
| `outdoor_heat_scale_c` | `8` | Bereich bis zum vollen Hitzescore |
| `rain_probability_threshold_percent` | `60` | Mindestwahrscheinlichkeit |
| `rain_amount_reference_mm` | `5` | Menge für vollen Niederschlagsscore |
| `max_heat_soil_threshold_increase_percent` | `2` | Hitzekorrektur Gießgrenze |
| `max_rain_soil_threshold_reduction_percent` | `2` | Regenkorrektur Gießgrenze |
| `max_heat_watering_increase` | `0.3` | maximale Dauererhöhung |
| `max_rain_watering_reduction` | `0.3` | maximale Dauerreduktion |
| `watering_multiplier_min` | `0.7` | untere Multiplikatorgrenze |
| `watering_multiplier_max` | `1.3` | obere Multiplikatorgrenze |
| `critical_soil_moisture_percent` | `20` | darunter keine Regenreduktion |

## Safety

| Schlüssel | Standard | Bedeutung |
| --- | ---: | --- |
| `safety.max_watering_pulse_seconds` | `60` | Obergrenze je Impuls |
| `safety.max_daily_watering_seconds` | `180` | tägliche Ventillaufzeit |
| `safety.sensor_stale_after_seconds` | `180` | danach keine Bewässerung |
| `safety.safe_state_after_seconds` | `600` | danach Lüfter AUS |

Manuelle Wasserbefehle werden ebenfalls durch Impuls- und Tagesgrenze
begrenzt. Die tatsächlich mögliche Dauer ist:

```text
min(
  gewünschte Dauer,
  safety.max_watering_pulse_seconds,
  safety.max_daily_watering_seconds - heutige Bewässerungssekunden
)
```

Beispiel: Eine Gießdauer von 600 s bei einem maximalen Wasserimpuls von 100 s
schaltet genau 100 s. Für einen vollständigen 600-s-Impuls müssen sowohl
`max_watering_pulse_seconds` als auch `max_daily_watering_seconds` mindestens
600 s betragen. Diese Limits nur nach Prüfung von Durchfluss, Behälter,
Drainage und Ausfallsicherheit erhöhen.

## Zielbereiche für Kennzahlen

| Schlüssel | Standard |
| --- | ---: |
| `targets.temperature_min_c` | `18` |
| `targets.temperature_max_c` | `28` |
| `targets.humidity_min_percent` | `40` |
| `targets.humidity_max_percent` | `70` |
| `targets.soil_moisture_min_percent` | `35` |
| `targets.soil_moisture_max_percent` | `70` |

Diese Werte dienen der Auswertung. Sie sind nicht automatisch die
Regelschwellwerte.

## Umgebungsvariablen

| Variable | Standard | Bedeutung |
| --- | --- | --- |
| `GREENHOUSE_BASE_DIR` | `/home/grow/gewaechshaus` | Datenwurzel |
| `GREENHOUSE_RUN_ID` | `live-YYYY-MM-DD` | Versuchskennung in Entscheidungslogs |
