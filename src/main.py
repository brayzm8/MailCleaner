"""
main.py - der Startpunkt. Nur diese Datei rufst du selbst auf.

  python src/main.py            holt ALLE Mails über die Gmail-API (Google-Konto aus token.json)
  python src/main.py --local    liest ALLE Mails aus Apple Mail auf diesem Mac (in Sekunden, ohne Internet)
  python src/main.py --local --konto ich@gmail.com    ... wenn in Apple Mail mehrere Konten sind

Beide Quellen haben eigene Dateien in reports/, sie vermischen sich nie:
  Gmail-API:   cache.jsonl        gescheitert.json        postfach.csv
  Apple Mail:  local_cache.jsonl  local_gescheitert.json  local_postfach.csv
(Apple Mail kann mit einem anderen Google-Konto verbunden sein als die API!)

Der Ablauf bei der Gmail-API:
  1. auth.py  -> bei Gmail anmelden
  2. scan.py  -> alle Mail-IDs holen, prüfen welche schon im Zwischenspeicher sind,
                 nur die fehlenden abrufen (Absender, Betreff, Datum, Labels)
  3. hier     -> alles als Tabelle in reports/postfach.csv speichern (öffnest du mit Excel/Numbers)
Bricht das Programm ab (z. B. Strg+C), bleibt alles bisher Abgerufene erhalten.
Beim nächsten Start geht es dort weiter, wo es aufgehört hat.

Der Ablauf bei --local:
  1. lokal.py -> die Datenbank von Apple Mail lesen (dauert Sekunden, daher jedes Mal komplett)
  2. hier     -> als Tabelle in reports/local_postfach.csv speichern

Dieses Programm liest nur. Es löscht oder verändert nichts.
"""
import argparse
import csv
import time
from urllib.parse import quote

from auth import PROJEKTORDNER, gmail_verbinden
from lokal import (LOCAL_CACHE_DATEI, LOCAL_GESCHEITERT_DATEI, LOCAL_POSTFACH_DATEI, konten_auflisten,
                   konto_waehlen, lokal_lesen)
from scan import (CACHE_DATEI, GESCHEITERT_DATEI, cache_anhaengen, cache_laden, cache_neu_schreiben,
                  gescheitert_laden, gescheitert_speichern, mail_ids_holen, mails_abrufen, zeit_text)

AUSGABE_DATEI = PROJEKTORDNER / "reports" / "postfach.csv"


def kuerzen(text, breite):
    """Kürzt zu lange Texte für die Vorschau und hängt … an."""
    if len(text) <= breite:
        return text
    return text[:breite - 1] + "…"


def link_zur_mail(mail):
    """Link, der die Mail öffnet: bei Google in Gmail, bei Apple Mail in der Mail-App."""
    if mail.get("quelle") == "apple_mail":
        if mail.get("message_id_header"):
            return "message://" + quote("<" + mail["message_id_header"].strip("<>") + ">")
        return ""
    return "https://mail.google.com/mail/u/0/#all/" + mail["id"]


def csv_schreiben(mails, pfad):
    """Schreibt eine Zeile pro Mail in eine CSV-Datei."""
    pfad.parent.mkdir(exist_ok=True)  # Ordner reports/ anlegen, falls er fehlt
    # utf-8-sig, damit Excel die Umlaute richtig anzeigt.
    with open(pfad, "w", newline="", encoding="utf-8-sig") as datei:
        schreiber = csv.writer(datei)
        schreiber.writerow(["datum", "absender", "absender_name", "betreff", "ungelesen", "labels", "link"])
        for mail in mails:
            schreiber.writerow([
                mail["datum"],
                mail["absender"],
                mail["absender_name"],
                mail["betreff"],
                "ja" if mail["ungelesen"] else "nein",
                " ".join(mail["labels"]),
                link_zur_mail(mail),
            ])


def vorschau_zeigen(mails, pfad):
    """Zeigt die neuesten 15 Mails und wo die ganze Liste liegt."""
    print("\n" + str(len(mails)) + " Mails gelesen. Die neuesten 15:")
    for mail in mails[:15]:
        print("  " + mail["datum"] + "  " + kuerzen(mail["absender"], 32).ljust(32) + "  " + kuerzen(mail["betreff"], 50))
    print("\nGanze Liste: " + str(pfad))


def nach_datum(mail):
    """Sortierhilfe: nach Datum."""
    return mail["datum"]


