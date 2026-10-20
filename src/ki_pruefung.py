"""
ki_pruefung.py - Schritt 2: Die KI bestimmt die ART jeder Mail-Gruppe, deine Regeln entscheiden.

  python src/ki_pruefung.py --local                  alle Gruppen aus reports/local_vorfilter.json
  python src/ki_pruefung.py --local --test 5         nur die 5 größten Absender-Adressen (zum Ausprobieren)
  python src/ki_pruefung.py --local --nur aliexpress nur Adressen, die "aliexpress" enthalten

Ablauf:
  1. Pro Gruppe (Absender-Adresse + Betreff-Muster) wird die neueste Mail als Beispiel genommen.
     Eindeutige Schutzwörter im Betreff (Rechnung, Kontoauszug ...) -> Art steht ohne KI fest.
  2. Sonst liest die KI Absender, Betreff und den Anfang des Mailtextes und MUSS eine Art aus
     einer festen Liste wählen (Werbung, Benachrichtigung, Transaktion, Sicherheit/Konto, Persönlich, Unklar).
  3. DEINE REGELN (siehe REGELN unten) entscheiden je nach Art und Alter: löschbar oder behalten.
  4. Sicherung: Schickt eine Adresse überwiegend wichtige Mails (z. B. transaction@ bei AliExpress),
     wird Werbung von ihr nicht gelöscht, sondern dir zum PRÜFEN vorgelegt. So kann ein Fehler
     der KI ("Paket wird zugestellt" = Werbung) keine wichtige Mail kosten.

Jede Art wird sofort gespeichert. Ein Abbruch kostet nichts, ein neuer Lauf prüft nur Neues.
Regeln kannst du jederzeit ändern und neu starten: die KI muss dafür nicht noch einmal laufen.

Ergebnis (bei --local mit "local_" davor), alles in reports/:
  ki_arten.json          die Art jeder Gruppe (Gedächtnis für spätere Läufe)
  entscheidungen.csv     jede Gruppe mit Art und Entscheidung, zum Durchsehen
  loeschvorschlag.csv    Übersicht pro Absender-Adresse
  loeschvorschlag.json   welche Mails genau gelöscht werden könnten (für den späteren Aufräum-Schritt)

Es wird nichts gelöscht.
"""
import argparse
import csv
import json
import re
import time
from datetime import date, datetime

from auth import PROJEKTORDNER
from ki import MODELL, frage
from lokal import LOCAL_CACHE_DATEI
from mailtext import index_bauen, text_holen
from scan import cache_laden, zeit_text

BERICHTE = PROJEKTORDNER / "reports"

# ---------------------------------------------------------------------------------------------
# DEINE REGELN: Art -> ab wie vielen Tagen Alter eine Mail löschbar ist. None = nie löschen.
# Ändern und neu starten genügt, die KI muss dafür nicht noch einmal laufen.
REGELN = {
    "Werbung": 0,             # immer löschbar
    "Benachrichtigung": 180,  # löschbar, wenn älter als ein halbes Jahr
    "Transaktion": None,      # nie
    "Sicherheit/Konto": None, # nie
    "Persönlich": None,       # nie
    "Unklar": None,           # nie: im Zweifel behalten
}

# Sicherung: Schickt eine Adresse mindestens diesen Anteil an wichtigen Mails (Arten unten),
# wird Werbung/Benachrichtigung von ihr nicht gelöscht, sondern zum Prüfen vorgelegt.
WICHTIGE_ARTEN = ["Transaktion", "Sicherheit/Konto", "Persönlich"]
SICHERUNG_ANTEIL = 0.3

# Plattformen, über die Käufer und Verkäufer schreiben. Deren Nachrichten dürfen alt werden wie
# Benachrichtigungen (Regel oben), auch wenn die KI sie für "Persönlich" hält.
# Sicherheitsmails dieser Plattformen bleiben davon unberührt (Art "Sicherheit/Konto").
PLATTFORM_NACHRICHTEN = ["kleinanzeigen.de", "ebay.de", "ebay.com", "shpock.com"]

# Warnsignale im Mailtext. Steht so etwas in einer Mail, die gelöscht werden soll,
# wird sie stattdessen zum PRÜFEN vorgelegt (falls die KI sich bei der Art geirrt hat).
WARNSIGNALE = re.compile(
    r"bestellnummer|bestell-nr|order (number|id|no)|sendungsnummer|tracking[- ]?(nummer|number)"
    r"|rechnungsnummer|rechnungsbetrag|gesamtbetrag|zahlungseingang|ihre zahlung|your payment"
    r"|konto (wurde )?gesperrt|ungewöhnliche anmeldung|neue anmeldung|new sign-?in"
    r"|passwort (wurde )?(geändert|zurückgesetzt)|password (was )?(changed|reset)"
    r"|verifizierungscode|bestätigungscode|sicherheitscode|verification code|\biban\b",
    re.IGNORECASE)
