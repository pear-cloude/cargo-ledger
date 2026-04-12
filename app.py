"""
app.py — Cargo-Ledger Web Dashboard  (Phase 5 — Multi-Role)
Trionex Labs

Roles:
  manager  → sees all 3 sites, can edit records
  govt     → sees all 3 sites, read-only, no edit/delete
  admin    → manages customers and licenses

Deploy: push to GitHub → Render.com auto-deploys (free)
"""
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash)
from functools import wraps
from datetime import datetime, timedelta
import sqlite3, hashlib, secrets, os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "instance", "cargoledger.db")

# ── Template helpers ──────────────────────────────────────────────────────────
@app.template_filter("yesterday")
def yesterday_filter(dt):
    return (dt - timedelta(days=1)).strftime("%Y-%m-%d")

@app.context_processor
def inject_now():
    return {"now": datetime.now()}

# ── DB ────────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            email      TEXT UNIQUE NOT NULL,
            password   TEXT NOT NULL,
            role       TEXT NOT NULL DEFAULT 'manager',
            site_ids   TEXT DEFAULT 'WB001,WB002,WB003',
            is_active  INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS weighbridge_records (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id        TEXT NOT NULL DEFAULT 'WB001',
            challan_id     TEXT,
            date           TEXT,
            vehicle_number TEXT,
            party_name     TEXT,
            material       TEXT,
            gross_weight   REAL,
            tare_weight    REAL,
            net_weight     REAL,
            rfid_tag       TEXT,
            gross_datetime TEXT,
            tare_datetime  TEXT,
            net_datetime   TEXT,
            slip_type      TEXT DEFAULT 'CHALLAN',
            driver         TEXT,
            synced_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS admins (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rec_site   ON weighbridge_records(site_id);
        CREATE INDEX IF NOT EXISTS idx_rec_date   ON weighbridge_records(date);
        CREATE INDEX IF NOT EXISTS idx_rec_challan ON weighbridge_records(challan_id);
        """)

        # Default admin
        db.execute("INSERT OR IGNORE INTO admins (username,password) VALUES (?,?)",
                   ("admin", _hash("admin@cargo2024")))

        # Demo manager (all sites)
        db.execute("""INSERT OR IGNORE INTO users
                      (name,email,password,role,site_ids,is_active) VALUES
                      (?,?,?,?,?,1)""",
                   ("Site Manager","manager@cargo.com",
                    _hash("manager123"),"manager","WB001,WB002,WB003"))

        # Demo govt (all sites, read-only)
        db.execute("""INSERT OR IGNORE INTO users
                      (name,email,password,role,site_ids,is_active) VALUES
                      (?,?,?,?,?,1)""",
                   ("Govt Officer","govt@cargo.com",
                    _hash("govt1234"),"govt","WB001,WB002,WB003"))
        db.commit()

def _hash(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ── Auth decorators ───────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def dec(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return dec

def is_govt():
    return session.get("role") == "govt"

def is_manager():
    return session.get("role") == "manager"

# ── PUBLIC ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("landing.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        pw    = _hash(request.form["password"])
        with get_db() as db:
            u = db.execute(
                "SELECT * FROM users WHERE email=? AND password=? AND is_active=1",
                (email, pw)).fetchone()
        if u:
            session["user_id"]    = u["id"]
            session["user_name"]  = u["name"]
            session["role"]       = u["role"]
            session["site_ids"]   = u["site_ids"]
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# ── DASHBOARD ─────────────────────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    filter_date_raw = request.args.get("date","").strip()
    if filter_date_raw:
        try:
            dt_obj          = datetime.strptime(filter_date_raw, "%Y-%m-%d")
            filter_date     = dt_obj.strftime("%d-%m-%Y")
            filter_date_iso = filter_date_raw
        except ValueError:
            filter_date     = datetime.now().strftime("%d-%m-%Y")
            filter_date_iso = datetime.now().strftime("%Y-%m-%d")
    else:
        filter_date     = datetime.now().strftime("%d-%m-%Y")
        filter_date_iso = datetime.now().strftime("%Y-%m-%d")

    site_ids = session["site_ids"].split(",")

    with get_db() as db:
        placeholders = ",".join("?" * len(site_ids))

        # Day records
        day_rows = db.execute(
            f"SELECT * FROM weighbridge_records WHERE site_id IN ({placeholders})"
            f" AND date=? ORDER BY id DESC",
            site_ids + [filter_date]).fetchall()

        # Month
        month_str  = datetime.strptime(filter_date, "%d-%m-%Y").strftime("%m-%Y")
        month_rows = db.execute(
            f"SELECT net_weight FROM weighbridge_records"
            f" WHERE site_id IN ({placeholders}) AND date LIKE ?",
            site_ids + [f"%-{month_str}"]).fetchall()

        # Total all-time
        total_trips = db.execute(
            f"SELECT COUNT(*) as c FROM weighbridge_records"
            f" WHERE site_id IN ({placeholders})",
            site_ids).fetchone()["c"]

        # Per-site breakdown for today
        site_breakdown = {}
        for sid in site_ids:
            rows = db.execute(
                "SELECT COUNT(*) as trips, COALESCE(SUM(net_weight),0) as net,"
                " COALESCE(SUM(gross_weight),0) as gross"
                " FROM weighbridge_records WHERE site_id=? AND date=?",
                (sid, filter_date)).fetchone()
            site_breakdown[sid] = dict(rows)

        # 7-day trend
        base_dt = datetime.strptime(filter_date, "%d-%m-%Y")
        trend_labels, trend_data = [], []
        for i in range(6,-1,-1):
            d   = (base_dt - timedelta(days=i)).strftime("%d-%m-%Y")
            lbl = (base_dt - timedelta(days=i)).strftime("%d %b")
            n   = db.execute(
                f"SELECT COALESCE(SUM(net_weight),0) as s"
                f" FROM weighbridge_records WHERE site_id IN ({placeholders}) AND date=?",
                site_ids + [d]).fetchone()["s"]
            trend_labels.append(lbl)
            trend_data.append(round(float(n)/1000, 2))

        # Top parties
        parties = db.execute(
            f"SELECT party_name, SUM(net_weight) as total"
            f" FROM weighbridge_records WHERE site_id IN ({placeholders})"
            f" GROUP BY party_name ORDER BY total DESC LIMIT 5",
            site_ids).fetchall()

    import json
    return render_template("dashboard.html",
        recent=day_rows,
        today_trips=len(day_rows),
        today_net=sum(float(r["net_weight"] or 0) for r in day_rows),
        today_gross=sum(float(r["gross_weight"] or 0) for r in day_rows),
        today_tare=sum(float(r["tare_weight"] or 0) for r in day_rows),
        month_net=sum(float(r["net_weight"] or 0) for r in month_rows),
        total_trips=total_trips,
        site_breakdown=site_breakdown,
        site_ids=site_ids,
        trend_labels=json.dumps(trend_labels),
        trend_data=json.dumps(trend_data),
        parties=parties,
        filter_date=filter_date,
        filter_date_iso=filter_date_iso,
        is_govt=is_govt(),
    )

# ── RECORDS ───────────────────────────────────────────────────────────────────
@app.route("/records")
@login_required
def records():
    site_ids   = session["site_ids"].split(",")
    page       = int(request.args.get("page", 1))
    search     = request.args.get("search","").strip()
    date_from  = request.args.get("date_from","").strip()
    date_to    = request.args.get("date_to","").strip()
    sort_col   = request.args.get("sort","id")
    sort_dir   = request.args.get("dir","desc")
    site_filter = request.args.get("site","all")
    per        = 25

    allowed = {"id","date","vehicle_number","party_name",
               "material","gross_weight","tare_weight","net_weight","site_id"}
    order_col = sort_col if sort_col in allowed else "id"
    order_dir = "ASC" if sort_dir == "asc" else "DESC"

    with get_db() as db:
        placeholders = ",".join("?" * len(site_ids))
        base = f"FROM weighbridge_records WHERE site_id IN ({placeholders})"
        args = list(site_ids)

        # Site filter
        if site_filter != "all" and site_filter in site_ids:
            base = "FROM weighbridge_records WHERE site_id=?"
            args = [site_filter]

        if search:
            base += """ AND (vehicle_number LIKE ? OR party_name LIKE ?
                         OR material LIKE ? OR rfid_tag LIKE ? OR challan_id LIKE ?)"""
            args += [f"%{search}%"]*5

        if date_from:
            try:
                df = datetime.strptime(date_from, "%Y-%m-%d").strftime("%d-%m-%Y")
                base += " AND date >= ?"; args.append(df)
            except ValueError: pass
        if date_to:
            try:
                dt = datetime.strptime(date_to, "%Y-%m-%d").strftime("%d-%m-%Y")
                base += " AND date <= ?"; args.append(dt)
            except ValueError: pass

        total   = db.execute(f"SELECT COUNT(*) as c {base}", args).fetchone()["c"]
        totals  = db.execute(
            f"SELECT COALESCE(SUM(gross_weight),0) as tg,"
            f" COALESCE(SUM(tare_weight),0) as tt,"
            f" COALESCE(SUM(net_weight),0) as tn {base}", args).fetchone()
        rows    = db.execute(
            f"SELECT * {base} ORDER BY {order_col} {order_dir}"
            f" LIMIT ? OFFSET ?",
            args + [per, (page-1)*per]).fetchall()

    pages = (total + per - 1) // per

    def qs(**overrides):
        params = {"search":search,"date_from":date_from,"date_to":date_to,
                  "sort":sort_col,"dir":sort_dir,"site":site_filter}
        params.update(overrides)
        return "&".join(f"{k}={v}" for k,v in params.items() if v)

    return render_template("records.html",
        rows=rows, page=page, pages=pages,
        search=search, date_from=date_from, date_to=date_to,
        sort_col=sort_col, sort_dir=sort_dir,
        site_filter=site_filter, site_ids=site_ids,
        total=total, totals=totals, qs=qs,
        is_govt=is_govt(),
    )

# ── API SYNC (desktop app posts records here) ─────────────────────────────────
@app.route("/api/sync", methods=["POST"])
def api_sync():
    key = request.headers.get("X-License-Key","")
    if not key:
        return jsonify({"ok":False,"error":"No license key"}), 403

    # Simple key check — keys stored as env vars or in DB
    # For now validate against a hardcoded env or any non-empty key
    api_keys = os.environ.get("API_KEYS","CARGO-WB001,CARGO-WB002,CARGO-WB003").split(",")
    site_map  = {
        os.environ.get("KEY_WB001","CARGO-WB001"): "WB001",
        os.environ.get("KEY_WB002","CARGO-WB002"): "WB002",
        os.environ.get("KEY_WB003","CARGO-WB003"): "WB003",
    }
    site_id = site_map.get(key)
    if not site_id:
        return jsonify({"ok":False,"error":"Invalid key"}), 403

    data = request.get_json(force=True) or []
    inserted = 0
    with get_db() as db:
        for r in data:
            ex = db.execute(
                "SELECT id FROM weighbridge_records WHERE challan_id=? AND site_id=?",
                (str(r.get("challan_id","")), site_id)).fetchone()
            if not ex:
                db.execute("""INSERT INTO weighbridge_records
                    (site_id,challan_id,date,vehicle_number,party_name,material,
                     gross_weight,tare_weight,net_weight,rfid_tag,
                     gross_datetime,tare_datetime,net_datetime,slip_type,driver)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (site_id,
                     r.get("challan_id",""), r.get("date",""),
                     r.get("vehicle_number",""), r.get("party_name",""),
                     r.get("material",""),
                     r.get("gross_weight",0), r.get("tare_weight",0),
                     r.get("net_weight",0),  r.get("rfid_tag",""),
                     r.get("gross_datetime",""), r.get("tare_datetime",""),
                     r.get("net_datetime",""),   r.get("slip_type","CHALLAN"),
                     r.get("driver","")))
                inserted += 1
        db.commit()
    return jsonify({"ok":True,"inserted":inserted})

# ── ADMIN ─────────────────────────────────────────────────────────────────────
@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        pw = _hash(request.form["password"])
        with get_db() as db:
            adm = db.execute(
                "SELECT * FROM admins WHERE username=? AND password=?",
                (request.form["username"], pw)).fetchone()
        if adm:
            session["admin_id"]   = adm["id"]
            session["admin_user"] = adm["username"]
            return redirect(url_for("admin_panel"))
        flash("Invalid credentials.", "error")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_id", None)
    return redirect(url_for("admin_login"))

@app.route("/admin")
@admin_required
def admin_panel():
    with get_db() as db:
        users = db.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
        stats = {
            "total_users":   db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"],
            "total_records": db.execute("SELECT COUNT(*) as c FROM weighbridge_records").fetchone()["c"],
            "sites": {}
        }
        for sid in ["WB001","WB002","WB003"]:
            row = db.execute(
                "SELECT COUNT(*) as trips, COALESCE(SUM(net_weight),0) as net"
                " FROM weighbridge_records WHERE site_id=?", (sid,)).fetchone()
            stats["sites"][sid] = dict(row)
    return render_template("admin_panel.html", users=users, stats=stats)

@app.route("/admin/user/add", methods=["POST"])
@admin_required
def admin_add_user():
    name     = request.form["name"]
    email    = request.form["email"].lower()
    password = _hash(request.form["password"])
    role     = request.form["role"]
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO users (name,email,password,role,site_ids) VALUES (?,?,?,?,?)",
                (name, email, password, role, "WB001,WB002,WB003"))
            db.commit()
        flash(f"User '{name}' created.", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/toggle/<int:uid>")
@admin_required
def admin_toggle_user(uid):
    with get_db() as db:
        u = db.execute("SELECT is_active FROM users WHERE id=?", (uid,)).fetchone()
        if u:
            db.execute("UPDATE users SET is_active=? WHERE id=?",
                       (0 if u["is_active"] else 1, uid))
            db.commit()
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/reset_password/<int:uid>", methods=["POST"])
@admin_required
def admin_reset_password(uid):
    new_pw = request.form.get("new_password","")
    if new_pw:
        with get_db() as db:
            db.execute("UPDATE users SET password=? WHERE id=?",
                       (_hash(new_pw), uid))
            db.commit()
        flash("Password reset.", "success")
    return redirect(url_for("admin_panel"))

if __name__ == "__main__":
    init_db()
    app.run(debug=False, host="0.0.0.0",
            port=int(os.environ.get("PORT", 5000)))
