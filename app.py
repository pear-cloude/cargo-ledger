"""
app.py — Cargo-Ledger Web Dashboard (Production Ready)
Trionex Labs
"""

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, flash
)
from functools import wraps
from datetime import datetime, timedelta
import sqlite3
import hashlib
import secrets
import os

# ──────────────────────────────────────────────────────────────────────
# APP CONFIGURATION
# ──────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.path.join(INSTANCE_DIR, "cargoledger.db")

os.makedirs(INSTANCE_DIR, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────
# DATABASE UTILITIES
# ──────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _hash(password):
    return hashlib.sha256(password.encode()).hexdigest()


def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'manager',
            site_ids TEXT DEFAULT 'WB001,WB002,WB003',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS weighbridge_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id TEXT NOT NULL,
            challan_id TEXT,
            date TEXT,
            vehicle_number TEXT,
            party_name TEXT,
            material TEXT,
            gross_weight REAL,
            tare_weight REAL,
            net_weight REAL,
            rfid_tag TEXT,
            gross_datetime TEXT,
            tare_datetime TEXT,
            net_datetime TEXT,
            slip_type TEXT DEFAULT 'CHALLAN',
            driver TEXT,
            synced_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        );
        """)

        # Default admin
        db.execute(
            "INSERT OR IGNORE INTO admins (username, password) VALUES (?, ?)",
            ("admin", _hash("admin@cargo2024"))
        )

        # Default manager
        db.execute("""
            INSERT OR IGNORE INTO users
            (name, email, password, role, site_ids, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (
            "Site Manager",
            "manager@cargo.com",
            _hash("manager123"),
            "manager",
            "WB001,WB002,WB003"
        ))

        # Default government user
        db.execute("""
            INSERT OR IGNORE INTO users
            (name, email, password, role, site_ids, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (
            "Govt Officer",
            "govt@cargo.com",
            _hash("govt1234"),
            "govt",
            "WB001,WB002,WB003"
        ))

        db.commit()


# Initialize database automatically (CRITICAL FOR RENDER)
try:
    init_db()
    print("✅ Database initialized successfully.")
except Exception as e:
    print(f"❌ Database initialization failed: {e}")


# ──────────────────────────────────────────────────────────────────────
# TEMPLATE HELPERS
# ──────────────────────────────────────────────────────────────────────
@app.template_filter("yesterday")
def yesterday_filter(dt):
    return (dt - timedelta(days=1)).strftime("%Y-%m-%d")


@app.context_processor
def inject_now():
    return {"now": datetime.now()}


# ──────────────────────────────────────────────────────────────────────
# AUTH DECORATORS
# ──────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper


def is_govt():
    return session.get("role") == "govt"


# ──────────────────────────────────────────────────────────────────────
# ROUTES
# ──────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("landing.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = _hash(request.form["password"])

        with get_db() as db:
            user = db.execute(
                "SELECT * FROM users WHERE email=? AND password=? AND is_active=1",
                (email, password)
            ).fetchone()

        if user:
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["role"] = user["role"]
            session["site_ids"] = user["site_ids"]
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM weighbridge_records ORDER BY id DESC LIMIT 50"
        ).fetchall()

    return render_template(
        "dashboard.html",
        recent=rows,
        today_trips=len(rows),
        today_net=sum(float(r["net_weight"] or 0) for r in rows),
        today_gross=sum(float(r["gross_weight"] or 0) for r in rows),
        today_tare=sum(float(r["tare_weight"] or 0) for r in rows),
        month_net=0,
        total_trips=len(rows),
        site_breakdown={},
        site_ids=["WB001", "WB002", "WB003"],
        trend_labels="[]",
        trend_data="[]",
        parties=[],
        filter_date=datetime.now().strftime("%d-%m-%Y"),
        filter_date_iso=datetime.now().strftime("%Y-%m-%d"),
        is_govt=is_govt(),
    )


# ──────────────────────────────────────────────────────────────────────
# API SYNC (Bridge App Integration)
# ──────────────────────────────────────────────────────────────────────
@app.route("/api/sync", methods=["POST"])
def api_sync():
    key = request.headers.get("X-License-Key", "")

    site_map = {
        os.environ.get("KEY_WB001", "CARGO-WB001-SECRET"): "WB001",
        os.environ.get("KEY_WB002", "CARGO-WB002-SECRET"): "WB002",
        os.environ.get("KEY_WB003", "CARGO-WB003-SECRET"): "WB003",
    }

    site_id = site_map.get(key)
    if not site_id:
        return jsonify({"ok": False, "error": "Invalid API Key"}), 403

    records = request.get_json(force=True) or []
    inserted = 0

    with get_db() as db:
        for r in records:
            existing = db.execute(
                "SELECT id FROM weighbridge_records WHERE challan_id=? AND site_id=?",
                (r.get("challan_id"), site_id)
            ).fetchone()

            if not existing:
                db.execute("""
                    INSERT INTO weighbridge_records (
                        site_id, challan_id, date, vehicle_number,
                        party_name, material, gross_weight,
                        tare_weight, net_weight, rfid_tag,
                        gross_datetime, tare_datetime, net_datetime
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    site_id,
                    r.get("challan_id", ""),
                    r.get("date", ""),
                    r.get("vehicle_number", ""),
                    r.get("party_name", ""),
                    r.get("material", ""),
                    r.get("gross_weight", 0),
                    r.get("tare_weight", 0),
                    r.get("net_weight", 0),
                    r.get("rfid_tag", ""),
                    r.get("gross_datetime", ""),
                    r.get("tare_datetime", ""),
                    r.get("net_datetime", "")
                ))
                inserted += 1

        db.commit()

    return jsonify({"ok": True, "inserted": inserted})


# ──────────────────────────────────────────────────────────────────────
# ADMIN LOGIN
# ──────────────────────────────────────────────────────────────────────
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form["username"]
        password = _hash(request.form["password"])

        with get_db() as db:
            admin = db.execute(
                "SELECT * FROM admins WHERE username=? AND password=?",
                (username, password)
            ).fetchone()

        if admin:
            session["admin_id"] = admin["id"]
            return redirect(url_for("admin_panel"))

        flash("Invalid credentials.", "error")

    return render_template("admin_login.html")


@app.route("/admin")
@admin_required
def admin_panel():
    with get_db() as db:
        users = db.execute("SELECT * FROM users").fetchall()
    return render_template("admin_panel.html", users=users)


# ──────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))