def lokal_ausfuehren(auswahl):
    """Liest alle Mails EINES Kontos aus Apple Mail und speichert sie in den local_-Dateien.
    auswahl = Adresse oder Nummer des Kontos (nur nötig, wenn in Apple Mail mehrere Konten sind)."""
    konto = konto_waehlen(konten_auflisten(), auswahl)
    if konto is None:
        return
    print("Quelle: Apple Mail auf diesem Mac (ohne Google-API), Konto: " + konto["adresse"] + "\n")

    # Die Fehlerliste vom letzten Mal: Da die lokale Quelle jedes Mal ALLES neu liest,
    # werden diese Mails automatisch erneut versucht.
    frueher_gescheitert = gescheitert_laden(LOCAL_GESCHEITERT_DATEI)
    if len(frueher_gescheitert) > 0:
        print("Beim letzten Mal gescheitert: " + str(len(frueher_gescheitert)) + " (werden erneut versucht)")

    start = time.time()
    mails, fehlerliste = lokal_lesen(konto)

    # Zwischenspeicher komplett neu schreiben: so ist immer der aktuelle Stand von Apple Mail drin.
    cache_neu_schreiben(mails, LOCAL_CACHE_DATEI)
    gescheitert_speichern(fehlerliste, LOCAL_GESCHEITERT_DATEI)  # leer = Datei wird gelöscht

    print("Gelesen: " + str(len(mails)) + " Mails in " + zeit_text(time.time() - start))
    if len(fehlerliste) > 0:
        print("Warnung: " + str(len(fehlerliste)) + " Einträge ließen sich nicht lesen. Gründe: " + str(LOCAL_GESCHEITERT_DATEI))
    elif len(frueher_gescheitert) > 0:
        print("Alle beim letzten Mal gescheiterten Einträge sind jetzt erledigt.")

    mails.sort(key=nach_datum, reverse=True)  # neueste zuerst
    csv_schreiben(mails, LOCAL_POSTFACH_DATEI)
    vorschau_zeigen(mails, LOCAL_POSTFACH_DATEI)


def google_ausfuehren():
    """Holt alle Mails über die Gmail-API (mit Zwischenspeicher, Fehlerliste und Minutenbudget)."""
    # 1. Anmelden und Konto anzeigen
    gmail = gmail_verbinden()
    profil = gmail.users().getProfile(userId="me").execute()
    print("Quelle: Gmail-API")
    print("Konto: " + profil["emailAddress"])
    print("Mails gesamt laut Gmail: " + str(profil["messagesTotal"]))

    # 2. Alle IDs holen und mit dem Zwischenspeicher abgleichen
    print("\nLese ALLE Mails (nur Metadaten, nur lesend) ...")
    ids = mail_ids_holen(gmail)
    gespeichert = cache_laden()
    fehlende_ids = [mail_id for mail_id in ids if mail_id not in gespeichert]

    # Mails, die beim letzten Mal gescheitert sind, kommen zuerst dran.
    fehlende_menge = set(fehlende_ids)
    frueher_gescheitert = [mail_id for mail_id in gescheitert_laden() if mail_id in fehlende_menge]
    frueher_menge = set(frueher_gescheitert)
    fehlende_ids = frueher_gescheitert + [mail_id for mail_id in fehlende_ids if mail_id not in frueher_menge]

    print(str(len(ids)) + " Mails im Postfach, davon schon gespeichert: "
          + str(len(ids) - len(fehlende_ids)) + ", noch abzurufen: " + str(len(fehlende_ids)))
    if len(frueher_gescheitert) > 0:
        print("Davon beim letzten Mal gescheitert (werden zuerst versucht): " + str(len(frueher_gescheitert)))

    # 3. Nur die fehlenden Mails abrufen. Jedes Paket wird sofort in den Zwischenspeicher geschrieben.
    if len(fehlende_ids) == 0:
        gescheitert_speichern([])  # nichts mehr offen: eine alte, überholte Liste wird gelöscht
    else:
        start = time.time()
        try:
            neue, nicht_geholt = mails_abrufen(gmail, fehlende_ids, cache_anhaengen)
        except KeyboardInterrupt:
            print("\n\nAbgebrochen nach " + zeit_text(time.time() - start) + ". Alles bisher Abgerufene ist gespeichert in " + str(CACHE_DATEI))
            print("Starte das Programm erneut, dann geht es dort weiter.")
            return
        gespeichert.update(neue)

        # Zusammenfassung: wie viele Mails in welcher Zeit abgerufen wurden
        dauer = time.time() - start
        print("Abruf beendet: " + str(len(neue)) + " Mails in " + zeit_text(dauer)
              + " (" + str(int(len(neue) / max(dauer, 1) * 60)) + " pro Minute)")
        if len(nicht_geholt) > 0:
            print("Warnung: " + str(len(nicht_geholt)) + " Mails konnten nicht abgerufen werden."
                  " IDs und Fehlergründe stehen in " + str(GESCHEITERT_DATEI) + ". Ein erneuter Start versucht sie zuerst.")

    # 4. Liste in der Reihenfolge des Postfachs (neueste zuerst) zusammenstellen
    mails = []
    for mail_id in ids:
        if mail_id in gespeichert:
            mails.append(gespeichert[mail_id])

    # 5. Speichern und eine kurze Vorschau zeigen
    csv_schreiben(mails, AUSGABE_DATEI)
    vorschau_zeigen(mails, AUSGABE_DATEI)


def main():
    parser = argparse.ArgumentParser(description="Gmail-Aufräumhilfe (nur lesend)")
    parser.add_argument("-local", "--local", action="store_true",
                        help="Mails aus Apple Mail auf diesem Mac lesen statt über die Gmail-API")
    parser.add_argument("--konto", help="bei --local: welches Konto aus Apple Mail (Adresse oder Nummer)")
    args = parser.parse_args()

    if args.local:
        lokal_ausfuehren(args.konto)
    else:
        google_ausfuehren()


# Dieser Block läuft nur, wenn du die Datei direkt startest.
if __name__ == "__main__":
    main()
