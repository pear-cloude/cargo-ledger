# Cargo-Ledger Web Dashboard
Trionex Labs

## Setup & Run
```bash
pip install flask
python run.py
```
Open http://localhost:5000

## Default Credentials
| Role     | Username / Email      | Password        |
|----------|-----------------------|-----------------|
| Customer | demo@cargo.com        | demo1234        |
| Admin    | admin                 | admin@cargo2024 |

## Pages
- `/`              — Public landing page with pricing
- `/login`         — Customer login
- `/dashboard`     — Customer dashboard (KPIs, charts, recent records)
- `/records`       — Full paginated records table with search
- `/admin/login`   — Admin login
- `/admin`         — Customer management (add, suspend, extend licenses)

## API — Desktop App Sync
POST `/api/sync`
- Header: `X-License-Key: CARGO-XXXX-XXXX-XXXX`
- Body: JSON array of challan records

GET `/api/license/check?key=CARGO-XXXX-XXXX-XXXX`
- Returns `{"valid": true/false, "plan": "...", "expires_at": "..."}`

## License Logic
- Customer gets a license key when added by admin
- Key is checked on every page load — if inactive or expired, access is blocked
- Admin can suspend/activate/extend any license from the admin panel
- Desktop app can call `/api/license/check` on startup to gate access

## Hosting (Production)
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```
Use nginx as a reverse proxy for SSL/domain setup.
