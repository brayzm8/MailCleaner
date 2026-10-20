"""
report.py - Ergebnisse anzeigen und speichern.

Wird von main.py aufgerufen. Hier wird nichts bei Gmail abgefragt, nur
ausgegeben: die Tabelle im Terminal und zwei CSV-Dateien im Ordner reports/.
CSV-Dateien kannst du in Excel oder Numbers öffnen.
"""
import csv

from auth import PROJEKTORDNER
from classify import GESCHUETZT, KANDIDAT, PRUEFEN, UNSICHER

BERICHTSORDNER = PROJEKTORDNER / "reports"
MAILS_CSV = BERICHTSORDNER / "mails.csv"        # jede einzelne Mail
ABSENDER_CSV = BERICHTSORDNER / "absender.csv"  # eine Zeile pro Absender
TOEPFE = [KANDIDAT, PRUEFEN, GESCHUETZT, UNSICHER]


def kuerzen(text, breite):
    """Kürzt zu lange Texte für die Tabelle und hängt … an."""
    if len(text) <= breite:
        return text
    return text[:breite - 1] + "…"


def absender_tabelle(mails):
    """
    Zählt pro Absender: Mails gesamt, ungelesen und wie viele in jedem Topf liegen.
    Sortiert so, dass Absender mit den meisten Kandidaten oben stehen.
    """
    tabelle = {}
    for mail in mails:
        gruppe = mail["gruppe"]
        if gruppe not in tabelle:
            zeile = {"absender": gruppe, "gesamt": 0, "ungelesen": 0}
            for topf in TOEPFE:
                zeile[topf] = 0
            tabelle[gruppe] = zeile
        zeile = tabelle[gruppe]
        zeile["gesamt"] += 1
        if mail["ungelesen"]:
            zeile["ungelesen"] += 1
        zeile[mail["topf"]] += 1

    zeilen = list(tabelle.values())

    def sortierwert(zeile):
        return (zeile[KANDIDAT] + zeile[PRUEFEN], zeile["gesamt"])

    zeilen.sort(key=sortierwert, reverse=True)  # reverse=True: größte zuerst
    return zeilen


def csv_schreiben(mails):
    """Schreibt reports/mails.csv und reports/absender.csv."""
    BERICHTSORDNER.mkdir(exist_ok=True)

    # utf-8-sig, damit Excel die Umlaute richtig anzeigt.
    with open(MAILS_CSV, "w", newline="", encoding="utf-8-sig") as datei:
        schreiber = csv.writer(datei)
        schreiber.writerow(["datum", "gruppe", "absender", "betreff", "topf", "gruende", "ungelesen", "link"])
        for mail in mails:
            schreiber.writerow([
                mail["datum"],
                mail["gruppe"],
                mail["absender"],
                mail["betreff"],
                mail["topf"],
                " | ".join(mail["gruende"]),
                "ja" if mail["ungelesen"] else "nein",
                "https://mail.google.com/mail/u/0/#all/" + mail["id"],  # öffnet die Mail in Gmail
            ])

    with open(ABSENDER_CSV, "w", newline="", encoding="utf-8-sig") as datei:
        schreiber = csv.writer(datei)
        schreiber.writerow(["absender", "mails", "ungelesen"] + TOEPFE)
        for zeile in absender_tabelle(mails):
            schreiber.writerow([zeile["absender"], zeile["gesamt"], zeile["ungelesen"]] + [zeile[t] for t in TOEPFE])


def uebersicht_ausgeben(mails, wie_viele_absender):
    """Druckt die Zusammenfassung ins Terminal."""
    print("\n" + str(len(mails)) + " Mails eingeordnet:")
    for topf in TOEPFE:
        anzahl = len([m for m in mails if m["topf"] == topf])
        print("  " + topf.ljust(14) + str(anzahl).rjust(6))

    print("\nTop " + str(wie_viele_absender) + " Absender (zuerst die mit den meisten Kandidaten):")
    print("Absender".ljust(34) + "Mails".rjust(7) + "Ungel.".rjust(7) + "Kand.".rjust(7)
          + "Prüf.".rjust(7) + "Gesch.".rjust(7) + "Unsich.".rjust(8))
    for zeile in absender_tabelle(mails)[:wie_viele_absender]:
        print(kuerzen(zeile["absender"], 33).ljust(34)
              + str(zeile["gesamt"]).rjust(7) + str(zeile["ungelesen"]).rjust(7)
              + str(zeile[KANDIDAT]).rjust(7) + str(zeile[PRUEFEN]).rjust(7)
              + str(zeile[GESCHUETZT]).rjust(7) + str(zeile[UNSICHER]).rjust(8))

    # Beispiele aus dem Topf "Prüfen", weil dort die Regeln am ehesten nachjustiert werden müssen.
    zu_pruefen = [m for m in mails if m["topf"] == PRUEFEN][:10]
    if len(zu_pruefen) > 0:
        print("\nBeispiele im Topf 'Prüfen' (Hinweis auf Wichtiges UND auf Werbung):")
        for mail in zu_pruefen:
            print("  " + mail["datum"] + "  " + kuerzen(mail["gruppe"], 24).ljust(24) + "  " + kuerzen(mail["betreff"], 50))
            print("      " + " | ".join(mail["gruende"]))

    print("\nVollständige Listen: " + str(MAILS_CSV))
    print("                     " + str(ABSENDER_CSV))
    print("Einzelnen Absender ansehen:  python src/main.py zeige <absender>")


def absender_zeigen(suchtext, topf):
    """Zeigt alle Mails eines Absenders aus dem letzten Scan (liest die CSV, fragt Gmail nicht)."""
    if not MAILS_CSV.exists():
        print("Noch kein Bericht vorhanden. Zuerst: python src/main.py scan")
        return

    suchtext = suchtext.lower()
    treffer = []
    with open(MAILS_CSV, newline="", encoding="utf-8-sig") as datei:
        for zeile in csv.DictReader(datei):
            passt = suchtext in zeile["gruppe"].lower() or suchtext in zeile["absender"].lower()
            if passt and (topf is None or zeile["topf"].lower() == topf.lower()):
                treffer.append(zeile)

    if len(treffer) == 0:
        print("Keine passenden Mails gefunden.")
        return

    for zeile in treffer:
        print(zeile["datum"] + "  [" + zeile["topf"] + "]  " + kuerzen(zeile["betreff"], 70))
        print("    " + zeile["gruende"])
    print("\n" + str(len(treffer)) + " Mails.")