# ---------------------------------------------------------------------------------------------

ARTEN = list(REGELN.keys())

# Feste Antwortform: Ollama zwingt das Modell, genau eine dieser Arten zu nennen.
SCHEMA = {
    "type": "object",
    "properties": {"art": {"type": "string", "enum": ARTEN}},
    "required": ["art"],
}

ANWEISUNG = """Bestimme die ART dieser E-Mail. Wähle genau eine:
- Werbung: Angebote, Rabatte, Newsletter, Produktempfehlungen, Gewinnspiele, Einladungen zum Einkaufen.
- Benachrichtigung: automatische Hinweise ohne bleibenden Wert, z. B. Likes, neue Follower,
  Erinnerungen, Bitten um eine Bewertung, Neuigkeiten aus Apps oder Spielen.
- Transaktion: Rechnungen, Bestellungen, Bestellbestätigungen, Versand- und Paketstatus,
  Zahlungen, Rückerstattungen, Verträge, Kündigungen, Termine.
- Sicherheit/Konto: Sicherheitswarnungen, Anmeldungen, Passwörter, Bestätigungscodes,
  Änderungen am Konto oder an Zahlungsmethoden.
- Persönlich: von einem Menschen persönlich an den Empfänger geschrieben.
- Unklar: wenn du es nicht sicher sagen kannst.

Absender: {absender}
Betreff: {betreff}
Text: {text}"""

# Steht eines dieser Wörter am WORTANFANG im Betreff, steht die Art ohne KI fest.
# Nur eindeutige Begriffe, keine wie "tracking" (Fitness-Tracking-Uhr) oder "konto" ("Konto überziehen").
SCHUTZWOERTER = {
    "rechnung": "Transaktion", "invoice": "Transaktion", "quittung": "Transaktion", "receipt": "Transaktion",
    "kontoauszug": "Transaktion", "mahnung": "Transaktion", "lastschrift": "Transaktion",
    "zahlungseingang": "Transaktion", "zahlungsbestätigung": "Transaktion", "zahlungserinnerung": "Transaktion",
    "payment received": "Transaktion", "bestellbestätigung": "Transaktion", "auftragsbestätigung": "Transaktion",
    "order confirmation": "Transaktion", "versandbestätigung": "Transaktion", "sendungsverfolgung": "Transaktion",
    "gutschrift": "Transaktion", "rückerstattung": "Transaktion", "erstattung": "Transaktion",
    "refund": "Transaktion", "stornierung": "Transaktion", "kündigungsbestätigung": "Transaktion",
    "steuerbescheid": "Transaktion", "steuererklärung": "Transaktion",
    "sicherheitscode": "Sicherheit/Konto", "verifizierungscode": "Sicherheit/Konto",
    "bestätigungscode": "Sicherheit/Konto", "verification code": "Sicherheit/Konto",
    "security code": "Sicherheit/Konto", "passwort zurücksetzen": "Sicherheit/Konto",
    "reset your password": "Sicherheit/Konto",
}

# Sieht der Betreff nach Werbung aus, entscheidet die KI statt des Schutzworts.
WERBESIGNAL = re.compile(r"%|rabatt|angebot|\bsale\b|gutschein|\bdeal|gratis|kostenlos|\bspar|reduziert|aktion",
                         re.IGNORECASE)


def dateien(lokal):
    """Welche Dateien gehören zu welcher Quelle? Bei --local mit 'local_' davor, damit sich nichts vermischt."""
    vorsilbe = "local_" if lokal else ""
    return {
        "vorfilter": BERICHTE / (vorsilbe + "vorfilter.json"),
        "arten": BERICHTE / (vorsilbe + "ki_arten.json"),
        "entscheidungen": BERICHTE / (vorsilbe + "entscheidungen.csv"),
        "vorschlag_csv": BERICHTE / (vorsilbe + "loeschvorschlag.csv"),
        "vorschlag_json": BERICHTE / (vorsilbe + "loeschvorschlag.json"),
    }


