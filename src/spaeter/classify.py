"""
classify.py - hier wird entschieden, in welchen "Topf" jede Mail kommt.

Wird von main.py aufgerufen. Liest nur, ändert nichts im Postfach.

Die vier Töpfe:
  Geschützt      - wichtig, wird nie als löschbar vorgeschlagen
  Löschkandidat  - sieht nach Werbung/Newsletter/Spam aus
  Prüfen         - gemischte Signale (z. B. Werbung, die das Wort "Rechnung" enthält)
  Unsicher       - keine Regel trifft zu, bleibt unberührt

Jede Mail wird als "Dictionary" gespeichert, also als Liste von Beschriftung -> Wert:
  mail["betreff"], mail["absender"], mail["topf"] ...
"""
import json
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parseaddr

from auth import PROJEKTORDNER

# Namen der Töpfe (an einer Stelle festgelegt, damit sich keine Tippfehler einschleichen)
GESCHUETZT = "Geschützt"
KANDIDAT = "Löschkandidat"
PRUEFEN = "Prüfen"
UNSICHER = "Unsicher"
PAPIERKORB = "TRASH"  # so nennt Gmail das Label für den Papierkorb

# Bei diesen Anbietern sagt die Domain nichts über den Absender aus
# (gmail.com gehört ja nicht "einer Firma"), deshalb gruppieren wir dort nach der ganzen Adresse.
FREEMAIL = ["gmail.com", "googlemail.com", "gmx.de", "gmx.net", "web.de", "outlook.com",
            "hotmail.com", "yahoo.com", "icloud.com", "t-online.de", "freenet.de"]


def regeln_laden():
    """Liest regeln.json. Gibt es sie nicht, wird die Vorlage regeln.example.json benutzt."""
    datei = PROJEKTORDNER / "regeln.json"
    if not datei.exists():
        datei = PROJEKTORDNER / "regeln.example.json"
    regeln = json.loads(datei.read_text(encoding="utf-8"))

    # Fehlende Einträge mit sinnvollen Standardwerten auffüllen.
    standard = {
        "eigene_adressen": [],
        "schutz_absender": [],
        "sperre_absender": [],
        "schutz_woerter": [],
        "absender_min_mails": 10,
        "absender_min_ungelesen_quote": 0.9,
    }
    for name in standard:
        if name not in regeln:
            regeln[name] = standard[name]
    return regeln


def text_dekodieren(text):
    """Betreffs mit Umlauten kommen manchmal verschlüsselt an (=?UTF-8?...). Hier wird das lesbar."""
    try:
        return str(make_header(decode_header(text)))
    except Exception:
        return text


def absender_gruppe(adresse):
    """
    Bestimmt, zu welchem "Absender" eine Adresse zählt.
      news@em.myprotein.com  ->  myprotein.com   (damit alle MyProtein-Mails zusammenfallen)
      freund@web.de          ->  freund@web.de   (Freemail: ganze Adresse)
    """
    if "@" not in adresse:
        return adresse or "unbekannt"

    domain = adresse.split("@")[-1]  # alles hinter dem @
    if domain in FREEMAIL:
        return adresse

    teile = domain.split(".")  # "em.myprotein.com" -> ["em", "myprotein", "com"]
    # Endungen wie .co.uk haben zwei Teile; dann nehmen wir drei Teile statt zwei.
    if len(teile) >= 3 and len(teile[-1]) == 2 and teile[-2] in ["co", "com", "org", "net", "ac", "gov"]:
        return ".".join(teile[-3:])
    return ".".join(teile[-2:])


def mail_aus_gmail_antwort(antwort):
    """Macht aus der rohen Gmail-Antwort ein übersichtliches Dictionary."""
    inhalt = antwort.get("payload", {})

    # Die Kopfzeilen kommen als Liste; wir machen daraus ein Nachschlagewerk
    # mit kleingeschriebenen Namen: kopf["subject"], kopf["from"] ...
    kopf = {}
    for zeile in inhalt.get("headers", []):
        kopf[zeile["name"].lower()] = zeile["value"]

    name, adresse = parseaddr(text_dekodieren(kopf.get("from", "")))
    adresse = adresse.lower()
    labels = antwort.get("labelIds", [])
    zeitpunkt = datetime.fromtimestamp(int(antwort.get("internalDate", 0)) / 1000)

    return {
        "id": antwort["id"],
        "datum": zeitpunkt.strftime("%Y-%m-%d"),
        "absender_name": name,
        "absender": adresse,
        "gruppe": absender_gruppe(adresse),
        "betreff": text_dekodieren(kopf.get("subject", "")),
        "labels": labels,
        "ungelesen": "UNREAD" in labels,
        # Mails mit Anhang haben diesen Typ. Ob es ein PDF ist, wissen wir hier nicht.
        "vermutlich_anhang": inhalt.get("mimeType", "") == "multipart/mixed",
        # Diese Kopfzeile steht fast nur in Newslettern und Werbung.
        "abmelde_header": "list-unsubscribe" in kopf,
        # Werden von einordnen() ausgefüllt:
        "topf": "",
        "gruende": [],
    }


