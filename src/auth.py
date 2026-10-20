"""
auth.py - die Anmeldung bei Gmail.

Wird von main.py aufgerufen. Macht genau eine Sache: liefert eine
funktionierende Verbindung zu Gmail ("service"), mit der man Mails abfragen kann.

Beim ersten Mal öffnet sich der Browser zur Google-Anmeldung. Danach wird der
Zugangsschlüssel in token.json gespeichert und beim nächsten Start wiederverwendet.
"""
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# "Scope" = was das Programm darf. gmail.readonly heißt: nur lesen, nichts ändern.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Der Projektordner ist ein Ordner über dieser Datei (src/auth.py -> GmailCleaner/).
PROJEKTORDNER = Path(__file__).resolve().parent.parent
CREDENTIALS_DATEI = PROJEKTORDNER / "credentials.json"  # Ausweis der App (von Google)
TOKEN_DATEI = PROJEKTORDNER / "token.json"              # dein persönlicher Schlüssel


def zugangsdaten_holen():
    zugang = None

    # 1. Gibt es schon einen gespeicherten Schlüssel? Dann laden.
    if TOKEN_DATEI.exists():
        zugang = Credentials.from_authorized_user_file(str(TOKEN_DATEI), SCOPES)

    # 2. Ist er noch gültig? Dann sind wir fertig.
    if zugang and zugang.valid:
        return zugang

    # 3. Abgelaufen, aber erneuerbar? Dann still im Hintergrund erneuern.
    if zugang and zugang.expired and zugang.refresh_token:
        zugang.refresh(Request())
    else:
        # 4. Sonst: Anmeldung im Browser.
        ablauf = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_DATEI), SCOPES)
        zugang = ablauf.run_local_server(port=0)

    # Neuen Schlüssel speichern, damit die Anmeldung nicht jedes Mal nötig ist.
    TOKEN_DATEI.write_text(zugang.to_json())
    return zugang


def gmail_verbinden():
    """Gibt die fertige Gmail-Verbindung zurück."""
    return build("gmail", "v1", credentials=zugangsdaten_holen(), cache_discovery=False)
