# spotsync

Exportiert deine **Spotify-Playlists und gespeicherten Titel als Metadaten**
(über die offizielle Spotify Web API, read-only) und gleicht sie gegen deine
**eigene lokale Musiksammlung** ab. Daraus baut es portable lokale Playlists
(`.m3u8`, lesbar von Navidrome/Jellyfin/Plex/VLC) und einen Lücken-Report
(„was fehlt mir noch" = Einkaufsliste).

## Was dieses Tool macht — und was nicht

**Macht es:**
- Playlists + „Liked Songs" als Metadaten exportieren (Titel, Artist, Album,
  Dauer, ISRC) via offizieller API, nur Lese-Scopes.
- Deine lokalen Dateien einlesen (Tags via `mutagen`).
- Abgleichen (ISRC → exakt → normalisiert → fuzzy).
- Lokale `.m3u8`-Playlists erzeugen, die auf deine eigenen Dateien zeigen.
- Lücken- und Dubletten-Report schreiben.

**Macht es ausdrücklich NICHT:**
- Kein Audio aus Spotify herunterladen oder entschlüsseln.
- Keine DRM-Umgehung, kein Premium-Bypass, keine modifizierten Clients.

Die Spotify-Seite liefert nur Metadaten; der Ton kommt ausschließlich aus
deinen eigenen, legal vorhandenen Dateien.

## Einrichtung

1. **App registrieren (kostenlos):** <https://developer.spotify.com/dashboard>
   → *Create app*. Als Redirect URI exakt eintragen:

   ```
   http://127.0.0.1:8888/callback
   ```

   Client-ID kopieren. (Kein Client-Secret nötig — wir nutzen PKCE.)

2. **Abhängigkeit installieren:**

   ```bash
   cd spotify-library-sync
   python3 -m pip install -r requirements.txt     # nur mutagen
   # optional: pip install -e .   -> stellt den Befehl `spotsync` bereit
   ```

3. **Client-ID setzen:**

   ```bash
   export SPOTSYNC_CLIENT_ID="deine_client_id"
   ```

## Benutzung

Ohne Installation läuft alles über `python -m spotsync` mit `PYTHONPATH=src`:

```bash
cd spotify-library-sync
export PYTHONPATH=src

# 1. Einmalig autorisieren (öffnet Browser, read-only)
python -m spotsync auth

# 2. Spotify-Metadaten exportieren
python -m spotsync export --out data/spotify

# 3. Lokale Bibliothek scannen (dein Musik-Ordner / Server-Mount)
python -m spotsync scan --root /pfad/zu/deiner/musik --out data/library.json

# 4. Abgleichen
python -m spotsync match --spotify data/spotify --library data/library.json --out data/matches.json

# 5. Lokale Playlists bauen
python -m spotsync playlists --matches data/matches.json --out data/playlists

# 6. Lücken + Dubletten
python -m spotsync report --matches data/matches.json --library data/library.json --out data/reports
```

Oder alles auf einmal:

```bash
python -m spotsync all --root /pfad/zu/deiner/musik --out data
```

### Pfade auf den Server umbiegen

Wenn du lokal scannst, die Playlists aber auf dem Server mit anderem Mount-Pfad
laufen sollen:

```bash
python -m spotsync playlists --matches data/matches.json --out data/playlists \
  --base-path /srv/music          # setzt Dateinamen neu auf /srv/music/<datei>
# oder relative Pfade:
python -m spotsync playlists --matches data/matches.json --out data/playlists \
  --relative-to /srv/music
```

## Abgleich-Logik (Reihenfolge, stärkste zuerst)

| Methode      | Grundlage                                             | Score |
|--------------|-------------------------------------------------------|-------|
| `isrc`       | Gleiche ISRC auf beiden Seiten                        | 1.00  |
| `exact`      | Identischer strikter Artist+Titel-Schlüssel           | 0.95  |
| `normalized` | Schlüssel ohne Remaster/Live/Klammer-Zusätze          | 0.88  |
| `fuzzy`      | Ähnlichkeit ≥ Schwelle, dauer-geprüft                 | 0.82+ |
| `none`       | Keine Zuordnung → kommt in den Lücken-Report          | —     |

Schwellen anpassbar: `--fuzzy-threshold`, `--duration-tolerance`.

## Datenablage

- `data/spotify/playlists.json`, `data/spotify/saved.json` — Export.
- `data/library.json` — Bibliotheks-Index.
- `data/matches.json` — Abgleich inkl. Methode/Score pro Titel.
- `data/playlists/*.m3u8` — lokale Playlists.
- `data/reports/gaps.csv|json`, `data/reports/duplicates.json`.

Token-Cache liegt unter `~/.config/spotsync/token.json` (0600). Überschreibbar
mit `SPOTSYNC_HOME`.

## Status

v0: lauffähige End-to-End-Pipeline. Kern (Normalisierung, Matching, Playlist-/
Report-Ausgabe) ist mit Smoke-Tests abgedeckt; die Spotify-Export-Pfade
erfordern eine echte App-Autorisierung und sind noch nicht gegen die Live-API
verifiziert (siehe `tests/`).
