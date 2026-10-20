"""
mailtext.py - den Text einer Mail aus den Dateien von Apple Mail holen.

Apple Mail speichert jede Mail als eigene Datei (.emlx) unter ~/Library/Mail/V10/<Konto-Kennung>/...
Darin steht die komplette Mail. Wir suchen sie über ihre Message-ID (eine eindeutige Kennung,
die jede Mail im Kopf trägt) und geben den Anfang des Textes zurück, ohne HTML.

Nur lesend. Ohne Internet. Etwa 30 Sekunden, um alle Dateien eines Kontos einmal zu durchsuchen.
"""
import email
import email.policy
import html
import re

from lokal import MAIL_ORDNER


def emlx_lesen(pfad):
    """Liest eine .emlx-Datei. Aufbau: 1. Zeile = Länge der Mail in Bytes, danach die Mail, danach Apple-Zusatzinfos."""
    roh = pfad.read_bytes()
    erste_zeile, rest = roh.split(b"\n", 1)
    return email.message_from_bytes(rest[:int(erste_zeile)], policy=email.policy.default)


def text_aus_mail(nachricht):
    """Holt den lesbaren Text einer Mail: lieber die Text-Fassung, sonst HTML ohne Tags."""
    teil = nachricht.get_body(preferencelist=("plain", "html"))
    if teil is None:
        return ""
    try:
        text = teil.get_content()
    except Exception:
        return ""
    if teil.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)  # Skripte und Formatierung entfernen
        text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))    # alle übrigen HTML-Tags entfernen
    return " ".join(text.split())  # mehrfache Leerzeichen und Zeilenumbrüche zusammenfassen


def message_id_sauber(wert):
    """'<abc@x.de>' -> 'abc@x.de' (in den Dateien steht sie mal mit, mal ohne spitze Klammern)."""
    return str(wert or "").strip().strip("<>").strip()


def index_bauen(konto_kennung):
    """
    Durchsucht alle Mail-Dateien eines Kontos einmal und merkt sich: Message-ID -> Datei.
    Gibt ein Dictionary zurück. Liest dabei nur die Kopfzeilen, nicht die ganzen Mails.
    """
    index = {}
    for ordner in MAIL_ORDNER.glob("V*/" + konto_kennung):
        for pfad in ordner.rglob("*.emlx"):
            try:
                kopf = pfad.read_bytes()[:20000]  # die Message-ID steht weit oben
                treffer = re.search(rb"(?im)^message-id:\s*(<[^>\r\n]+>|\S+)", kopf)
                if treffer:
                    index[message_id_sauber(treffer.group(1).decode("utf-8", "replace"))] = pfad
            except OSError:
                continue
    return index


def text_holen(index, message_id, max_zeichen=1500):
    """Gibt die ersten max_zeichen Zeichen des Mailtextes zurück, oder "" wenn die Mail nicht gefunden wird."""
    pfad = index.get(message_id_sauber(message_id))
    if pfad is None:
        return ""
    try:
        return text_aus_mail(emlx_lesen(pfad))[:max_zeichen]
    except Exception:
        return ""
