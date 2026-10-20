# GmailCleaner

Ein Python-Werkzeug, das ein überfülltes Gmail-Postfach analysiert, mit einer lokal laufenden KI
jede Mail einordnet (Werbung, Benachrichtigung, Rechnung, Sicherheitsmail …) und vorschlägt, was weg kann.

## Motivation

E-Mail-Postfächer müllen schnell zu: Newsletter, Shop-Angebote, Social-Media-Benachrichtigungen.
Kaum jemand öffnet sie, kaum jemand löscht sie. Irgendwann ist der kostenlose Speicher bei Google voll,
und statt aufzuräumen landet man beim kostenpflichtigen Speicher-Upgrade.

Natürlich kann man in Gmail von Hand aufräumen oder einfach eine ganze Kategorie wie "Werbung" oder
"Soziale Netzwerke" leeren. Das Problem: Googles Einordnung ist nicht zuverlässig. Eine Rechnung oder
Bestellbestätigung landet schnell bei "Werbung", und wer die Kategorie pauschal löscht, verliert sie mit.
Umgekehrt stecken viele Newsletter im Hauptpostfach, wo man sie nicht vermutet.

Genau das war die Motivation: nicht blind nach Googles Kategorien löschen, sondern selbst prüfen,
was eine Mail wirklich ist. Dieses Projekt lässt eine lokale KI anhand von Absender, Betreff und Text
bestimmen, welche Art Mail es ist, entscheidet mit klaren Regeln und mehreren Sicherungen, was weg kann,
und lässt die letzte Entscheidung beim Nutzer.

## Was es macht

1. **Einlesen** aller Mails, wahlweise über die Gmail-API oder in Sekunden aus der lokalen Datenbank von Apple Mail
2. **Gruppieren** nach Absender-Adresse und Betreff-Muster; Eigenes, Entwürfe und markierte Mails bleiben unberührt
3. **Art-Erkennung** mit einem lokalen Sprachmodell: Werbung, Benachrichtigung, Transaktion, Sicherheit/Konto oder Persönlich
4. **Regeln und Sicherungen** entscheiden, was löschbar ist, z. B. Werbung ja, alte Benachrichtigungen ja, Rechnungen nie.
   Widersprüche im Text (Bestellnummer, "neue Anmeldung" …) oder Absender, die sonst Wichtiges schicken, führen zu "prüfen"
5. **Aufräumen** in den Papierkorb, erst nach Bestätigung *(geplant)*

Ein Postfach mit rund 20.000 Mails ergab knapp 600 Absender-Adressen; die 50 größten machen 84 % der Mails aus.

## Technik

- **Python 3**
- **Google Cloud / Gmail API** mit OAuth 2.0 (`google-api-python-client`, `google-auth-oauthlib`), Zugriff nur lesend
- **SQLite**, um die Datenbank von Apple Mail direkt auszulesen
- **Ollama** mit dem offenen Sprachmodell **Qwen 3**, läuft komplett lokal, keine Mail-Daten verlassen den Rechner

## Technische Schwerpunkte

- **Umgang mit API-Limits:** Gmail erlaubt nur eine begrenzte Zahl Abfragen pro Minute. Das Programm verteilt die Anfragen gleichmäßig, erkennt Ablehnungen, bremst selbstständig und merkt sich die Grenze.
- **Fortsetzbar:** Jedes Paket wird sofort zwischengespeichert. Nach einem Abbruch geht es an derselben Stelle weiter, fehlgeschlagene Mails werden protokolliert und zuerst wiederholt.
- **Datenschutz:** Mailtexte werden nur lokal gelesen, aus den Dateien von Apple Mail. Die KI läuft auf dem eigenen Rechner, nichts geht an einen Cloud-Dienst.
- **Sicherheit vor Bequemlichkeit:** Die KI bestimmt nur die Art einer Mail, nicht ob sie gelöscht wird. Das entscheiden feste Regeln, mehrere unabhängige Sicherungen und am Ende der Nutzer. Gelöscht wird nur in den Papierkorb.


