"""
scan.py - Mails bei Gmail abholen und zwischenspeichern.

Wird von main.py aufgerufen. Hier wird nur gelesen, nichts verändert.
Wir holen NICHT den Text der Mails, sondern nur: Absender, Betreff, Labels, Datum.
Das geht schneller und ist datensparsamer.

Zwischenspeicher: Jede abgerufene Mail wird sofort in reports/cache.jsonl gespeichert
(eine Mail pro Zeile). Bricht das Programm ab, bleibt alles Bisherige erhalten und der
nächste Start holt nur noch die fehlenden Mails.
"""
import json
import sys
import time
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parseaddr

from auth import PROJEKTORDNER

CACHE_DATEI = PROJEKTORDNER / "reports" / "cache.jsonl"

# Hier stehen alle Mails, bei denen das Abrufen NICHT geklappt hat, mit dem Grund (JSON).
# Gegenstück dazu ist CACHE_DATEI mit allen erfolgreich geholten Mails.
# Die Datei wird beim nächsten Start zuerst abgearbeitet und gelöscht, sobald sie leer ist.
GESCHEITERT_DATEI = PROJEKTORDNER / "reports" / "gescheitert.json"

# So viele Mails fragen wir gleichzeitig ab. Bei 25 meldete Google manchmal "Too many concurrent
# requests for user" (zu viele Anfragen gleichzeitig). Das Tempo pro Minute bleibt davon unberührt,
# es wird über das Minutenbudget gesteuert: kleinere Pakete gehen einfach öfter raus.
MAILS_PRO_PAKET = 10

# Google erlaubt nur eine bestimmte Menge pro Minute ("Units per minute per user").
# Das Programm hält sich deshalb an ein Minutenbudget: höchstens so viele Mails in 60 Sekunden.
# Es startet bei START_MAILS_PRO_MINUTE, senkt das Budget bei Ablehnungen und hebt es vorsichtig
# wieder an, wenn alles klappt (aber nie unter MIN_ und nie über MAX_MAILS_PRO_MINUTE).
# Gemessen: 302/min lief minutenlang ohne Fehler, ab etwa 385/min hat Google komplett gesperrt.
START_MAILS_PRO_MINUTE = 300
MIN_MAILS_PRO_MINUTE = 60
MAX_MAILS_PRO_MINUTE = 330

# So lange lassen wir Google in Ruhe, wenn es viele Anfragen abgelehnt hat (Sekunden).
PAUSE_NACH_SPERRE = 20

# Nach so vielen Paketen ohne Ablehnung wird das Budget um 10 % erhöht (10 Pakete = 100 Mails),
# aber nie über MAX_MAILS_PRO_MINUTE.
PAKETE_BIS_SCHNELLER = 10

# Von jeder Mail fordern wir nur diese Felder an (Antworten werden dadurch kleiner und schneller).
FELDER = "id,internalDate,labelIds,payload(headers)"

# Bei diesen Fehlercodes sagt Google "zu schnell, versuch es gleich nochmal".
NOCHMAL_VERSUCHEN = [403, 429, 500, 502, 503]

# Diese Kopfzeilen jeder Mail brauchen wir. Die letzten vier verraten Massenmails (Newsletter,
# Benachrichtigungen), egal wer der Absender ist. Mehr Kopfzeilen kosten kein zusätzliches Kontingent.
KOPFZEILEN = ["From", "Subject", "List-Unsubscribe", "List-Id", "Feedback-ID", "Precedence"]


def text_dekodieren(text):
    """Betreffs mit Umlauten kommen manchmal verschlüsselt an (=?UTF-8?...). Hier wird das lesbar."""
    try:
        return str(make_header(decode_header(text)))
    except Exception:
        return text


