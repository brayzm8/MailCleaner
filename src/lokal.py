"""
lokal.py - Mails aus der Datenbank von Apple Mail lesen (statt über die Gmail-API).

Wird von main.py aufgerufen, wenn du  python src/main.py --local  startest.
Liest NUR, ohne Internet, in Sekunden.

Voraussetzung: VS Code (bzw. das Terminal) braucht unter
Systemeinstellungen -> Datenschutz & Sicherheit -> Festplattenvollzugriff die Erlaubnis.

Wichtig: Apple Mail kann mit einem ANDEREN Google-Konto verbunden sein als die Gmail-API (token.json).
Deshalb hat die lokale Variante eigene Dateien (local_cache.jsonl, local_gescheitert.json, local_postfach.csv).

Jede Mail wird in dasselbe Format umgewandelt wie beim Google-Abruf (siehe scan.mail_vereinfachen),
damit der Vorfilter beide Quellen gleich behandeln kann.
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from auth import PROJEKTORDNER

LOCAL_CACHE_DATEI = PROJEKTORDNER / "reports" / "local_cache.jsonl"
LOCAL_GESCHEITERT_DATEI = PROJEKTORDNER / "reports" / "local_gescheitert.json"
LOCAL_POSTFACH_DATEI = PROJEKTORDNER / "reports" / "local_postfach.csv"

MAIL_ORDNER = Path.home() / "Library" / "Mail"

# Ordnernamen in Apple Mail (deutsch/englisch) -> Gmail-Label. So bleibt der Rest des Programms gleich.
ORDNER_LABELS = [
    ("spam", "SPAM"), ("junk", "SPAM"),
    ("papierkorb", "TRASH"), ("trash", "TRASH"),
    ("entw", "DRAFT"), ("draft", "DRAFT"),
    ("gesendet", "SENT"), ("sent", "SENT"),
    ("inbox", "INBOX"),
]

# Apples eigene Einordnung (Spalte model_category). Apple dokumentiert die Zahlen NICHT.
# Die Zuordnung ist VERMUTET, abgeleitet aus dem Verhalten der Mails:
#   3: 72 % mit Abmelde-Link, 95 % ungelesen   -> Werbung
#   2: 37 % mit Abmelde-Link                    -> Aktualisierungen
#   1: 14 % mit Abmelde-Link                    -> Transaktionen (Bestellungen, Rechnungen)
#   0:  0 % mit Abmelde-Link, meist gelesen     -> Hauptpostfach (persönlich)
APPLE_KATEGORIEN = {
    0: "CATEGORY_PERSONAL",
    1: "CATEGORY_TRANSAKTIONEN",
    2: "CATEGORY_UPDATES",
    3: "CATEGORY_PROMOTIONS",
}

ABFRAGE = """
    select x.ROWID, x.global_message_id, x.date_received, a.address, a.comment,
           coalesce(x.subject_prefix, '') || coalesce(s.subject, ''),
           x.read, x.flagged, x.unsubscribe_type, x.list_id_hash,
           m.url, g.message_id_header, g.model_category
    from messages x
    left join addresses a on a.ROWID = x.sender
    left join subjects s on s.ROWID = x.subject
    left join mailboxes m on m.ROWID = x.mailbox
    left join message_global_data g on g.message_id = x.message_id
    where x.deleted = 0 and m.url like ?
"""

# Die Adresse eines Kontos: der Empfänger, an den in diesem Konto mit Abstand die meisten Mails gingen.
# (Apple speichert das Konto in der Datenbank nur als Kennnummer, nicht als Adresse.)
KONTO_ADRESSE_ABFRAGE = """
    select lower(a.address) from recipients r
    join addresses a on a.ROWID = r.address
    join messages x on x.ROWID = r.message
    join mailboxes m on m.ROWID = x.mailbox
    where m.url like ?
    group by r.address order by count(*) desc limit 1
"""

# Wie viele Mails hat jedes Konto? Die Kennnummer steht in der Ordner-Adresse: imap://<KENNUNG>/...
KONTEN_ABFRAGE = """
    select m.url, count(*) from messages x join mailboxes m on m.ROWID = x.mailbox
    where x.deleted = 0 group by m.ROWID
