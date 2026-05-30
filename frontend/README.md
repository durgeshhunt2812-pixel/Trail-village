# Frontend deployment

This folder is the standalone UI bundle.

Files:

- `front_page.html`
- `middle_page.html`
- `otp_page.html`
- `index.html`
- `static/app-config.js`
- `static/style.css`

Set `window.APP_CONFIG.apiBaseUrl` to the backend origin before deploying. The UI uses that value for OTP requests, dashboard polling, camera streaming, and Socket.IO.