def mail_vereinfachen(antwort):
    """Macht aus der rohen Gmail-Antwort ein einfaches Dictionary mit den Angaben, die wir brauchen."""
    # Die Kopfzeilen kommen als Liste; wir machen daraus ein Nachschlagewerk: kopf["subject"] ...
    kopf = {}
    for zeile in antwort.get("payload", {}).get("headers", []):
        kopf[zeile["name"].lower()] = zeile["value"]

    name, adresse = parseaddr(text_dekodieren(kopf.get("from", "")))
    labels = antwort.get("labelIds", [])
    zeitpunkt = datetime.fromtimestamp(int(antwort.get("internalDate", 0)) / 1000)

    return {
        "id": antwort["id"],
        "datum": zeitpunkt.strftime("%Y-%m-%d"),
        "absender_name": name,
        "absender": adresse.lower(),
        "betreff": text_dekodieren(kopf.get("subject", "")),
        "ungelesen": "UNREAD" in labels,
        "labels": labels,
        # Kennzeichen von Massenmails (Newsletter, Benachrichtigungen). Persönliche Mails haben sie praktisch nie.
        "abmelde_header": "list-unsubscribe" in kopf,   # "Abmelden"-Link für das Mailprogramm
        "liste_header": "list-id" in kopf,              # Kennung einer Verteilerliste
        "feedback_header": "feedback-id" in kopf,       # Kennung von Massenversendern für Google
        "massenmail_header": kopf.get("precedence", "").strip().lower() in ["bulk", "list", "junk"],
    }


def cache_laden(pfad=None):
    """
    Liest den Zwischenspeicher. Gibt ein Dictionary zurück: ID -> Mail. Leer, wenn es noch keinen gibt.
    pfad = welche Datei (ohne Angabe: der Google-Zwischenspeicher CACHE_DATEI).
    """
    datei_pfad = pfad if pfad is not None else CACHE_DATEI
    mails = {}
    if not datei_pfad.exists():
        return mails
    with open(datei_pfad, encoding="utf-8") as datei:
        for zeile in datei:
            zeile = zeile.strip()
            if zeile == "":
                continue
            try:
                mail = json.loads(zeile)
            except ValueError:
                continue  # eine beim Abbruch halb geschriebene Zeile überspringen wir einfach
            mails[mail["id"]] = mail
    return mails


def cache_anhaengen(mails, pfad=None):
    """Hängt Mails an den Zwischenspeicher an (eine pro Zeile). Bereits Gespeichertes bleibt unberührt."""
    datei_pfad = pfad if pfad is not None else CACHE_DATEI
    datei_pfad.parent.mkdir(exist_ok=True)
    with open(datei_pfad, "a", encoding="utf-8") as datei:
        for mail in mails:
            # Der Zeilenumbruch steht VOR dem Eintrag, so bleibt die Datei auch nach einem Abbruch lesbar.
            datei.write("\n" + json.dumps(mail, ensure_ascii=False))


def cache_neu_schreiben(mails, pfad):
    """Schreibt den Zwischenspeicher komplett neu (für die lokale Quelle, die in Sekunden alles liest).
    Erst in eine Hilfsdatei, dann umbenennen: So ist die Datei auch bei einem Abbruch nie halb fertig."""
    pfad.parent.mkdir(exist_ok=True)
    hilfsdatei = pfad.with_name(pfad.name + ".tmp")
    with open(hilfsdatei, "w", encoding="utf-8") as datei:
        for mail in mails:
            datei.write("\n" + json.dumps(mail, ensure_ascii=False))
    hilfsdatei.replace(pfad)


