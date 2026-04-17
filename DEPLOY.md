# Cargo-Ledger — Render PostgreSQL Deployment Guide
Trionex Labs

---

## Why This Migration?

Render free tier uses ephemeral storage — every restart wiped SQLite data.
This version uses Render PostgreSQL which persists permanently.

---

## Step 1 — Push to GitHub

```bash
git init && git add . && git commit -m "Phase 8 PostgreSQL"
git branch -M main
git remote add origin https://github.com/YOU/cargo-ledger.git
git push -u origin main
```

---

## Step 2 — Create PostgreSQL on Render

1. render.com → New → PostgreSQL
2. Name: cargo-ledger-db, Plan: Free
3. Copy the Internal Database URL

---

## Step 3 — Deploy Web Service

1. New → Web Service → connect GitHub repo
2. Environment tab → Add variables:

| Key | Value |
|-----|-------|
| DATABASE_URL | Paste Internal Database URL |
| SECRET_KEY | Click Generate |
| KEY_WB001 | CARGO-WB001-SECRET |
| KEY_WB002 | CARGO-WB002-SECRET |
| KEY_WB003 | CARGO-WB003-SECRET |

3. Click Deploy — live in 3 minutes
4. Tables are created automatically on startup

---

## Default Credentials

| Role | Login | Password |
|------|-------|----------|
| Manager | manager@cargo.com | manager123 |
| Govt | govt@cargo.com | govt1234 |
| Admin | /admin/login → admin | admin@cargo2024 |

Change all passwords after first login.

---

## RST Slip Links

Admin Panel → Daily RST Slip Links section:
1. Select site (WB001/WB002/WB003)
2. Pick date
3. Paste Google Drive share link
4. Save — appears as "📄 RST Slip" on dashboard

For public Google Drive links:
Right-click file → Share → Anyone with the link → Viewer

---

## Bridge Uploader Config

In bridge_uploader.py:
  server_url: https://your-app.onrender.com
  KEY_WB001:  CARGO-WB001-SECRET (match env var)

---

## Notes
- Free Render tier sleeps after 15 min (30s wake on first hit)
- pool_pre_ping + pool_recycle=280 handle connection timeouts
- Upgrade to Render Starter $7/mo for always-on