"""


def versionsnummer(pfad):
    """Sortierhilfe: '.../V10/MailData/Envelope Index' -> 10 (damit V10 nach V9 kommt)."""
    try:
        return int(pfad.parts[-3][1:])
    except ValueError:
        return 0


def datenbank_finden():
    """Sucht die Datenbank von Apple Mail. Der Ordner (V10, V11 ...) hängt von der macOS-Version ab."""
    try:
        kandidaten = list(MAIL_ORDNER.glob("V*/MailData/Envelope Index"))
    except PermissionError:
        raise SystemExit("Kein Zugriff auf " + str(MAIL_ORDNER) + ". Bitte unter Systemeinstellungen -> "
                         "Datenschutz & Sicherheit -> Festplattenvollzugriff für VS Code/Terminal erlauben.")
    if len(kandidaten) == 0:
        raise SystemExit("Keine Datenbank von Apple Mail gefunden (oder kein Zugriff). Ist ein Konto in Apple Mail eingerichtet?")
    kandidaten.sort(key=versionsnummer)
    return kandidaten[-1]


def zeile_umwandeln(zeile, eigene_adresse):
    """Macht aus einer Zeile der Apple-Datenbank eine Mail im selben Format wie beim Google-Abruf."""
    (rowid, global_id, empfangen, adresse, name, betreff, gelesen, markiert,
     abmelde, liste, ordner_url, message_id_header, kategorie) = zeile

    ordner = unquote(ordner_url or "").lower()
    adresse = (adresse or "").lower()

    labels = []
    for teil, label in ORDNER_LABELS:
        if teil in ordner and label not in labels:
            labels.append(label)
    if not gelesen:
        labels.append("UNREAD")
    if markiert:
        labels.append("STARRED")
    if kategorie in APPLE_KATEGORIEN:
        labels.append(APPLE_KATEGORIEN[kategorie])
    if eigene_adresse and adresse == eigene_adresse and "SENT" not in labels:
        labels.append("SENT")  # von dir selbst geschickt

    return {
        "id": "apple-" + str(global_id if global_id is not None else rowid),
        "datum": datetime.fromtimestamp(empfangen or 0).strftime("%Y-%m-%d"),
        "absender_name": name or "",
        "absender": adresse,
        "betreff": (betreff or "").strip(),
        "ungelesen": not gelesen,
        "labels": labels,
        "abmelde_header": bool(abmelde),          # Apple erkennt Abmelde-Links selbst
        "liste_header": bool(liste),              # Apple speichert eine Kennung der Verteilerliste
        "quelle": "apple_mail",
        "message_id_header": message_id_header or "",  # brauchen wir später, um die Mail in Gmail wiederzufinden
    }


def verbinden():
    """Öffnet die Datenbank von Apple Mail NUR ZUM LESEN. Apple Mail darf dabei geöffnet bleiben."""
    return sqlite3.connect("file:" + str(datenbank_finden()) + "?mode=ro", uri=True)


def nach_adresse(konto):
    """Sortierhilfe: Konten alphabetisch nach Adresse, damit die Nummern stabil bleiben."""
    return konto["adresse"]


def konten_auflisten():
    """Gibt alle Konten in Apple Mail zurück: Liste von {"nummer", "kennung", "adresse", "mails"}."""
    verbindung = verbinden()
    try:
        mails_pro_konto = {}
        for url, anzahl in verbindung.execute(KONTEN_ABFRAGE).fetchall():
            teile = (url or "").split("/")
            if len(teile) < 3:
                continue
            kennung = teile[2]  # imap://<KENNUNG>/Ordner
            mails_pro_konto[kennung] = mails_pro_konto.get(kennung, 0) + anzahl

        konten = []
        for kennung, anzahl in mails_pro_konto.items():
            ergebnis = verbindung.execute(KONTO_ADRESSE_ABFRAGE, ("%://" + kennung + "/%",)).fetchone()
            konten.append({"kennung": kennung, "adresse": ergebnis[0] if ergebnis else "unbekannt", "mails": anzahl})
    finally:
        verbindung.close()

    konten.sort(key=nach_adresse)
    for nummer, konto in enumerate(konten, start=1):
        konto["nummer"] = nummer
    return konten


def konto_waehlen(konten, auswahl):
    """
    Sucht das gewünschte Konto. auswahl = Adresse (z. B. "ich@gmail.com") oder Nummer aus der Liste.
    Gibt das Konto zurück, oder None, wenn nicht eindeutig (dann wird die Liste angezeigt).
    """
    if auswahl is None and len(konten) == 1:
        return konten[0]
    for konto in konten:
        if auswahl is not None and (auswahl.lower() == konto["adresse"] or auswahl == str(konto["nummer"])):
            return konto

    if auswahl is None:
        print("In Apple Mail sind mehrere Konten. Bitte eines wählen:")
    else:
        print("Konto '" + auswahl + "' nicht gefunden. Vorhanden sind:")
    for konto in konten:
        print("  " + str(konto["nummer"]) + ")  " + konto["adresse"].ljust(36) + str(konto["mails"]).rjust(7) + " Mails")
    print("\nZum Beispiel:  python src/main.py --local --konto " + (konten[0]["adresse"] if konten else "1"))
    return None


def lokal_lesen(konto):
    """
    Liest alle Mails EINES Kontos aus Apple Mail. Gibt zurück: (mails, fehlerliste)
      mails       = Liste der Mails (eine pro Mail, auch wenn sie in mehreren Ordnern liegt)
      fehlerliste = Dictionary ID -> Grund, für Zeilen, die sich nicht umwandeln ließen
    """
    verbindung = verbinden()
    try:
        zeilen = verbindung.execute(ABFRAGE, ("%://" + konto["kennung"] + "/%",)).fetchall()
    finally:
        verbindung.close()

    mails = {}
    fehlerliste = {}
    for zeile in zeilen:
        try:
            mail = zeile_umwandeln(zeile, konto["adresse"])
            mail["konto"] = konto["adresse"]  # damit später klar ist, aus welchem Konto die Mail stammt
            mail["konto_kennung"] = konto["kennung"]  # Ordnername in ~/Library/Mail, für den Mailtext
        except Exception as fehler:
            fehlerliste["apple-zeile-" + str(zeile[0])] = {"status": None, "meldung": str(fehler)[:200], "details": ""}
            continue
        # Dieselbe Mail kann in mehreren Ordnern liegen: zusammenführen, Labels vereinen.
        if mail["id"] in mails:
            for label in mail["labels"]:
                if label not in mails[mail["id"]]["labels"]:
                    mails[mail["id"]]["labels"].append(label)
        else:
            mails[mail["id"]] = mail

    return list(mails.values()), fehlerliste
