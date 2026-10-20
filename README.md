# GmailCleaner

Ein Python-Werkzeug, das ein überfülltes Gmail-Postfach analysiert, Newsletter und Werbung erkennt
und mit einer lokal laufenden KI prüft, ob darunter doch etwas Wichtiges ist.

## Motivation

E-Mail-Postfächer müllen schnell zu: Newsletter, Shop-Angebote, Social-Media-Benachrichtigungen.
Kaum jemand öffnet sie, kaum jemand löscht sie. Irgendwann ist der kostenlose Speicher bei Google voll,
und statt aufzuräumen landet man beim kostenpflichtigen Speicher-Upgrade.

Natürlich kann man in Gmail von Hand aufräumen oder einfach eine ganze Kategorie wie "Werbung" oder
"Soziale Netzwerke" leeren. Das Problem: Googles Einordnung ist nicht zuverlässig. Eine Rechnung oder
Bestellbestätigung landet schnell bei "Werbung", und wer die Kategorie pauschal löscht, verliert sie mit.
Umgekehrt stecken viele Newsletter im Hauptpostfach, wo man sie nicht vermutet.

Genau das war die Motivation: nicht blind nach Googles Kategorien löschen, sondern selbst prüfen,
was eine Mail wirklich ist. Dieses Projekt erkennt Massenmails anhand eigener Merkmale, lässt eine KI
nach wichtigen Mails suchen, die fälschlich dazwischen geraten sind, und lässt die Entscheidung beim Nutzer.

## Was es macht

1. **Einlesen** aller Mails, wahlweise über die Gmail-API oder in Sekunden aus der lokalen Datenbank von Apple Mail
2. **Vorfiltern** von Massenmails anhand technischer Kennzeichen (z. B. `List-Unsubscribe`), auch wenn Gmail sie falsch einsortiert hat
3. **KI-Prüfung** mit einem lokalen Sprachmodell: Sind unter den Kandidaten Rechnungen, Bestellungen oder Persönliches? *(in Arbeit)*
4. **Aufräumen** in den Papierkorb, erst nach Bestätigung *(geplant)*

In einem Postfach mit rund 20.000 Mails fand der Vorfilter etwa 13.700 Kandidaten von nur 121 Absendern.
Aus Tausenden Einzelentscheidungen werden so ein paar Dutzend.

## Technik

- **Python 3**
- **Google Cloud / Gmail API** mit OAuth 2.0 (`google-api-python-client`, `google-auth-oauthlib`), Zugriff nur lesend
- **SQLite**, um die Datenbank von Apple Mail direkt auszulesen
- **Ollama** mit dem offenen Sprachmodell **Qwen 3**, läuft komplett lokal, keine Mail-Daten verlassen den Rechner

## Technische Schwerpunkte

- **Umgang mit API-Limits:** Gmail erlaubt nur eine begrenzte Zahl Abfragen pro Minute. Das Programm verteilt die Anfragen gleichmäßig, erkennt Ablehnungen, bremst selbstständig und merkt sich die Grenze.
- **Fortsetzbar:** Jedes Paket wird sofort zwischengespeichert. Nach einem Abbruch geht es an derselben Stelle weiter, fehlgeschlagene Mails werden protokolliert und zuerst wiederholt.
- **Datenschutz:** Es werden keine Mailtexte gelesen, nur Absender, Betreff und Kennzeichen. Die KI läuft lokal.
- **Sicherheit vor Bequemlichkeit:** Die KI kann Mails nur vor dem Löschen retten, nie zum Löschen vorschlagen. Gelöscht wird nur in den Papierkorb.


