"""
vorfilter.py - Schritt 1 der Aufräumhilfe: alle Mails sortieren und gruppieren.

  python src/vorfilter.py           nimmt die Mails aus der Gmail-API   (reports/cache.jsonl)
  python src/vorfilter.py --local   nimmt die Mails aus Apple Mail      (reports/local_cache.jsonl)

Liest NUR den jeweiligen Zwischenspeicher. Keine Verbindung zu Gmail, dauert Sekunden.

Ablauf:
  1. ALLE Mails kommen in Frage. (Nur nach Kennzeichen wie dem Abmelde-Link zu filtern reicht nicht:
     AliExpress zum Beispiel verschickt Werbung ganz ohne solche Kennzeichen.)
  2. Geschütztes fliegt sofort raus: von dir gesendet, Entwürfe, mit Stern, von deiner eigenen Adresse,
     und was schon im Papierkorb liegt.
  3. Gruppieren nach voller Absender-Adresse (transaction@... und promotion@... vom selben Shop
     sind verschiedene Gruppen) und darin nach Betreff-Muster.

Ergebnis (bei --local mit "local_" davor):
  reports/vorfilter.json          für die Art-Erkennung (Schritt 2)
  reports/vorfilter_absender.csv  Übersicht pro Absender-Adresse zum Ansehen in Excel/Numbers

Es wird nichts gelöscht.
"""
import argparse
import csv
import json
import re
from datetime import datetime

from auth import PROJEKTORDNER
from lokal import LOCAL_CACHE_DATEI
from scan import cache_laden

JSON_DATEI = PROJEKTORDNER / "reports" / "vorfilter.json"
CSV_DATEI = PROJEKTORDNER / "reports" / "vorfilter_absender.csv"
LOCAL_JSON_DATEI = PROJEKTORDNER / "reports" / "local_vorfilter.json"
LOCAL_CSV_DATEI = PROJEKTORDNER / "reports" / "local_vorfilter_absender.csv"

# Mails mit diesen Labels werden NIE angefasst (Label -> Grund).
SCHUTZ_LABELS = {
    "SENT": "von dir gesendet",
    "DRAFT": "Entwurf",
    "STARRED": "mit Stern markiert",
}

# Einordnung durch Gmail bzw. Apple (Label -> lesbarer Name). Nur zur Information für die KI.
KATEGORIEN = {
    "CATEGORY_PROMOTIONS": "Werbung",
    "CATEGORY_SOCIAL": "Social Media",
    "CATEGORY_UPDATES": "Benachrichtigungen",
    "CATEGORY_TRANSAKTIONEN": "Transaktionen",
    "CATEGORY_PERSONAL": "Allgemein",
    "SPAM": "Spam",
}


def betreff_muster(betreff):
    """'Deine Bestellung 4711 vom 12.03.' -> 'deine bestellung # vom #.#.'
    So fallen Betreffs, die sich nur in Zahlen unterscheiden, zu einem Muster zusammen."""
    text = re.sub(r"\d+", "#", betreff.lower())
    return " ".join(text.split())  # mehrfache Leerzeichen zu einem


def nach_anzahl(eintrag):
    """Sortierhilfe: größte zuerst."""
    return eintrag["anzahl"]


