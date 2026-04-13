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
from urllib.parse import urlencode
import sqlite3
import hashlib
import secrets
import os

# ─────────────────────────────────────────────────────────────
# APP CONFIGURATION
# ─────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.path.join(INSTANCE_DIR, "cargoledger.db")

os.makedirs(INSTANCE_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# DATABASE UTILITIES
# ─────────────────────────────────────────────────────────────
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

        # Default Admin
        db.execute(
            "INSERT OR IGNORE INTO admins (username, password) VALUES (?, ?)",
            ("admin", _hash("admin@cargo2024"))
        )

        # Default Manager
        db.execute("""
            INSERT OR IGNORE INTO users
            (name, email, password, role, site_ids)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "Site Manager",
            "manager@cargo.com",
            _hash("manager123"),
            "manager",
            "WB001,WB002,WB003"
        ))

        # Default Government Officer
        db.execute("""
            INSERT OR IGNORE INTO users
            (name, email, password, role, site_ids)
            VALUES (?, ?, ?, ?, ?)
        """, (
            "Govt Officer",
            "govt@cargo.com",
            _hash("govt1234"),
            "govt",
            "WB001,WB002,WB003"
        ))

        db.commit()


# Initialize DB (Required for Render)
init_db()

# ─────────────────────────────────────────────────────────────
# CONTEXT PROCESSORS
# ─────────────────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    return {"now": datetime.now()}


@app.context_processor
def utility_processor():
    def qs(**kwargs):
        args = request.args.to_dict()
        args.update({k: v for k, v in kwargs.items() if v is not None})
        return urlencode(args)
    return dict(qs=qs)


@app.template_filter("yesterday")
def yesterday_filter(dt):
    return (dt - timedelta(days=1)).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────
# AUTH DECORATORS
# ─────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────
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
    site_ids = session.get("site_ids", "WB001,WB002,WB003").split(",")

    placeholders = ",".join("?" * len(site_ids))

    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT * FROM weighbridge_records
            WHERE site_id IN ({placeholders})
            ORDER BY id DESC LIMIT 50
            """,
            site_ids
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
        site_ids=site_ids,
        trend_labels="[]",
        trend_data="[]",
        parties=[],
        filter_date=datetime.now().strftime("%d-%m-%Y"),
        filter_date_iso=datetime.now().strftime("%Y-%m-%d"),
        is_govt=is_govt(),
    )


# ─────────────────────────────────────────────────────────────
# RECORDS ROUTE
# ─────────────────────────────────────────────────────────────
@app.route("/records")
@login_required
def records():
    db = get_db()

    site_ids = session.get("site_ids", "WB001,WB002,WB003").split(",")

    site_filter = request.args.get("site", "all")
    search = request.args.get("search", "").strip()
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    sort_col = request.args.get("sort", "id")
    sort_dir = request.args.get("dir", "desc")
    page = int(request.args.get("page", 1))
    per_page = 25
    offset = (page - 1) * per_page

    allowed_cols = {
        "id", "site_id", "challan_id", "date",
        "vehicle_number", "party_name", "material",
        "gross_weight", "tare_weight", "net_weight"
    }

    if sort_col not in allowed_cols:
        sort_col = "id"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    conditions = []
    params = []

    if site_filter != "all":
        conditions.append("site_id = ?")
        params.append(site_filter)
    else:
        placeholders = ",".join("?" * len(site_ids))
        conditions.append(f"site_id IN ({placeholders})")
        params.extend(site_ids)

    if search:
        like = f"%{search}%"
        conditions.append("""
            (vehicle_number LIKE ? OR
             party_name LIKE ? OR
             material LIKE ? OR
             rfid_tag LIKE ? OR
             challan_id LIKE ?)
        """)
        params.extend([like] * 5)

    if date_from:
        conditions.append("date >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("date <= ?")
        params.append(date_to)

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    total = db.execute(
        f"SELECT COUNT(*) FROM weighbridge_records {where_clause}",
        params
    ).fetchone()[0]

    rows = db.execute(
        f"""
        SELECT * FROM weighbridge_records
        {where_clause}
        ORDER BY {sort_col} {sort_dir}
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset]
    ).fetchall()

    totals_row = db.execute(
        f"""
        SELECT
            SUM(gross_weight) AS tg,
            SUM(tare_weight) AS tt,
            SUM(net_weight) AS tn
        FROM weighbridge_records
        {where_clause}
        """,
        params
    ).fetchone()

    totals = {
        "tg": totals_row["tg"] or 0,
        "tt": totals_row["tt"] or 0,
        "tn": totals_row["tn"] or 0,
    }

    pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "records.html",
        rows=rows,
        total=total,
        site_ids=site_ids,
        site_filter=site_filter,
        search=search,
        date_from=date_from,
        date_to=date_to,
        sort_col=sort_col,
        sort_dir=sort_dir,
        page=page,
        pages=pages,
        totals=totals,
        is_govt=is_govt(),
    )


# ─────────────────────────────────────────────────────────────
# API SYNC (Bridge Integration)
# ─────────────────────────────────────────────────────────────
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
            exists = db.execute(
                "SELECT id FROM weighbridge_records WHERE challan_id=? AND site_id=?",
                (r.get("challan_id"), site_id)
            ).fetchone()

            if not exists:
                db.execute("""
                    INSERT INTO weighbridge_records (
                        site_id, challan_id, date, vehicle_number,
                        party_name, material, gross_weight,
                        tare_weight, net_weight, rfid_tag,
                        gross_datetime, tare_datetime, net_datetime
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    site_id,
                    r.get("challan_id"),
                    r.get("date"),
                    r.get("vehicle_number"),
                    r.get("party_name"),
                    r.get("material"),
                    r.get("gross_weight", 0),
                    r.get("tare_weight", 0),
                    r.get("net_weight", 0),
                    r.get("rfid_tag"),
                    r.get("gross_datetime"),
                    r.get("tare_datetime"),
                    r.get("net_datetime"),
                ))
                inserted += 1

        db.commit()

    return jsonify({"ok": True, "inserted": inserted})


# ─────────────────────────────────────────────────────────────
# ADMIN ROUTES
# ─────────────────────────────────────────────────────────────
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
        users = db.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
    return render_template("admin_panel.html", users=users)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_id", None)
    return redirect(url_for("admin_login"))


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0",
            port=int(os.environ.get("PORT", 5000)))