def json_laden(pfad, standard):
    """Liest eine JSON-Datei. Gibt 'standard' zurück, wenn es sie nicht gibt oder sie unlesbar ist."""
    if not pfad.exists():
        return standard
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except ValueError:
        return standard


def json_speichern(pfad, daten):
    """Speichert JSON. Erst in eine Hilfsdatei, dann umbenennen: so ist die Datei nie halb fertig."""
    pfad.parent.mkdir(exist_ok=True)
    hilfsdatei = pfad.with_name(pfad.name + ".tmp")
    hilfsdatei.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")
    hilfsdatei.replace(pfad)


def schluessel(absender, muster):
    """Unter diesem Namen wird die Art einer Gruppe gespeichert: Absender-Adresse + Betreff-Muster."""
    return absender + " | " + muster


def schutzwort_art(betreff):
    """Gibt (Art, Schutzwort) zurück, wenn ein eindeutiges Schutzwort im Betreff steht, sonst (None, None)."""
    if WERBESIGNAL.search(betreff):
        return None, None
    for wort, art in SCHUTZWOERTER.items():
        if re.search(r"\b" + re.escape(wort), betreff, re.IGNORECASE):  # \b = Wortanfang
            return art, wort
    return None, None


def art_bestimmen(absender, betreff, text):
    """Fragt die KI nach der Art. Bei unbrauchbarer Antwort einmal neu fragen, sonst 'Unklar' (= behalten)."""
    anfrage = ANWEISUNG.format(absender=absender, betreff=betreff, text=text or "(kein Text verfügbar)")
    for versuch in range(2):
        try:
            art = json.loads(frage(anfrage, schema=SCHEMA)).get("art")
        except ValueError:
            art = None
        if art in ARTEN:
            return art
    return "Unklar"


def alter_in_tagen(datum_text, heute):
    """'2024-03-01' -> Anzahl Tage bis heute."""
    try:
        return (heute - datetime.strptime(datum_text, "%Y-%m-%d").date()).days
    except ValueError:
        return 0  # unbekanntes Datum: als ganz neu behandeln (= eher behalten)


