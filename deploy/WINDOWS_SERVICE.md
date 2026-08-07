# Running as a Windows service

Windows is the recommended host: it is the only platform where Excel can
evaluate the BMS log's `TAT` and ageing formulas and run the Daman workbook's
own validation macro before a file is handed to the insurer. On any other
platform those files are still produced correctly, but `/health` will report
`excel_recalculation: false` and each export records `recalculated: false`.

## Using NSSM

[NSSM](https://nssm.cc) is the simplest supported route.

```bat
nssm install BMSEndorsements ^
  "C:\bms-endorsements\app\.venv\Scripts\uvicorn.exe" ^
  "bms.web.app:app --host 127.0.0.1 --port 8000"

nssm set BMSEndorsements AppDirectory   C:\bms-endorsements\app
nssm set BMSEndorsements DisplayName    "BMS Medical Endorsement Platform"
nssm set BMSEndorsements Start          SERVICE_AUTO_START
nssm set BMSEndorsements AppStdout      C:\bms-endorsements\logs\service.log
nssm set BMSEndorsements AppStderr      C:\bms-endorsements\logs\service.log
nssm set BMSEndorsements AppRotateFiles 1

nssm start BMSEndorsements
```

Environment variables are read from `app\.env`. If you prefer setting them on
the service instead, use `nssm set BMSEndorsements AppEnvironmentExtra`.

## The service account

Excel automation runs as the service account, so that account needs:

- a local user profile (log in as it once before starting the service);
- an activated Microsoft Excel installation;
- read access to the template folder and write access to `BMS_DATA_ROOT`.

If Excel automation fails under the service account, the platform does not stop:
it records `recalculated: false` with the reason and the workbook is still
correct. Check `/health` to confirm which behaviour you are getting.

## Binding and access

Bind to `127.0.0.1` and put IIS or another reverse proxy in front for TLS, or
bind to the internal interface only. The application must not be reachable from
outside the BMS network.

Once TLS is in place, set `secure=True` on the session cookie in
`app/bms/web/app.py`.
