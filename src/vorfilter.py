"""
vorfilter.py - Schritt 1 der Aufräumhilfe: mögliche Löschkandidaten heraussuchen.

  python src/vorfilter.py           nimmt die Mails aus der Gmail-API   (reports/cache.jsonl)
  python src/vorfilter.py --local   nimmt die Mails aus Apple Mail      (reports/local_cache.jsonl)

Liest NUR den jeweiligen Zwischenspeicher. Keine Verbindung zu Gmail, dauert Sekunden,
und kann auch laufen, während main.py noch Mails abruft (dann einfach später noch einmal starten).

Ablauf:
  1. Kandidat ist eine Mail, wenn sie als Werbung, Social Media oder Spam einsortiert ist
     ODER ein Kennzeichen von Massenmails hat (Abmelde-Link, Verteilerliste). Damit fallen auch
     Newsletter auf, die Gmail/Apple fälschlich unter "Allgemein" einsortiert haben.
  2. Geschütztes fliegt sofort raus: von dir gesendet, Entwürfe, mit Stern, von deiner eigenen Adresse.
  3. Gruppieren nach Absender und darin nach Betreff-Muster.

Ergebnis (bei --local mit "local_" davor):
  reports/vorfilter.json          für die KI-Prüfung (Schritt 2): pro Absender seine verschiedenen Betreffs
  reports/vorfilter_absender.csv  Übersicht pro Absender zum Ansehen in Excel/Numbers

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

# Kennzeichen von Massenmails (Feld in der Mail -> lesbarer Name). Persönliche Mails haben sie praktisch nie.
MASSENMAIL_KENNZEICHEN = {
    "abmelde_header": "Abmelde-Link",
    "liste_header": "Verteilerliste",
}

# Diese Gmail-Kategorien kommen in den Vorfilter (Label -> lesbarer Name).
VORFILTER_KATEGORIEN = {
    "CATEGORY_PROMOTIONS": "Werbung",
    "CATEGORY_SOCIAL": "Social Media",
    "SPAM": "Spam",
}

# Mails mit diesen Labels werden NIE Kandidat (Label -> Grund).
SCHUTZ_LABELS = {
    "SENT": "von dir gesendet",
    "DRAFT": "Entwurf",
    "STARRED": "mit Stern markiert",
}

# Bei diesen Anbietern sagt die Domain nichts über den Absender aus (gmail.com ist keine Firma),
# deshalb gruppieren wir dort nach der ganzen Adresse.
FREEMAIL = ["gmail.com", "googlemail.com", "gmx.de", "gmx.net", "web.de", "outlook.com",
            "hotmail.com", "yahoo.com", "icloud.com", "t-online.de", "freenet.de"]


def absender_gruppe(adresse):
    """news@em.myprotein.com -> myprotein.com, freund@web.de -> freund@web.de"""
    if "@" not in adresse:
        return adresse or "unbekannt"
    domain = adresse.split("@")[-1]
    if domain in FREEMAIL:
        return adresse
    teile = domain.split(".")
    # Endungen wie .co.uk haben zwei Teile; dann nehmen wir drei Teile statt zwei.
    if len(teile) >= 3 and len(teile[-1]) == 2 and teile[-2] in ["co", "com", "org", "net", "ac", "gov"]:
        return ".".join(teile[-3:])
    return ".".join(teile[-2:])


def betreff_muster(betreff):
    """'Deine Bestellung 4711 vom 12.03.' -> 'deine bestellung # vom #.#.'
    So fallen Betreffs, die sich nur in Zahlen unterscheiden, zu einem Muster zusammen."""
    text = re.sub(r"\d+", "#", betreff.lower())
    return " ".join(text.split())  # mehrfache Leerzeichen zu einem


def nach_anzahl(eintrag):
    """Sortierhilfe: größte zuerst."""
    return eintrag["anzahl"]


def main():
    parser = argparse.ArgumentParser(description="Vorfilter: mögliche Löschkandidaten heraussuchen")
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
        konten = sorted(set(m.get("konto", "unbekannt") for m in mails))
        print("Konto: " + ", ".join(konten))

    # Deine eigene(n) Adresse(n): alle, von denen Mails mit dem Label "gesendet" stammen.
    eigene_adressen = set()
    for mail in mails:
        if "SENT" in mail["labels"]:
            eigene_adressen.add(mail["absender"])

    absender = {}       # Absender-Gruppe -> gesammelte Angaben
    ausgenommen = {}    # Schutzgrund -> Anzahl
    nicht_im_filter = 0

    for mail in mails:
        labels = mail["labels"]
        if "TRASH" in labels:
            continue  # liegt schon im Papierkorb

        # 1. Gehört die Mail in eine der Vorfilter-Kategorien oder hat sie ein Massenmail-Kennzeichen?
        kategorien = []
        for label in VORFILTER_KATEGORIEN:
            if label in labels:
                kategorien.append(VORFILTER_KATEGORIEN[label])
        for feld in MASSENMAIL_KENNZEICHEN:
            if mail.get(feld):  # .get: ältere Einträge im Zwischenspeicher haben diese Felder noch nicht
                kategorien.append(MASSENMAIL_KENNZEICHEN[feld])
        if len(kategorien) == 0:
            nicht_im_filter += 1
            continue

        # 2. Geschützt? Dann raus, egal welche Kategorie.
        grund = None
        for label in SCHUTZ_LABELS:
            if label in labels:
                grund = SCHUTZ_LABELS[label]
        if mail["absender"] in eigene_adressen:
            grund = "von deiner eigenen Adresse"
        if grund is not None:
            ausgenommen[grund] = ausgenommen.get(grund, 0) + 1
            continue

        # 3. Einsortieren: erst nach Absender, darin nach Betreff-Muster.
        gruppe = absender_gruppe(mail["absender"])
        if gruppe not in absender:
            absender[gruppe] = {"absender": gruppe, "anzahl": 0, "ungelesen": 0, "kategorien": {}, "betreffe": {}}
        eintrag = absender[gruppe]
        eintrag["anzahl"] += 1
        if mail["ungelesen"]:
            eintrag["ungelesen"] += 1
        for name in kategorien:
            eintrag["kategorien"][name] = eintrag["kategorien"].get(name, 0) + 1

        muster = betreff_muster(mail["betreff"])
        if muster not in eintrag["betreffe"]:
            eintrag["betreffe"][muster] = {"muster": muster, "beispiel": mail["betreff"], "anzahl": 0, "ids": []}
        eintrag["betreffe"][muster]["anzahl"] += 1
        eintrag["betreffe"][muster]["ids"].append(mail["id"])  # gebraucht, falls später gelöscht wird

    # Sortieren: größte Absender zuerst, darin die häufigsten Betreffs zuerst.
    liste = list(absender.values())
    liste.sort(key=nach_anzahl, reverse=True)
    for eintrag in liste:
        betreffe = list(eintrag["betreffe"].values())
        betreffe.sort(key=nach_anzahl, reverse=True)
        eintrag["betreffe"] = betreffe

    kandidaten = sum(e["anzahl"] for e in liste)
    verschiedene_betreffe = sum(len(e["betreffe"]) for e in liste)

    # Datei für die KI (Schritt 2)
    json_pfad.parent.mkdir(exist_ok=True)
    json_pfad.write_text(json.dumps({
        "erstellt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "mails_im_zwischenspeicher": len(mails),
        "kandidaten": kandidaten,
        "absender": liste,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # Übersicht für dich (Excel/Numbers)
    with open(csv_pfad, "w", newline="", encoding="utf-8-sig") as datei:
        schreiber = csv.writer(datei)
        schreiber.writerow(["absender", "mails", "ungelesen", "kategorien", "verschiedene_betreffe", "beispiele"])
        for e in liste:
            kategorien_text = ", ".join(name + " " + str(anzahl) for name, anzahl in e["kategorien"].items())
            beispiele = " | ".join(b["beispiel"] for b in e["betreffe"][:3])
            schreiber.writerow([e["absender"], e["anzahl"], e["ungelesen"], kategorien_text, len(e["betreffe"]), beispiele])

    # Zusammenfassung im Terminal
    hinweis = "" if args.local else " (läuft der Abruf noch, später erneut starten)"
    print(str(len(mails)) + " Mails im Zwischenspeicher" + hinweis + ".")
    print("Vorfilter (Werbung, Social Media, Spam, Abmelde-Link, Verteilerliste): " + str(kandidaten) + " Mails von " + str(len(liste))
          + " Absendern, " + str(verschiedene_betreffe) + " verschiedene Betreffs")
    # Welcher Grund hat wie viele Mails ausgewählt? (Eine Mail kann mehrere Gründe haben.)
    nach_grund = {}
    for e in liste:
        for name, anzahl in e["kategorien"].items():
            nach_grund[name] = nach_grund.get(name, 0) + anzahl
    print("  nach Grund: " + ", ".join(name + " " + str(anzahl) for name, anzahl in nach_grund.items()))
    if len(ausgenommen) > 0:
        teile = [grund + ": " + str(anzahl) for grund, anzahl in ausgenommen.items()]
        print("Geschützt und ausgenommen: " + ", ".join(teile))
    print("Nicht im Vorfilter (werden nicht angefasst): " + str(nicht_im_filter))

    print("\nDie 25 größten Absender im Vorfilter:")
    print("Absender".ljust(32) + "Mails".rjust(7) + "Ungel.".rjust(8) + "Betreffs".rjust(10))
    for e in liste[:25]:
        print(e["absender"][:31].ljust(32) + str(e["anzahl"]).rjust(7) + str(e["ungelesen"]).rjust(8)
              + str(len(e["betreffe"])).rjust(10))

    print("\nFür die KI: " + str(json_pfad))
    print("Übersicht:  " + str(csv_pfad))


if __name__ == "__main__":
    main()