def main():
    parser = argparse.ArgumentParser(description="Art-Erkennung der Mail-Gruppen mit lokaler KI und deinen Regeln")
    parser.add_argument("-local", "--local", action="store_true", help="Gruppen aus Apple Mail (local_vorfilter.json)")
    parser.add_argument("--test", type=int, help="nur die N größten Absender-Adressen prüfen")
    parser.add_argument("--nur", help="nur Absender-Adressen, die diesen Text enthalten (z. B. aliexpress)")
    parser.add_argument("--nur-regeln", action="store_true",
                        help="KI nicht laufen lassen, nur die Regeln auf die schon bestimmten Arten anwenden")
    args = parser.parse_args()

    pfade = dateien(args.local)
    vorfilter = json_laden(pfade["vorfilter"], None)
    if vorfilter is None:
        print("Keine Gruppen gefunden. Zuerst: python src/vorfilter.py" + (" --local" if args.local else ""))
        return

    absender_liste = vorfilter["absender"]
    if args.nur:
        absender_liste = [e for e in absender_liste if args.nur.lower() in e["absender"].lower()]
    if args.test:
        absender_liste = absender_liste[:args.test]
    arten = json_laden(pfade["arten"], {})  # Gedächtnis: welche Gruppen haben schon eine Art?

    # --- 1. Schutzwörter (ohne KI) und sammeln, was die KI noch bestimmen muss.
    offen = []  # Liste von (absender, gruppe)
    for eintrag in absender_liste:
        for gruppe in eintrag["betreffe"]:
            name = schluessel(eintrag["absender"], gruppe["muster"])
            # Schutzwort-Ergebnisse werden jedes Mal neu berechnet (kostet nichts),
            # KI-Ergebnisse aus früheren Läufen bleiben erhalten.
            if name in arten and arten[name]["quelle"] != "schutzwort":
                continue
            arten.pop(name, None)
            art, wort = schutzwort_art(gruppe["beispiel"])
            if art is not None:
                arten[name] = {"art": art, "quelle": "schutzwort", "hinweis": "Schutzwort '" + wort + "'"}
            else:
                offen.append((eintrag["absender"], gruppe))
    json_speichern(pfade["arten"], arten)

    # Mailtexte (nur bei Apple Mail): gebraucht von der KI UND von der Widerspruchsprüfung in Schritt 4.
    index = {}       # Message-ID -> Datei der Mail
    message_ids = {} # Mail-ID im Programm -> Message-ID
    if args.local:
        kennung = None  # Ordner in Apple Mail, der zum Konto im Zwischenspeicher gehört
        for mail_id, mail in cache_laden(LOCAL_CACHE_DATEI).items():
            message_ids[mail_id] = mail.get("message_id_header", "")
            kennung = kennung or mail.get("konto_kennung")
        if kennung:
            print("Suche die Mailtexte in Apple Mail ... (etwa eine halbe Minute)")
            index = index_bauen(kennung)
            print("  " + str(len(index)) + " Mails gefunden")
        else:
            print("Hinweis: Kein Mailtext verfügbar, bitte erst neu einlesen: python src/main.py --local --konto ...")
    else:
        print("Hinweis: Bei der Gmail-API wird nur der Betreff geprüft (der Mailtext ist nicht abgerufen).")

    # --- 2. Die KI bestimmt die Art der übrigen Gruppen, mit dem Text der neuesten Mail.
    print("Modell: " + MODELL + " | " + str(len(offen)) + " Gruppen für die KI")
    if len(offen) > 0 and args.nur_regeln:
        print("  --nur-regeln: KI wird übersprungen, diese Gruppen gelten als 'nicht geprüft' (= behalten)")
    elif len(offen) > 0:
        start = time.time()
        try:
            for nummer, (absender, gruppe) in enumerate(offen, start=1):
                text = text_holen(index, gruppe.get("message_id", "")) if index else ""
                art = art_bestimmen(absender, gruppe["beispiel"], text)
                arten[schluessel(absender, gruppe["muster"])] = {
                    "art": art, "quelle": "ki", "modell": MODELL, "mit_text": bool(text)}
                json_speichern(pfade["arten"], arten)

                vergangen = time.time() - start
                rest = vergangen / nummer * (len(offen) - nummer)
                print("\r  Gruppe " + str(nummer) + "/" + str(len(offen)) + " | Zeit " + zeit_text(vergangen)
                      + " | noch etwa " + zeit_text(rest) + " | " + absender[:35] + "      ", end="", flush=True)
        except KeyboardInterrupt:
            print("\n\nAbgebrochen. Alles bisher Bestimmte ist gespeichert, ein neuer Start macht weiter.")
            return
        print()

    # --- 3. Sicherung vorbereiten: Welcher Anteil der Mails jeder Adresse ist wichtig?
    wichtig_anteil = {}
    for eintrag in absender_liste:
        wichtig = 0
        for gruppe in eintrag["betreffe"]:
            urteil = arten.get(schluessel(eintrag["absender"], gruppe["muster"]))
            if urteil is not None and urteil["art"] in WICHTIGE_ARTEN:
                wichtig += gruppe["anzahl"]
        wichtig_anteil[eintrag["absender"]] = wichtig / max(eintrag["anzahl"], 1)

    # --- 4. Deine Regeln anwenden: jede einzelne Mail wird löschbar, prüfen oder behalten.
    heute = date.today()
    zeilen = []          # eine Zeile pro Gruppe für entscheidungen.csv
    pro_absender = []    # eine Zeile pro Adresse für loeschvorschlag.csv
    loeschbar_ids, pruefen_ids = [], []
    for eintrag in absender_liste:
        summe = {"löschbar": 0, "prüfen": 0, "behalten": 0}
        arten_zaehler = {}
        gesichert = wichtig_anteil[eintrag["absender"]] >= SICHERUNG_ANTEIL
        domain = eintrag.get("domain", eintrag["absender"].split("@")[-1])
        plattform = any(domain == p or domain.endswith("." + p) for p in PLATTFORM_NACHRICHTEN)
        for gruppe in eintrag["betreffe"]:
            urteil = arten.get(schluessel(eintrag["absender"], gruppe["muster"]))
            art = urteil["art"] if urteil else "nicht geprüft"
            arten_zaehler[art] = arten_zaehler.get(art, 0) + gruppe["anzahl"]
            # Nachrichten über eBay, Kleinanzeigen & Co. altern wie Benachrichtigungen.
            regel_art = "Benachrichtigung" if (plattform and art == "Persönlich") else art
            tage = REGELN.get(regel_art)  # None bei "nicht geprüft" und bei Arten, die nie gelöscht werden

            gruppe_summe = {"löschbar": 0, "prüfen": 0, "behalten": 0}
            erster_grund = ""  # warum Mails dieser Gruppe zum Prüfen vorgelegt werden
            for mail_id, datum in zip(gruppe["ids"], gruppe["daten"]):
                if tage is None or alter_in_tagen(datum, heute) < tage:
                    entscheidung = "behalten"
                else:
                    # Die Regel sagt "löschbar". Jetzt die Sicherungen: spricht etwas dagegen?
                    grund = None
                    if gesichert:
                        grund = "Adresse schickt zu " + str(int(wichtig_anteil[eintrag["absender"]] * 100)) + " % wichtige Mails"
                    elif args.local:
                        text = text_holen(index, message_ids.get(mail_id, ""), 3000)
                        if text == "":
                            grund = "kein Mailtext lesbar"  # ohne Text nie automatisch löschen
                        else:
                            treffer = WARNSIGNALE.search(text)
                            if treffer:
                                grund = "Text enthält '" + treffer.group(0) + "'"
                    entscheidung = "prüfen" if grund else "löschbar"
                    if grund and not erster_grund:
                        erster_grund = grund
                gruppe_summe[entscheidung] += 1
                if entscheidung == "löschbar":
                    loeschbar_ids.append(mail_id)
                elif entscheidung == "prüfen":
                    pruefen_ids.append(mail_id)
            for k in summe:
                summe[k] += gruppe_summe[k]

            if urteil is None:
                hinweis = "noch nicht geprüft"
            elif erster_grund:
                hinweis = erster_grund
            elif regel_art != art:
                hinweis = "Plattform-Nachricht, wie Benachrichtigung behandelt"
            else:
                hinweis = urteil.get("hinweis", "")
            zeilen.append([eintrag["absender"], gruppe["beispiel"], gruppe["anzahl"], art,
                           urteil["quelle"] if urteil else "-", gruppe_summe["löschbar"], gruppe_summe["prüfen"],
                           gruppe_summe["behalten"], hinweis])
        pro_absender.append([eintrag["absender"], eintrag["anzahl"], summe["löschbar"], summe["prüfen"], summe["behalten"],
                             ", ".join(a + " " + str(n) for a, n in sorted(arten_zaehler.items(), key=lambda x: -x[1]))])

    # --- 5. Ergebnisdateien schreiben
    with open(pfade["entscheidungen"], "w", newline="", encoding="utf-8-sig") as datei:
        schreiber = csv.writer(datei)
        schreiber.writerow(["absender", "betreff", "mails", "art", "quelle", "löschbar", "prüfen", "behalten", "hinweis"])
        schreiber.writerows(zeilen)
    with open(pfade["vorschlag_csv"], "w", newline="", encoding="utf-8-sig") as datei:
        schreiber = csv.writer(datei)
        schreiber.writerow(["absender", "mails", "löschbar", "prüfen", "behalten", "arten"])
        schreiber.writerows(pro_absender)
    json_speichern(pfade["vorschlag_json"], {
        "erstellt": datetime.now().strftime("%Y-%m-%d %H:%M"), "modell": MODELL, "regeln": REGELN,
        "nur_teil": bool(args.test or args.nur), "loeschbar_ids": loeschbar_ids, "pruefen_ids": pruefen_ids,
    })

    # --- 6. Zusammenfassung im Terminal
    gesamt = sum(z[1] for z in pro_absender)
    arten_gesamt = {}
    for z in zeilen:
        arten_gesamt[z[3]] = arten_gesamt.get(z[3], 0) + z[2]
    teil = "TEILLAUF (" + str(len(absender_liste)) + " Adressen). " if (args.test or args.nur) else ""
    print("\n" + teil + str(gesamt) + " Mails: " + str(len(loeschbar_ids)) + " löschbar, " + str(len(pruefen_ids))
          + " zum Prüfen, " + str(gesamt - len(loeschbar_ids) - len(pruefen_ids)) + " behalten")
    print("  nach Art: " + ", ".join(a + " " + str(n) for a, n in sorted(arten_gesamt.items(), key=lambda x: -x[1])))
    print("\n" + "Absender".ljust(44) + "Mails".rjust(7) + "Löschbar".rjust(10) + "Prüfen".rjust(8) + "Behalten".rjust(10))
    for z in pro_absender[:25]:
        print(z[0][:43].ljust(44) + str(z[1]).rjust(7) + str(z[2]).rjust(10) + str(z[3]).rjust(8) + str(z[4]).rjust(10))
    print("\nJede Gruppe mit Art und Entscheidung: " + str(pfade["entscheidungen"]))
    print("Übersicht pro Absender:              " + str(pfade["vorschlag_csv"]))


if __name__ == "__main__":
    main()
