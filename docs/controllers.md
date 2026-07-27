# Regelungsmodelle und Formeln

## Gemeinsames Verhalten

Alle Modelle steuern dieselben drei Aktoren und erhalten dieselben validierten
Sensordaten. Die Modelle liefern nur Anforderungen; der Safety-Layer darf sie
anschließend begrenzen. Lüfter werden in jedem Regelzyklus entschieden,
Bewässerung nur bei fälliger Bewässerungsprüfung.

| Eigenschaft | Fest | Hysterese | Adaptiv lokal | Adaptiv Wetter |
| --- | ---: | ---: | ---: | ---: |
| feste Grundgrenzen | ja | ja | ja | ja |
| EIN-/AUS-Hysterese | nein | ja | ja | ja |
| Mindestlaufzeiten | nein | ja | ja | ja |
| Bewässerungssperre | nein | ja | ja | ja |
| lokale Trends | nein | nein | ja | ja |
| Lichtanpassung | nein | nein | ja | ja |
| Wetteranpassung | nein | nein | nein | ja |

## Baseline 1: `baseline_fixed`

Der Controller ist zustandslos:

- Abluft EIN, wenn Temperatur mindestens 28 °C ist.
- Alternativ Abluft EIN, wenn Temperatur mindestens 18 °C und Luftfeuchte
  mindestens 50 % ist.
- Umluft EIN, wenn Temperatur mindestens 24 °C oder Luftfeuchte mindestens
  45 % ist.
- Bewässerung für 10 s, wenn mindestens ein gültiger aktivierter Bodensensor
  höchstens 35 % meldet.

Sinkt ein Wert im nächsten Zyklus unter den einzigen Schwellwert, wird der
betroffene Lüfter direkt ausgeschaltet. Dieses Modell ist bewusst einfach und
kann nahe einer Grenze häufig schalten.

## Baseline 2: `baseline_hysteresis`

Für jeden Lüfter existieren getrennte EIN- und AUS-Grenzen. Ein eingeschalteter
Lüfter bleibt an, bis Temperatur **und** Luftfeuchte ihre AUS-Grenzen erreicht
haben. Ein ausgeschalteter Lüfter startet, wenn Temperatur **oder**
Luftfeuchte die EIN-Grenze erreicht. Bei der Abluft gilt zusätzlich eine harte
Mindesttemperatur.

Standardwerte:

- Abluft: 28/25 °C, 50/40 %, mindestens 120 s EIN und 120 s AUS
- Umluft: 24/22 °C, 45/38 %, mindestens 120 s EIN und 120 s AUS
- Wasser: EIN bei höchstens 35 %, erneute Freigabe erst ab 45 % an allen
  aktivierten Sensoren, zusätzlich 3600 s Sperrzeit

Die Bewässerungsfreigabe wird nach einem Impuls entzogen. Sie wird erst wieder
gesetzt, wenn alle aktivierten Bodensensoren gültig sind und mindestens die
AUS-Grenze melden. Dieser Zustand und die letzte Bewässerung werden in
`state.json` gesichert.

## Ansatz A: `adaptive_local`

Ansatz A übernimmt Hysterese, Mindestlaufzeiten und die Bewässerungslogik von
Baseline 2. Zusätzlich werden lineare Trends aus der Historie berechnet.
Standardmäßig:

- Trendfenster: 15 min
- notwendige Messspanne: 2 min
- Vorausschau: 10 min

Trends werden pro Minute berechnet und gegen konfigurierbare Maximalwerte
begrenzt. Doppelte Zeitstempel werden in der Trendberechnung zusammengeführt.

### Lichtfaktor

Mit Lichtwert `H` und Startwert 50 %:

```text
L = clamp((H - 50) / (100 - 50), 0, 1)
```

Unter 50 % ist `L = 0`, bei 100 % ist `L = 1`.

### Lüfteranpassung

```text
projizierte Erwärmung =
  max(0, Temperaturtrend) × Vorausschau

Temperaturabsenkung =
  clamp(
    projizierte Erwärmung + L × 1,5,
    0,
    3 °C
  )

Feuchteabsenkung =
  clamp(
    max(0, Feuchtetrend) × Vorausschau,
    0,
    10 Prozentpunkte
  )
```

Die EIN-Grenzen werden um die volle Absenkung reduziert, die AUS-Grenzen um
die Hälfte. So bleibt die Hysterese bestehen.

### Bewässerungsanpassung

