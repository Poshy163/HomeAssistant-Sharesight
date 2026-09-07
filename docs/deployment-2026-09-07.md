# Approved deployment: Sharesight 2.3.1 and SharesightAPI 1.6.0

The dependency pin in the manifest and test requirements is now
`SharesightAPI==1.6.0`, installed from the published PyPI wheel. The
[client release](https://github.com/Poshy163/Sharesight-API/releases/tag/v1.6.0)
adds models and read helpers; the integration's generic response API remains
compatible. Diagnostics now expose the installed `api.client_version`, read
in the executor, so deployment verification can check more than the manifest pin.

Local validation with 1.6.0: 395 portable tests passed; 428 tests passed on each
of HA 2026.7.4 and 2026.9.1, with 78.02% and 78.08% coverage respectively.
Both Linux environments passed dependency consistency checks. Ruff and hassfest
passed. The runtime-version diagnostic has additional focused HA validation.

Before deployment, a Home Assistant backup named **Before Sharesight 2.3.1 API
1.6.0** completed with both configuration and Recorder database included.
A separate copy of all 28 existing integration source files was retained locally.
Rollback uses that copy for source; restore the supported HA backup if Recorder
data also needs reverting. The old manifest requests client 1.5.0; verify that
dependency and the entry after restarting a rollback.

## Deployment verification

All **28/28** source files were copied over Samba and matched by SHA-256.
Home Assistant was restarted through its validated restart action. After startup,
its loaded manifest reported **2.3.1** and the new runtime diagnostic reported
the installed client as **1.6.0**. HA remained on **2026.9.1 / Python 3.14.6**.

The first complete poll succeeded, including slow and optional sources, with
`degraded=false`, no lockout, no parked/unsupported endpoints and no logged
endpoint failures. All **890** registry entities remained. The same name-search
snapshot before and after restart contained **891** states: 838 with values,
50 unknown and three unavailable. The three unavailable states are the existing
two absent-label entities and retired news entity documented in the audit.

Portfolio value, daily/weekly/30-day change and closed-position metrics returned
numeric states. Existing monthly entity IDs survived the clearer 30-Day label.
No active repair issues were reported. Startup logs confirmed installation of
1.6.0 and a successful 11.7-second poll. History backfill found no missing
pre-today portfolio-value points, and no Sharesight errors were observed in the
post-restart log window. The normal HA custom-integration warning still appears.

The previously prepared local source ZIP, exact deployment diff and hash manifest
were refreshed to include the 1.6.0 pin and runtime diagnostic.
