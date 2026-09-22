# Joyful Rides owner and enquiry upgrade
Based on GitHub commit 0872c42. Not deployed or pushed.

## Safe installation (Windows)
Stop Flask. Back up your entire existing folder first, including instance/analytics.db and any WAL files. This ZIP is an UPDATE PACKAGE, not a standalone site: it deliberately excludes existing images, databases and credentials. Make a COPY of your entire existing project folder named joyfull-rides-review. Overlay the files from this ZIP into that copy, replacing matching code/templates and retaining your existing static images and instance database. Do not copy these changes into production before reviewing. Use a fresh virtual environment inside the review folder.

    py -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    python -m flask --app app create-owner
    python app.py

The create-owner command prompts privately for a password (minimum 14 characters). Use a unique generated password. No default owner is created. Visit http://127.0.0.1:5000/login.

The copied instance/analytics.db preserves historical analytics. Keep Flask stopped while copying. The old visits table is untouched; new validated measurements start in page_views. Back up before schema changes. Never replace a running SQLite database.

## Contact configuration (PowerShell, before starting)
Set CONTACT_PHONE to the real international phone number; WHATSAPP_NUMBER to digits including country code. These links stay hidden when unset. CONTACT_EMAIL defaults to the existing site email; verify it is monitored. Environment variables are read directly; a .env file is NOT automatically loaded.

    $env:CONTACT_PHONE = "your real phone number"
    $env:WHATSAPP_NUMBER = "your real WhatsApp number"
    $env:CONTACT_EMAIL = "your monitored email address"

Quote and contact forms save enquiries to the owner inbox. No email or WhatsApp notification is sent automatically. The owner must check the inbox; messaging integration is a separate future feature. Do not enter a child's full name or sensitive information.

## Production requirements
Set APP_ENV=production and a unique random SECRET_KEY of at least 32 characters. HTTPS is required because production session cookies are Secure. Set DATA_DIR to the exact mounted persistent disk directory. Copy historical data there before start if required. Create the owner on that same database using the hosting shell. Start with gunicorn app:app. Do not run with Flask development server in production.

Only set TRUSTED_PROXY_HOPS when the hosting topology and trusted proxy count are known; default is zero. Wrong values let clients spoof identities used for rate limiting. With zero behind a shared proxy, login/submission rate limits may be shared among users. SQLite is intended for one service instance with persistent storage, not multiple independent replicas. Verify storage survives restart BEFORE release. Keep database backups private; it contains contact details. Define a retention period and operational deletion process appropriate to your service before launch.

Owner sessions expire after 30 minutes from sign-in; cookies are HttpOnly/SameSite=Lax. Every POST requires CSRF. Login attempts: five per IP per 15 minutes; submissions: ten per IP per hour. Form validation, honeypot and duplicate submission tokens are included. Existing legacy HTTP Basic credentials are not used.

Analytics excludes known bots by user-agent heuristic (not guaranteed human traffic), non-GET requests, errors, static and owner pages. Campaign attribution is last tagged campaign in the browser session. Example: /?utm_source=facebook&utm_medium=social&utm_campaign=school_runs. Conversion counts distinct tracked browsers who enquire, not total enquiries divided by visitors. Won count refers to enquiries created in the selected 30-day window that currently have won status. Browser-session cookies are used for attribution; review your privacy/consent requirements before release.

## Tests
    python -m pip install pytest
    python -m pytest -q

## Rollback
Stop the service and restore both the prior code and the pre-upgrade database backup. Restoring an old database loses enquiries received after that backup; export/preserve newer enquiries securely first. No repository commits, pushes or deployments were performed by this package.