```text
projizierter Bodenverlust =
  max(0, -Bodenfeuchtetrend) × Vorausschau

Temperaturstress =
  max(0, Innentemperatur - 25 °C)

Erhöhung der Gießgrenze =
  clamp(
    0,5 × projizierter Bodenverlust
    + 0,3 × Temperaturstress
    + L,
    0,
    5 Prozentpunkte
  )
```

Wenn mindestens ein Sensor die effektive Gießgrenze unterschreitet und
Hysterese sowie Sperrzeit freigeben:

```text
Gießdauer =
  clamp(
    10
    + 0,5 × Feuchtedefizit
    + 0,5 × projizierter Bodenverlust
    + 0,5 × Temperaturstress
    + 2 × L,
    5,
    30 Sekunden
  )
```

Ansatz A ignoriert Wetterdaten ausdrücklich und protokolliert
`weather_used=false`.

## Ansatz B: `adaptive_weather`

Ansatz B verwendet **alle lokalen Parameter aus `adaptive_local`** und ergänzt
nur den Abschnitt `adaptive_weather`. Ohne aktuelle, brauchbare Wetterdaten
ist das Ergebnis identisch zu Ansatz A.

### Außenluft und Abluft

Aus der Differenz von Innen- und Außentemperatur entsteht ein auf `[-1, 1]`
begrenzter Kühlungsscore. Kühlere Außenluft senkt die effektive
Abluft-Temperaturgrenze, heißere Außenluft erhöht sie. Die Standardkorrektur
ist auf ±1,5 °C begrenzt.

Für Feuchte wird nicht die relative Feuchte direkt verglichen. Aus Temperatur
und relativer Feuchte wird die absolute Feuchte in g/m³ berechnet. Trocknere
Außenluft senkt, feuchtere Außenluft erhöht die Abluft-Feuchtegrenze um
maximal ±5 Prozentpunkte.

Die Korrekturen verschieben EIN- und AUS-Grenzen unterschiedlich, damit die
Hysterese erhalten bleibt.

### Hitze und Regen

- Außenhitze oberhalb 28 °C erhöht die Gießgrenze um maximal 2 Prozentpunkte
  und die Dauer um maximal 30 %.
- Eine Regenwirkung wird erst ab 60 % prognostizierter Wahrscheinlichkeit
  berücksichtigt.
- Der Regenscore kombiniert Wahrscheinlichkeit und Niederschlagsmenge,
  bezogen auf 5 mm.
- Regen senkt die Gießgrenze um maximal 2 Prozentpunkte und die Dauer um
  maximal 30 %.
- Bei höchstens 20 % Bodenfeuchte wird die Regenabsenkung ignoriert.
- Der endgültige Dauermultiplikator bleibt zwischen 0,7 und 1,3.

Die Logik nimmt nicht an, dass Regen direkt ins Gewächshaus gelangt. Regen ist
lediglich ein moderater Indikator für geringere Verdunstungsbelastung.

## `legacy`

Der Legacy-Controller bildet das Verhalten vor der Plattformtrennung ab und
bleibt für Kompatibilitäts- und Charakterisierungstests registriert. Er nutzt
die alten Top-Level-Grenzwerte. Wenn alle Bodenwerte fehlen, darf er einmal pro
Tag die konfigurierte Fallback-Dauer anfordern; der Safety-Layer blockiert
diese Anforderung jedoch, wenn kein gültiger aktivierter Bodensensor vorliegt.
Für neue Vergleiche sollte einer der vier Hauptcontroller verwendet werden.

## Modell wechseln

Im Dashboard die Modellkarte öffnen, Modell auswählen und den
Bestätigungsdialog akzeptieren. Alternativ:

```bash
curl -X POST http://localhost:8080/api/controller \
  -H 'Content-Type: application/json' \
  -d '{"controller_id":"adaptive_local"}'
```

Die Auswahl wird in `controller.active` gespeichert. Der Daemon übernimmt sie
im nächsten Regelzyklus ohne Neustart. Controllerinterner Zustand wird nur
wiederhergestellt, wenn die gespeicherte Controller-ID passt.

## Entscheidung nachvollziehen

Das Dashboard zeigt:

- aktive Controller-ID,
- maschinenlesbare Gründe,
- wirksame Schwellwerte und Trends,
- verbleibende Sperrzeiten,
- Wetterstatus und Wetterscores,
- Safety-Eingriffe,
- angeforderte und tatsächliche Aktorzustände.

Dieselben Daten stehen vollständig in `logs/control_decisions.csv`.
