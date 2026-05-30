# Project setup

This project expects a `mediapipe` package that exposes the `mp.solutions` API.
Some `mediapipe` releases are not available for very new Python versions (for
example Python 3.14). If you see an error about `mp.solutions` not existing,
use a Python 3.10 or 3.11 interpreter and install the pinned requirements.

Quick setup (recommended):

Windows (PowerShell)

```powershell
# create a virtualenv with a Python 3.10/3.11 executable
python3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# create a .env file with GROQ_API_KEY=your-key (or set env var)
# then run the app
python app.py
```

If you do not have Python 3.10/3.11 installed, install it via the official
installer and repeat the steps above.
