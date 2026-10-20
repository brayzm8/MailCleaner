"""
ki.py - Verbindung zum lokalen Sprachmodell (Ollama).

Ollama läuft auf deinem Mac im Hintergrund (Lama-Symbol in der Menüleiste) und nimmt Fragen
unter http://localhost:11434 entgegen. Nichts davon geht ins Internet.

Ausprobieren:
  python src/ki.py "Wie heißt die Hauptstadt von Frankreich?"

In anderen Dateien benutzen:
  from ki import frage
  antwort = frage("Antworte nur mit OK")
"""
import json
import sys
import urllib.error
import urllib.request

OLLAMA_ADRESSE = "http://localhost:11434/api/generate"
MODELL = "qwen3:8b"


def frage(text, als_json=False, modell=MODELL, schema=None):
    """
    Schickt eine Frage an das lokale Modell und gibt die Antwort als Text zurück.
      als_json = True: das Modell muss gültiges JSON antworten (praktisch, wenn ein Programm die Antwort auswertet)
      schema   = feste Form der Antwort (JSON-Schema). Ollama ZWINGT das Modell dann in diese Form,
                 z. B. dass ein Feld nur bestimmte Werte haben darf.
    """
    anfrage = {
        "model": modell,
        "prompt": text,
        "stream": False,   # die ganze Antwort auf einmal statt Wort für Wort
        "think": False,    # kein "Nachdenk-Modus": sonst dauert jede Antwort 5 Sekunden statt Bruchteilen
    }
    if schema is not None:
        anfrage["format"] = schema
    elif als_json:
        anfrage["format"] = "json"

    daten = json.dumps(anfrage).encode("utf-8")
    request = urllib.request.Request(OLLAMA_ADRESSE, data=daten, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as antwort:
            ergebnis = json.loads(antwort.read().decode("utf-8"))
    except urllib.error.HTTPError as fehler:
        # Ollama läuft, hat aber einen Fehler gemeldet (z. B. Modell nicht installiert).
        try:
            meldung = json.loads(fehler.read().decode("utf-8")).get("error", str(fehler))
        except ValueError:
            meldung = str(fehler)
        raise SystemExit("Fehler von Ollama: " + meldung)
    except urllib.error.URLError:
        raise SystemExit("Ollama antwortet nicht. Läuft die Ollama-App (Lama-Symbol in der Menüleiste)?")

    if "error" in ergebnis:
        raise SystemExit("Fehler von Ollama: " + ergebnis["error"])
    return ergebnis["response"].strip()


# Dieser Block läuft nur, wenn du die Datei direkt startest.
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Aufruf: python src/ki.py "Deine Frage"')
    else:
        print(frage(sys.argv[1]))