def passt_auf_liste(adresse, liste):
    """
    Prüft, ob eine Adresse in einer Liste (Schutzliste/Sperrliste) steht.
    Ein Eintrag mit @ meint genau eine Adresse, ohne @ eine ganze Domain.
    Gibt den passenden Eintrag zurück, oder None, wenn nichts passt.
    """
    adresse = adresse.lower()
    domain = adresse.split("@")[-1]
    for eintrag in liste:
        eintrag_klein = eintrag.lower().strip()
        if "@" in eintrag_klein:
            if adresse == eintrag_klein:
                return eintrag
        else:
            # "linkedin.com" passt auch auf "mail.linkedin.com"
            if domain == eintrag_klein or domain.endswith("." + eintrag_klein):
                return eintrag
    return None


def absender_zaehlen(mails):
    """Zählt pro Absender, wie viele Mails es gibt und wie viele davon ungelesen sind."""
    zaehler = {}
    for mail in mails:
        gruppe = mail["gruppe"]
        if gruppe not in zaehler:
            zaehler[gruppe] = {"gesamt": 0, "ungelesen": 0}
        zaehler[gruppe]["gesamt"] += 1
        if mail["ungelesen"]:
            zaehler[gruppe]["ungelesen"] += 1
    return zaehler


def einordnen(mail, regeln, eigene_adressen, zaehler):
    """
    Entscheidet über eine Mail und trägt das Ergebnis ein:
      mail["topf"]    = einer der vier Töpfe
      mail["gruende"] = Liste der Gründe, damit du nachvollziehen kannst, warum
    """
    labels = mail["labels"]

    # ---- Stufe 1: Harter Schutz. Ein einziger Treffer reicht, dann ist die Mail sicher. ----
    harte_gruende = []
    if "SENT" in labels or "DRAFT" in labels:
        harte_gruende.append("von dir gesendet / Entwurf")
    if mail["absender"] in eigene_adressen:
        harte_gruende.append("Absender ist eigene Adresse (Selbst-Mail)")
    if "STARRED" in labels:
        harte_gruende.append("mit Stern markiert")
    eintrag = passt_auf_liste(mail["absender"], regeln["schutz_absender"])
    if eintrag is not None:
        harte_gruende.append("Schutzliste: " + eintrag)

    if len(harte_gruende) > 0:
        mail["topf"] = GESCHUETZT
        mail["gruende"] = harte_gruende
        return  # fertig, nichts anderes zählt mehr

    # ---- Stufe 2: Weiche Hinweise auf "könnte wichtig sein". ----
    # Sie schützen nur, wenn nichts auf Werbung hindeutet (siehe Stufe 4).
    hinweise = []
    betreff_klein = mail["betreff"].lower()
    for wort in regeln["schutz_woerter"]:
        if wort.lower() in betreff_klein:
            hinweise.append("Schlagwort '" + wort + "'")
    if "CATEGORY_PURCHASES" in labels:
        hinweise.append("Gmail-Kategorie Käufe")
    if "IMPORTANT" in labels:
        hinweise.append("von Gmail als wichtig eingestuft")
    if mail["vermutlich_anhang"]:
        hinweise.append("enthält vermutlich einen Anhang")

    # ---- Stufe 3: Merkmale für "unnötig". ----
    merkmale = []
    if "SPAM" in labels:
        merkmale.append("Gmail-Spam")
    if "CATEGORY_PROMOTIONS" in labels:
        merkmale.append("Gmail-Kategorie Werbung")
    if "CATEGORY_SOCIAL" in labels:
        merkmale.append("Gmail-Kategorie Soziale Netzwerke")
    if mail["abmelde_header"]:
        merkmale.append("Abmelde-Header (Newsletter)")
    eintrag = passt_auf_liste(mail["absender"], regeln["sperre_absender"])
    if eintrag is not None:
        merkmale.append("Sperrliste: " + eintrag)

    # Absender, von dem fast alles ungelesen bleibt:
    zahlen = zaehler.get(mail["gruppe"], {"gesamt": 0, "ungelesen": 0})
    if zahlen["gesamt"] >= regeln["absender_min_mails"]:
        quote = zahlen["ungelesen"] / zahlen["gesamt"]
        if quote >= regeln["absender_min_ungelesen_quote"]:
            merkmale.append("Absender: " + str(zahlen["ungelesen"]) + " von " + str(zahlen["gesamt"]) + " ungelesen")

    # ---- Stufe 4: Entscheiden. ----
    if "SPAM" in labels:
        # Spam bleibt Kandidat, auch wenn das Wort "Rechnung" drinsteht (oft Betrug).
        mail["topf"] = KANDIDAT
        mail["gruende"] = merkmale
    elif len(hinweise) > 0 and len(merkmale) > 0:
        # Beides trifft zu: Du sollst entscheiden.
        mail["topf"] = PRUEFEN
        mail["gruende"] = ["Hinweis: " + h for h in hinweise] + ["Merkmal: " + m for m in merkmale]
    elif len(hinweise) > 0:
        mail["topf"] = GESCHUETZT
        mail["gruende"] = hinweise
    elif len(merkmale) > 0:
        mail["topf"] = KANDIDAT
        mail["gruende"] = merkmale
    else:
        mail["topf"] = UNSICHER
        mail["gruende"] = ["keine Regel trifft zu"]