def gescheitert_laden(pfad=None):
    """
    Liest die Fehlerliste. Gibt ein Dictionary zurück: ID -> {"status": ..., "meldung": ...}.
    Leer, wenn es die Datei nicht gibt (oder sie unlesbar ist).
    pfad = welche Datei (ohne Angabe: die Google-Fehlerliste GESCHEITERT_DATEI).
    """
    datei_pfad = pfad if pfad is not None else GESCHEITERT_DATEI
    if not datei_pfad.exists():
        return {}
    try:
        return json.loads(datei_pfad.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def gescheitert_speichern(fehlerliste, pfad=None):
    """
    Speichert die Fehlerliste als JSON (überschreibt die Datei).
    Ist die Liste leer, wird die Datei gelöscht.
    """
    datei_pfad = pfad if pfad is not None else GESCHEITERT_DATEI
    if len(fehlerliste) == 0:
        if datei_pfad.exists():
            datei_pfad.unlink()
        return
    datei_pfad.parent.mkdir(exist_ok=True)
    # Erst in eine Hilfsdatei schreiben und dann umbenennen: So ist die Datei auch bei einem
    # Abbruch mitten im Schreiben nie halb fertig.
    hilfsdatei = datei_pfad.with_name(datei_pfad.name + ".tmp")
    hilfsdatei.write_text(json.dumps(fehlerliste, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    hilfsdatei.replace(datei_pfad)


def zeit_text(sekunden):
    """Macht aus Sekunden einen lesbaren Text: 75 -> '01:15', 3725 -> '1:02:05'."""
    sekunden = int(sekunden)
    stunden = sekunden // 3600
    minuten = (sekunden % 3600) // 60
    rest = sekunden % 60
    if stunden > 0:
        return str(stunden) + ":" + str(minuten).zfill(2) + ":" + str(rest).zfill(2)
    return str(minuten).zfill(2) + ":" + str(rest).zfill(2)


def fortschritt(text):
    # Schreibt eine Zeile, die sich selbst überschreibt (\r = zurück an den Zeilenanfang).
    print("\r" + text, end="", flush=True, file=sys.stderr)


def mail_ids_holen(gmail):
    """
    Holt die IDs ALLER Mails (neueste zuerst).
    Gmail liefert höchstens 500 IDs pro Anfrage, deshalb die Schleife:
    sie läuft so lange, bis es keine weitere Seite mehr gibt.
    """
    ids = []
    seiten_token = None  # Merkzettel für "die nächste Seite"

    while True:
        antwort = gmail.users().messages().list(
            userId="me",
            maxResults=500,
            pageToken=seiten_token,
            includeSpamTrash=True,  # Spam soll mit auftauchen
        ).execute()

        for eintrag in antwort.get("messages", []):
            ids.append(eintrag["id"])
        fortschritt("  " + str(len(ids)) + " Mail-IDs gefunden")

        seiten_token = antwort.get("nextPageToken")
        if seiten_token is None:
            break  # keine weitere Seite, alle IDs sind geholt

    print(file=sys.stderr)
    return ids


def mails_abrufen(gmail, ids, nach_jedem_paket):
    """
    Holt zu jeder ID die Metadaten (Absender, Betreff, Labels ...).
    nach_jedem_paket = Funktion, die nach jedem Paket mit den neuen Mails aufgerufen wird
                       (main.py übergibt hier das Speichern in den Zwischenspeicher).
    Gibt zurück: (ergebnisse, nicht_geholt)
      ergebnisse   = Dictionary  ID -> einfache Mail
      nicht_geholt = Liste der IDs, bei denen es nicht geklappt hat (Gründe: gescheitert.json)
    """
    startzeit = time.time()  # für den Timer in der Anzeige: wann hat das Abrufen begonnen?
    ergebnisse = {}
    offen = list(ids)  # diese IDs müssen noch geholt werden
    neu_im_paket = []  # die Mails des gerade abgefragten Pakets

    # Die Fehlerliste: ID -> Grund. Sie wird nach jedem Paket in gescheitert.json gesichert.
    # Wir starten mit der Liste vom letzten Mal, aber nur mit IDs, die jetzt wirklich noch
    # geholt werden sollen (Mails, die es nicht mehr gibt, fliegen raus).
    alle_ids = set(ids)
    fehlerliste = {}
    for mail_id, grund in gescheitert_laden().items():
        if mail_id in alle_ids:
            fehlerliste[mail_id] = grund

    gescheitert = []  # IDs, bei denen Google in der laufenden Runde "zu schnell" & Co. gemeldet hat

    # Für die Anzeige: Wie viele der Mails, die schon vom letzten Mal auf der Fehlerliste standen,
    # sind in diesem Lauf inzwischen erledigt?
    fruehere_menge = set(fehlerliste)
    fruehere_erledigt = {"anzahl": 0}

    # Diese Funktion ruft Google selbst auf, sobald eine Antwort aus einem
    # Paket da ist (bei Gmail "callback" genannt). Wir sortieren darin nur ein.
    def antwort_einsortieren(anfrage_id, antwort, fehler):
        if fehler is None:
            mail = mail_vereinfachen(antwort)
            ergebnisse[anfrage_id] = mail
            neu_im_paket.append(mail)
            fehlerliste.pop(anfrage_id, None)  # klappt (doch): von der Fehlerliste streichen
            if anfrage_id in fruehere_menge:
                fruehere_erledigt["anzahl"] += 1
        else:
            # JEDER Fehler kommt mit seinem Grund auf die Fehlerliste.
            status = getattr(getattr(fehler, "resp", None), "status", None)
            # Den Grund von Google speichern ("reason"/"error_details"), nicht die lange Adresse,
            # die sonst die ganze Meldung belegt.
            grund_text = getattr(fehler, "reason", None) or str(fehler)
            details = getattr(fehler, "error_details", None)
            fehlerliste[anfrage_id] = {
                "status": status,
                "meldung": str(grund_text)[:200],
                "details": str(details)[:200] if details else "",
            }
            # Bei "zu schnell" & Co. lohnt sich ein neuer Versuch gleich in der nächsten Runde.
            if status in NOCHMAL_VERSUCHEN:
                gescheitert.append(anfrage_id)

    # Das Minutenbudget: "pro_minute" ist die Obergrenze, "sauber_in_folge" zählt Pakete ohne Ablehnung,
    # "naechster_start" ist der Zeitpunkt, ab dem das nächste Paket losgeschickt werden darf.
    # "obergrenze" ist das höchste Budget, das wir uns erlauben. Hat Google einmal gesperrt, wird sie
    # knapp unter das Tempo gesetzt, bei dem das passiert ist. "letzte_bremsung" = wann zuletzt gesenkt wurde.
    tempo = {"pro_minute": START_MAILS_PRO_MINUTE, "obergrenze": MAX_MAILS_PRO_MINUTE,
             "sauber_in_folge": 0, "naechster_start": 0.0, "letzte_bremsung": -1000000.0}

    def anzeige_aktualisieren(zusatz=""):
        """Schreibt die Fortschrittszeile, z. B.:
        Zeit 05:12 | Alte Gescheiterte: 1200/4142 | Geholt: 1180/15609 (227/min) | Limit 250/min | gescheitert: 38"""
        vergangen = time.time() - startzeit
        text = "  Zeit " + zeit_text(vergangen) + " | "
        if len(fruehere_menge) > 0:
            text += "Alte Gescheiterte: " + str(fruehere_erledigt["anzahl"]) + "/" + str(len(fruehere_menge)) + " | "
        text += "Geholt: " + str(len(ergebnisse)) + "/" + str(len(ids))
        if vergangen >= 1:
            text += " (" + str(int(len(ergebnisse) / vergangen * 60)) + "/min)"  # tatsächliche Geschwindigkeit seit Start
        text += " | Limit " + str(tempo["pro_minute"]) + "/min"  # das Budget, das sich das Programm selbst setzt
        text += " | gescheitert: " + str(len(fehlerliste))
        text += zusatz + "      "  # Leerzeichen am Ende löschen Reste einer längeren vorherigen Zeile
        fortschritt(text)

    def budget_abwarten(anzahl):
        """
        Verteilt die Pakete gleichmäßig über die Minute, statt das ganze Budget auf einmal zu verbrauchen.
        Bei 300 Mails pro Minute und 10 Mails pro Paket heißt das: alle 2 Sekunden ein Paket.
        Während des Wartens läuft die Anzeige weiter, damit man sieht, dass nichts hängt.
        """
        while True:
            rest = tempo["naechster_start"] - time.time()
            if rest <= 0:
                break
            anzeige_aktualisieren(" | nächstes Paket in " + str(int(rest) + 1) + " s")
            time.sleep(min(rest, 1))
        # Das nächste Paket darf erst starten, wenn dieses "abbezahlt" ist.
        tempo["naechster_start"] = time.time() + anzahl * 60 / tempo["pro_minute"]

    def pakete_abfragen(id_liste):
        """Fragt die IDs in Paketen zu je MAILS_PRO_PAKET Mails ab. Ein Paket = eine Anfrage an Google."""
        for start in range(0, len(id_liste), MAILS_PRO_PAKET):
            ids_im_paket = id_liste[start:start + MAILS_PRO_PAKET]
            abgelehnt_vorher = len(gescheitert)
            budget_abwarten(len(ids_im_paket))
            paket = gmail.new_batch_http_request(callback=antwort_einsortieren)
            for mail_id in ids_im_paket:
                paket.add(
                    gmail.users().messages().get(
                        userId="me", id=mail_id, format="metadata", metadataHeaders=KOPFZEILEN, fields=FELDER
                    ),
                    request_id=mail_id,
                )
            paket.execute()

            gescheitert_speichern(fehlerliste)  # zuerst die Liste der gescheiterten sichern ...
            nach_jedem_paket(neu_im_paket)     # ... dann die geholten Mails, damit bei einem Abbruch nichts verloren geht
            neu_im_paket.clear()

            # Budget anpassen.
            abgelehnt = len(gescheitert) - abgelehnt_vorher
            if abgelehnt * 10 > len(ids_im_paket):
                # Google lehnt mehr als 10 % ab: Das Limit ist überschritten.
                jetzt = time.time()
                tempo["sauber_in_folge"] = 0
                # Nur einmal pro Minute bremsen. Google sperrt nach dem Überschreiten eine Weile;
                # in der Zeit würde jedes weitere Senken das Tempo nur unnötig drücken.
                if jetzt - tempo["letzte_bremsung"] > 60:
                    # Hier lag die Grenze: in diesem Lauf nie wieder darüber gehen.
                    tempo["obergrenze"] = max(MIN_MAILS_PRO_MINUTE, int(tempo["pro_minute"] * 0.9))
                    tempo["pro_minute"] = max(MIN_MAILS_PRO_MINUTE, int(tempo["pro_minute"] * 0.8))
                    tempo["letzte_bremsung"] = jetzt
                # Google in Ruhe lassen, bis die Sperre vorbei ist (die Anzeige zählt dabei herunter).
                tempo["naechster_start"] = max(tempo["naechster_start"], jetzt + PAUSE_NACH_SPERRE)
            elif abgelehnt == 0:
                # Alles klappt: Nach PAKETE_BIS_SCHNELLER sauberen Paketen um 10 % schneller, aber nie über die Obergrenze.
                tempo["sauber_in_folge"] += 1
                if tempo["sauber_in_folge"] >= PAKETE_BIS_SCHNELLER:
                    tempo["pro_minute"] = min(tempo["obergrenze"], int(tempo["pro_minute"] * 1.1))
                    tempo["sauber_in_folge"] = 0

            anzeige_aktualisieren()

    # Hauptlauf: Bis zu 6 Runden. Was in einer Runde wegen "zu schnell" scheitert, wird in der nächsten wiederholt.
    for runde in range(6):
        gescheitert.clear()
        pakete_abfragen(offen)
        offen = list(gescheitert)
        if len(offen) == 0:
            break
        time.sleep(2 ** runde)  # vor der nächsten Runde länger warten: 1, 2, 4 ... Sekunden

    # Nachgang: Wenn alle Mails durchgelaufen sind, bekommen auch die Mails mit anderen Fehlern
    # (die bisher nicht wiederholt wurden) noch einen letzten Versuch.
    nachgang = [mail_id for mail_id, grund in fehlerliste.items() if grund["status"] not in NOCHMAL_VERSUCHEN]
    if len(nachgang) > 0:
        print("\n  Nachgang: " + str(len(nachgang)) + " Mails mit Fehler werden noch einmal versucht ...", file=sys.stderr)
        pakete_abfragen(nachgang)

    gescheitert_speichern(fehlerliste)  # leer = Datei wird gelöscht
    print(file=sys.stderr)
    return ergebnisse, sorted(fehlerliste)
