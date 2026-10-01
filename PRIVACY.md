# Privacy Policy — FitLog

Last updated: October 1, 2026.

FitLog is a self-hosted workout-logging MCP server built for the Amazon
"Build, Ship, Shape" Developer Hackathon 2026 (Alexa+ track).

## What data is collected

- **Workout logs**: exercise names, sets (reps × weight), and dates you log.
- **Nutrition logs**: protein grams and dates you log.
- **OAuth tokens**: random bearer tokens issued during account linking so
  Alexa+ can call the server on your behalf. They expire after 30 days.

## Where it lives

Everything is stored in a SQLite database file on the machine where **you**
run the server (`~/.fitlog/fitlog.db` by default). No data is sent to the
developer or any third party. When Alexa+ invokes a tool, only the data
needed for that request travels over the encrypted (HTTPS) connection.

## Account linking

Alexa+ links to your server with OAuth 2.1 authorization-code + PKCE (S256).
Before the approval page is shown, the server requires the owner password
you configured via `FITLOG_OWNER_PASSWORD` (mandatory for any public
deployment), so only you can complete the linking.

## Your control

Delete the database file and all your logged data is gone. Revoking the
add-on in Alexa+ stops all future access (issued tokens also expire).

## Contact

Open an issue at https://github.com/hahahahahahahahah6/fitlog-mcp/issues.