def main():
    parser = argparse.ArgumentParser(description="Vorfilter: alle Mails nach Absender und Betreff gruppieren")
    parser.add_argument("-local", "--local", action="store_true", help="Mails aus Apple Mail statt aus der Gmail-API")
    args = parser.parse_args()

    # Je nach Quelle andere Dateien, damit sich nichts vermischt.
    if args.local:
        cache_pfad, json_pfad, csv_pfad = LOCAL_CACHE_DATEI, LOCAL_JSON_DATEI, LOCAL_CSV_DATEI
        print("Quelle: Apple Mail (" + str(cache_pfad) + ")")
    else:
        cache_pfad, json_pfad, csv_pfad = None, JSON_DATEI, CSV_DATEI  # None = Google-Zwischenspeicher
        print("Quelle: Gmail-API (reports/cache.jsonl)")

    mails = list(cache_laden(cache_pfad).values())
    if len(mails) == 0:
        print("Der Zwischenspeicher ist leer. Zuerst: python src/main.py" + (" --local" if args.local else ""))
        return
    if args.local:
        print("Konto: " + ", ".join(sorted(set(m.get("konto", "unbekannt") for m in mails))))

    # Deine eigene(n) Adresse(n): alle, von denen Mails mit dem Label "gesendet" stammen.
    eigene_adressen = set()
    for mail in mails:
        if "SENT" in mail["labels"]:
            eigene_adressen.add(mail["absender"])

    absender = {}       # Absender-Adresse -> gesammelte Angaben
    ausgenommen = {}    # Schutzgrund -> Anzahl

    for mail in mails:
        labels = mail["labels"]

        # 1. Geschützt oder schon im Papierkorb? Dann raus.
        grund = None
        if "TRASH" in labels:
            grund = "liegt schon im Papierkorb"
        for label in SCHUTZ_LABELS:
            if label in labels:
                grund = SCHUTZ_LABELS[label]
        if mail["absender"] in eigene_adressen:
            grund = "von deiner eigenen Adresse"
        if grund is not None:
            ausgenommen[grund] = ausgenommen.get(grund, 0) + 1
            continue

        # 2. Einsortieren: erst nach voller Absender-Adresse, darin nach Betreff-Muster.
        adresse = mail["absender"] or "unbekannt"
        if adresse not in absender:
            absender[adresse] = {"absender": adresse, "domain": adresse.split("@")[-1], "anzahl": 0,
                                 "ungelesen": 0, "massenmail": 0, "kategorien": {}, "betreffe": {}}
        eintrag = absender[adresse]
        eintrag["anzahl"] += 1
        if mail["ungelesen"]:
            eintrag["ungelesen"] += 1
        # .get: ältere Einträge im Zwischenspeicher haben diese Felder vielleicht noch nicht
        if mail.get("abmelde_header") or mail.get("liste_header"):
            eintrag["massenmail"] += 1
        for label in KATEGORIEN:
            if label in labels:
                name = KATEGORIEN[label]
                eintrag["kategorien"][name] = eintrag["kategorien"].get(name, 0) + 1

        muster = betreff_muster(mail["betreff"])
        if muster not in eintrag["betreffe"]:
            eintrag["betreffe"][muster] = {"muster": muster, "beispiel": mail["betreff"], "anzahl": 0,
                                           "ids": [], "daten": [], "message_id": "", "neuestes_datum": ""}
        gruppe = eintrag["betreffe"][muster]
        gruppe["anzahl"] += 1
        gruppe["ids"].append(mail["id"])        # gebraucht, falls später gelöscht wird
        gruppe["daten"].append(mail["datum"])   # gebraucht für Altersregeln ("älter als 6 Monate")
        # Die neueste Mail der Gruppe dient als Beispiel, deren Text liest später die KI.
        if mail["datum"] >= gruppe["neuestes_datum"]:
            gruppe["neuestes_datum"] = mail["datum"]
            gruppe["beispiel"] = mail["betreff"]
            gruppe["message_id"] = mail.get("message_id_header", "")

    # Sortieren: größte Absender zuerst, darin die häufigsten Betreffs zuerst.
    liste = list(absender.values())
    liste.sort(key=nach_anzahl, reverse=True)
    for eintrag in liste:
        betreffe = list(eintrag["betreffe"].values())
        betreffe.sort(key=nach_anzahl, reverse=True)
        eintrag["betreffe"] = betreffe

    gruppiert = sum(e["anzahl"] for e in liste)
    verschiedene_betreffe = sum(len(e["betreffe"]) for e in liste)

    # Datei für die Art-Erkennung (Schritt 2)
    json_pfad.parent.mkdir(exist_ok=True)
    json_pfad.write_text(json.dumps({
        "erstellt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "lokal": args.local,
        "mails_im_zwischenspeicher": len(mails),
        "gruppiert": gruppiert,
        "absender": liste,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # Übersicht für dich (Excel/Numbers)
    with open(csv_pfad, "w", newline="", encoding="utf-8-sig") as datei:
        schreiber = csv.writer(datei)
        schreiber.writerow(["absender", "mails", "ungelesen", "mit_massenmail_kennzeichen", "kategorien",
                            "verschiedene_betreffe", "beispiele"])
        for e in liste:
            kategorien_text = ", ".join(name + " " + str(anzahl) for name, anzahl in e["kategorien"].items())
            beispiele = " | ".join(b["beispiel"] for b in e["betreffe"][:3])
            schreiber.writerow([e["absender"], e["anzahl"], e["ungelesen"], e["massenmail"], kategorien_text,
                                len(e["betreffe"]), beispiele])

    # Zusammenfassung im Terminal
    hinweis = "" if args.local else " (läuft der Abruf noch, später erneut starten)"
    print(str(len(mails)) + " Mails im Zwischenspeicher" + hinweis + ".")
    print("Gruppiert: " + str(gruppiert) + " Mails von " + str(len(liste)) + " Absender-Adressen, "
          + str(verschiedene_betreffe) + " verschiedene Betreff-Muster")
    print("  davon mit Massenmail-Kennzeichen (Abmelde-Link/Verteilerliste): " + str(sum(e["massenmail"] for e in liste)))
    if len(ausgenommen) > 0:
        teile = [grund + ": " + str(anzahl) for grund, anzahl in ausgenommen.items()]
        print("Geschützt und ausgenommen: " + ", ".join(teile))

    print("\nDie 25 größten Absender-Adressen:")
    print("Absender".ljust(44) + "Mails".rjust(7) + "Ungel.".rjust(8) + "Betreffs".rjust(10))
    for e in liste[:25]:
        print(e["absender"][:43].ljust(44) + str(e["anzahl"]).rjust(7) + str(e["ungelesen"]).rjust(8)
              + str(len(e["betreffe"])).rjust(10))

    print("\nFür die Art-Erkennung: " + str(json_pfad))
    print("Übersicht:             " + str(csv_pfad))


if __name__ == "__main__":
    main()
