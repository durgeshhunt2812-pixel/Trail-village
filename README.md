# Project setup

The repository is now split into two deployable parts:

- `backend/` contains the Flask app, API routes, and SQLite data.
- `frontend/` contains the static HTML pages and frontend assets.

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
pip install -r backend\requirements.txt
# create a .env file with GROQ_API_KEY=your-key (or set env var)
# then run the backend
python backend\app.py
```

If you do not have Python 3.10/3.11 installed, install it via the official
installer and repeat the steps above.

## Split deployment

The app supports separate frontend and backend deployment.

Backend:

```powershell
$env:FRONTEND_BASE_URL="https://your-frontend-host"
python backend\app.py
```

Frontend:

- Host the HTML pages from `frontend/` as static files.
- Host `frontend/static/app-config.js` and `frontend/static/style.css` from the frontend origin.
- Set `window.APP_CONFIG.apiBaseUrl` to the backend URL before deployment.

The backend stores SQLite files in `backend/data/` and keeps its own static folder in `backend/static/`.

Example frontend config:

```javascript
window.APP_CONFIG = {
	apiBaseUrl: "https://your-backend-host"
};
```
