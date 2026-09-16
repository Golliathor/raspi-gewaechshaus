# Installation, Hardware und systemd

## Voraussetzungen

Empfohlen:

- Raspberry Pi mit Raspberry Pi OS und korrekter Systemzeit
- Python 3 mit `venv`
- DHT22 am GPIO-Pin des Klima-Loggers
- ADS1115 am I²C-Bus für Bodenfeuchte und Licht
- 3-kanalige, active-low Relaiskarte
- optional Raspberry-Pi-Kamera mit `rpicam-still`
- Netzwerkzugang für `adaptive_weather`

Die Anwendung setzt keine konkrete Pflanzenart voraus. Grenzwerte,
Kalibrierung und Sicherheitslimits müssen zum realen Aufbau passen.

## Aktuelle Pin- und Kanalbelegung

Die GPIO-Nummern sind BCM-Nummern:

| Funktion | Pin/Kanal | Quelle |
| --- | --- | --- |
| DHT22 | GPIO 4 | `klima_logger.py` |
| Abluftrelais | GPIO 17 | `web/relay_control.py` |
| Umluftrelais | GPIO 27 | `web/relay_control.py` |
| Wasserventil | GPIO 22 | `web/relay_control.py` |
| Boden 1 | ADS1115 A0 | konfigurierbar |
| Boden 2 | ADS1115 A1 | konfigurierbar |
| Boden 3 | ADS1115 A2 | konfigurierbar |
| Licht | ADS1115 A3 | konfigurierbar |

Die Relaislogik ist standardmäßig `ACTIVE_LOW = True`: GPIO LOW schaltet ein.
Vor Anschluss einer Pumpe oder eines Ventils die Logik mit ungefährlicher Last
prüfen. Ein GPIO darf keine Last direkt treiben.

Die Standardadresse des ADS1115 ist dezimal `72`, entsprechend `0x48`.

## Raspberry Pi vorbereiten

System aktualisieren und Werkzeuge installieren:

```bash
sudo apt update
sudo apt full-upgrade
sudo apt install git python3 python3-venv python3-pip i2c-tools
```

I²C und Kamera über `sudo raspi-config` aktivieren und danach neu starten.
Anschließend den ADC prüfen:

```bash
i2cdetect -y 1
```

In der Tabelle sollte normalerweise `48` erscheinen.

## Repository und Python-Abhängigkeiten

```bash
cd /home/grow
git clone git@github.com:Golliathor/raspi-gewaechshaus.git
cd raspi-gewaechshaus
git switch model/comparison
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r web/requirements.txt
```

Das Requirements-File enthält Flask, GPIO-, DHT- und ADS1115-Bibliotheken.
Replay und reine Controller-Tests benötigen keine Raspberry-Pi-Module.

## Datenverzeichnis

Code und veränderliche Daten sollten getrennt sein:

```bash
sudo install -d -o grow -g grow /home/grow/gewaechshaus-data/web
sudo install -d -o grow -g grow /home/grow/gewaechshaus-data/logs
sudo install -d -o grow -g grow /home/grow/gewaechshaus-data/images
export GREENHOUSE_BASE_DIR=/home/grow/gewaechshaus-data
```

Beim ersten Start erzeugt die Anwendung `web/config.json` aus den Defaults.
Alternativ:

```bash
cp examples/config.json "$GREENHOUSE_BASE_DIR/web/config.json"
```

Die vorhandene Datei darf unvollständig sein. Fehlende Werte werden beim Laden
aus den zentralen Defaults ergänzt; ungültige Werte werden beim Speichern über
die Weboberfläche abgewiesen.

## Manueller Erststart

Jeden Befehl in einem eigenen Terminal im Repository ausführen:

```bash
export GREENHOUSE_BASE_DIR=/home/grow/gewaechshaus-data
.venv/bin/python klima_logger.py
```

```bash
export GREENHOUSE_BASE_DIR=/home/grow/gewaechshaus-data
export GREENHOUSE_RUN_ID=pi-smoke-test
.venv/bin/python web/automation_daemon.py
```

```bash
export GREENHOUSE_BASE_DIR=/home/grow/gewaechshaus-data
.venv/bin/python web/camera_daemon.py
```

```bash
export GREENHOUSE_BASE_DIR=/home/grow/gewaechshaus-data
.venv/bin/python web/app.py
```

Dann `http://<IP-des-Pi>:8080` öffnen. Für den ersten Relaischeck Automatik
deaktivieren. Das Wasserventil nur mit kontrollierter Wassermenge testen.

## Empfohlener systemd-Betrieb

### Vorhandene Dienste zuerst feststellen

