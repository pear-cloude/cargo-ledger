# Deploy to Render.com in 5 Minutes
## Cargo-Ledger Web Dashboard — Free Hosting

---

## Step 1 — Push to GitHub

```bash
cd CargoLedgerWeb
git init
git add .
git commit -m "Cargo-Ledger Web Dashboard"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/cargo-ledger.git
git push -u origin main
```

## Step 2 — Deploy on Render.com

1. Go to **render.com** → Sign up free (no credit card)
2. Click **New** → **Web Service**
3. Connect your GitHub repo → select `cargo-ledger`
4. Settings auto-filled from `render.yaml`
5. Click **Deploy** — live in ~3 minutes

Your URL: `https://cargo-ledger.onrender.com`

---

## Default Credentials

| Role | Email | Password |
|------|-------|----------|
| Manager | manager@cargo.com | manager123 |
| Govt | govt@cargo.com | govt1234 |
| Admin | /admin/login → admin | admin@cargo2024 |

**Change these immediately in Admin panel after first login.**

---

## Role Differences

| Feature | Manager | Govt |
|---------|---------|------|
| View all 3 sites | ✅ | ✅ |
| Date-wise search | ✅ | ✅ |
| Per-site breakdown | ✅ | ✅ |
| Records table | ✅ | ✅ |
| Edit records | ✅ | ❌ |
| Delete records | ✅ | ❌ |
| Role badge shown | MANAGER | GOVT — READ ONLY |

---

## Sync Desktop App → Web

From your weighbridge PC, POST records to:
```
POST https://cargo-ledger.onrender.com/api/sync
Header: X-License-Key: CARGO-WB001-SECRET
Body: JSON array of challan records
```

Change the API keys in Render dashboard → Environment Variables.

---

## Notes

- Free Render tier sleeps after 15 min inactivity (wakes in ~30s on first request)
- For always-on: upgrade to Render Starter ($7/month) or use Railway.app (free)
- Data persists in `/instance/cargoledger.db` (SQLite on disk)