Die Unit-Namen sind Teil der Installation und werden nicht vom Python-Code
vorgegeben. Auf bestehenden GrowPi-Installationen heißt der
Automationsdienst `gewaechshaus-automation.service`. Vor dem Anlegen oder
Neustarten von Units deshalb immer zuerst die tatsächlich vorhandenen Namen
ermitteln:

```bash
systemctl list-unit-files --type=service | grep -Ei 'gewaechshaus|greenhouse|klima|camera'
pgrep -af 'klima_logger.py|automation_daemon.py|camera_daemon.py|web/app.py'
systemctl cat gewaechshaus-automation.service
```

Alternativ lässt sich ein bereits laufender Prozess direkt zuordnen, wobei
`<PID>` durch die angezeigte Prozessnummer ersetzt wird:

```bash
systemctl status <PID> --no-pager
```

Keine zweite Unit nur wegen eines abweichenden Namens anlegen. Zwei parallel
laufende Automationsdaemons würden dieselben Relais und Zustandsdateien
ansteuern. Die folgenden Namen sind die Konvention für eine Neuinstallation;
bei einem bestehenden System werden dessen vorhandene Namen weiterverwendet.

### Units neu anlegen

Gemeinsame Umgebung in `/etc/default/greenhouse`:

```ini
GREENHOUSE_BASE_DIR=/home/grow/gewaechshaus-data
GREENHOUSE_RUN_ID=praxis-2026
PYTHONUNBUFFERED=1
```

Grundmuster für einen Dienst:

```ini
[Unit]
Description=Gewächshaus Automationsdaemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=grow
Group=grow
WorkingDirectory=/home/grow/raspi-gewaechshaus
EnvironmentFile=/etc/default/greenhouse
ExecStart=/home/grow/raspi-gewaechshaus/.venv/bin/python web/automation_daemon.py
Restart=on-failure
RestartSec=5
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
```

`WorkingDirectory` und der vordere Teil von `ExecStart` müssen auf den
tatsächlichen Checkout zeigen. Dieses Muster als folgende Units anlegen und
`Description`/`ExecStart` anpassen:

| Unit | Python-Programm |
| --- | --- |
| `gewaechshaus-klima.service` | `klima_logger.py` |
| `gewaechshaus-automation.service` | `web/automation_daemon.py` |
| `gewaechshaus-kamera.service` | `web/camera_daemon.py` |
| `gewaechshaus-web.service` | `web/app.py` |

Danach:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gewaechshaus-klima.service
sudo systemctl enable --now gewaechshaus-automation.service
sudo systemctl enable --now gewaechshaus-kamera.service
sudo systemctl enable --now gewaechshaus-web.service
```

Status und Logs:

```bash
systemctl status gewaechshaus-automation.service
journalctl -u gewaechshaus-automation.service -f
```

Der Automationsdaemon schaltet in seinem regulären Beendigungspfad alle Relais
aus. Ein stromlos sicherer Hardwareaufbau bleibt trotzdem erforderlich.

## Update

Vor dem ersten Update einmal die realen Unit- und Repository-Pfade mit den
Befehlen im vorigen Abschnitt prüfen. Das folgende Beispiel verwendet den in
dieser Dokumentation empfohlenen Checkout und die deutschen Unit-Namen:

```bash
cd /home/grow/raspi-gewaechshaus
git switch model/comparison
git pull --ff-only
.venv/bin/pip install -r web/requirements.txt
.venv/bin/python -m unittest discover -v
sudo systemctl restart gewaechshaus-klima gewaechshaus-automation gewaechshaus-kamera gewaechshaus-web
```

Anschließend nicht nur den Exitstatus von `systemctl` beachten, sondern den
laufenden Stand prüfen:

```bash
systemctl --no-pager --full status gewaechshaus-klima gewaechshaus-automation gewaechshaus-kamera gewaechshaus-web
cat /home/grow/gewaechshaus-data/web/latest_climate.json
curl -s http://127.0.0.1:8080/api/status
ps -eo pid,comm,%cpu,%mem,etime --sort=-%cpu | head -20
```

Meldet `systemctl` „Unit ... not found“, wurde nicht die installierte Unit
verwendet. Dann nicht rebooten, sondern die Namen wie oben beschrieben
ermitteln und nur diese Dienste neu starten. Ein Reboot behebt keinen falschen
Unit-Namen.

Vor größeren Updates Konfiguration und Logs sichern:

```bash
cp -a /home/grow/gewaechshaus-data /home/grow/gewaechshaus-data.backup
```

Ein Neustart des gesamten Pi ist normalerweise nicht erforderlich. Nach
Kernel-, Firmware- oder Schnittstellenänderungen ist er sinnvoll.
