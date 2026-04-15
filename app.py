"""
app.py — Cargo-Ledger Web Dashboard
Trionex Labs

Fixed in this version:
  - admin_panel route now passes 'stats' dict to template (500 error fix)
  - Added missing admin_add_user, admin_toggle_user, admin_reset_password routes
  - Dashboard properly filters by date and site
  - 7-day trend chart data generated correctly
  - Records qs() helper fixed for pagination
  - All routes complete and tested
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

# ── App config ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH      = os.path.join(INSTANCE_DIR, "cargoledger.db")
os.makedirs(INSTANCE_DIR, exist_ok=True)

# ── DB helpers ────────────────────────────────────────────────────────────────
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
        CREATE INDEX IF NOT EXISTS idx_site   ON weighbridge_records(site_id);
        CREATE INDEX IF NOT EXISTS idx_date   ON weighbridge_records(date);
        CREATE INDEX IF NOT EXISTS idx_challan ON weighbridge_records(challan_id);
        """)

        db.execute("INSERT OR IGNORE INTO admins (username,password) VALUES (?,?)",
                   ("admin", _hash("admin@cargo2024")))
        db.execute("""INSERT OR IGNORE INTO users
                      (name,email,password,role,site_ids) VALUES (?,?,?,?,?)""",
                   ("Site Manager","manager@cargo.com",
                    _hash("manager123"),"manager","WB001,WB002,WB003"))
        db.execute("""INSERT OR IGNORE INTO users
                      (name,email,password,role,site_ids) VALUES (?,?,?,?,?)""",
                   ("Govt Officer","govt@cargo.com",
                    _hash("govt1234"),"govt","WB001,WB002,WB003"))
        db.commit()

init_db()

# ── Context processors ────────────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    return {"now": datetime.now()}

@app.template_filter("yesterday")
def yesterday_filter(dt):
    return (dt - timedelta(days=1)).strftime("%Y-%m-%d")

# ── Auth helpers ──────────────────────────────────────────────────────────────
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

def can_edit():
    return session.get("role") in ("manager", "admin")

# ── Public routes ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("landing.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        pw    = _hash(request.form["password"])
        with get_db() as db:
            user = db.execute(
                "SELECT * FROM users WHERE email=? AND password=? AND is_active=1",
                (email, pw)).fetchone()
        if user:
            session["user_id"]   = user["id"]
            session["user_name"] = user["name"]
            session["role"]      = user["role"]
            session["site_ids"]  = user["site_ids"]
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    # Date filter
    date_raw = request.args.get("date", "").strip()
    if date_raw:
        try:
            dt_obj          = datetime.strptime(date_raw, "%Y-%m-%d")
            filter_date     = dt_obj.strftime("%d-%m-%Y")
            filter_date_iso = date_raw
        except ValueError:
            filter_date     = datetime.now().strftime("%d-%m-%Y")
            filter_date_iso = datetime.now().strftime("%Y-%m-%d")
    else:
        filter_date     = datetime.now().strftime("%d-%m-%Y")
        filter_date_iso = datetime.now().strftime("%Y-%m-%d")

    # Site filter
    user_sites  = [s.strip() for s in session.get("site_ids","WB001,WB002,WB003").split(",") if s.strip()]
    site_filter = request.args.get("site", "all")
    if site_filter != "all" and site_filter in user_sites:
        active_sites = [site_filter]
    else:
        site_filter  = "all"
        active_sites = user_sites

    ph = ",".join("?" * len(active_sites))

    with get_db() as db:
        # Today's records
        day_rows = db.execute(
            f"SELECT * FROM weighbridge_records WHERE site_id IN ({ph})"
            f" AND date=? ORDER BY id DESC",
            active_sites + [filter_date]).fetchall()

        # Monthly net
        month_str  = datetime.strptime(filter_date, "%d-%m-%Y").strftime("%m-%Y")
        month_rows = db.execute(
            f"SELECT net_weight FROM weighbridge_records"
            f" WHERE site_id IN ({ph}) AND date LIKE ?",
            active_sites + [f"%-{month_str}"]).fetchall()

        # All-time total
        total_trips = db.execute(
            f"SELECT COUNT(*) as c FROM weighbridge_records WHERE site_id IN ({ph})",
            active_sites).fetchone()["c"]

        # Per-site breakdown
        site_breakdown = {}
        for sid in user_sites:
            r = db.execute(
                "SELECT COUNT(*) as trips,"
                " COALESCE(SUM(net_weight),0) as net,"
                " COALESCE(SUM(gross_weight),0) as gross"
                " FROM weighbridge_records WHERE site_id=? AND date=?",
                (sid, filter_date)).fetchone()
            site_breakdown[sid] = dict(r)

        # 7-day trend
        base_dt = datetime.strptime(filter_date, "%d-%m-%Y")
        trend_labels, trend_data = [], []
        for i in range(6, -1, -1):
            d   = (base_dt - timedelta(days=i)).strftime("%d-%m-%Y")
            lbl = (base_dt - timedelta(days=i)).strftime("%d %b")
            n   = db.execute(
                f"SELECT COALESCE(SUM(net_weight),0) as s"
                f" FROM weighbridge_records WHERE site_id IN ({ph}) AND date=?",
                active_sites + [d]).fetchone()["s"]
            trend_labels.append(lbl)
            trend_data.append(round(float(n) / 1000, 2))

        # Top parties (kept for template compatibility even if chart removed)
        parties = db.execute(
            f"SELECT party_name, SUM(net_weight) as total"
            f" FROM weighbridge_records WHERE site_id IN ({ph})"
            f" GROUP BY party_name ORDER BY total DESC LIMIT 5",
            active_sites).fetchall()

    import json
    return render_template("dashboard.html",
        recent=day_rows,
        today_trips=len(day_rows),
        today_net=sum(float(r["net_weight"]   or 0) for r in day_rows),
        today_gross=sum(float(r["gross_weight"] or 0) for r in day_rows),
        today_tare=sum(float(r["tare_weight"]  or 0) for r in day_rows),
        month_net=sum(float(r["net_weight"] or 0) for r in month_rows),
        total_trips=total_trips,
        site_breakdown=site_breakdown,
        site_ids=user_sites,
        user_sites=user_sites,
        site_filter=site_filter,
        active_sites=active_sites,
        trend_labels=json.dumps(trend_labels),
        trend_data=json.dumps(trend_data),
        parties=parties,
        filter_date=filter_date,
        filter_date_iso=filter_date_iso,
        is_govt=is_govt(),
    )

# ── Records ───────────────────────────────────────────────────────────────────
@app.route("/records")
@login_required
def records():
    user_sites  = [s.strip() for s in session.get("site_ids","WB001,WB002,WB003").split(",") if s.strip()]
    page        = max(1, int(request.args.get("page", 1)))
    search      = request.args.get("search",    "").strip()
    date_from   = request.args.get("date_from", "").strip()
    date_to     = request.args.get("date_to",   "").strip()
    sort_col    = request.args.get("sort", "id")
    sort_dir    = request.args.get("dir",  "desc")
    site_filter = request.args.get("site", "all")
    per_page    = 25

    allowed_cols = {"id","site_id","challan_id","date","vehicle_number",
                    "party_name","material","gross_weight","tare_weight","net_weight"}
    if sort_col not in allowed_cols: sort_col = "id"
    if sort_dir not in {"asc","desc"}: sort_dir = "desc"

    conditions, params = [], []

    if site_filter != "all" and site_filter in user_sites:
        conditions.append("site_id = ?")
        params.append(site_filter)
    else:
        site_filter = "all"
        ph = ",".join("?" * len(user_sites))
        conditions.append(f"site_id IN ({ph})")
        params.extend(user_sites)

    if search:
        like = f"%{search}%"
        conditions.append("""(vehicle_number LIKE ? OR party_name LIKE ?
                               OR material LIKE ? OR rfid_tag LIKE ? OR challan_id LIKE ?)""")
        params.extend([like]*5)

    if date_from:
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d").strftime("%d-%m-%Y")
            conditions.append("date >= ?"); params.append(df)
        except ValueError: pass

    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d").strftime("%d-%m-%Y")
            conditions.append("date <= ?"); params.append(dt)
        except ValueError: pass

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    with get_db() as db:
        total  = db.execute(f"SELECT COUNT(*) as c FROM weighbridge_records {where}",
                            params).fetchone()["c"]
        totals_row = db.execute(
            f"SELECT COALESCE(SUM(gross_weight),0) as tg,"
            f" COALESCE(SUM(tare_weight),0) as tt,"
            f" COALESCE(SUM(net_weight),0) as tn"
            f" FROM weighbridge_records {where}", params).fetchone()
        totals = {"tg": totals_row["tg"], "tt": totals_row["tt"], "tn": totals_row["tn"]}
        rows   = db.execute(
            f"SELECT * FROM weighbridge_records {where}"
            f" ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?",
            params + [per_page, (page-1)*per_page]).fetchall()

    pages = max(1, (total + per_page - 1) // per_page)

    def qs(**overrides):
        p = {"search":search,"date_from":date_from,"date_to":date_to,
             "sort":sort_col,"dir":sort_dir,"site":site_filter}
        p.update(overrides)
        return urlencode({k:v for k,v in p.items() if v})

    return render_template("records.html",
        rows=rows, page=page, pages=pages,
        search=search, date_from=date_from, date_to=date_to,
        sort_col=sort_col, sort_dir=sort_dir,
        site_filter=site_filter, site_ids=user_sites,
        total=total, totals=totals, qs=qs,
        is_govt=is_govt(),
    )

# ── Edit / Delete records ─────────────────────────────────────────────────────
@app.route("/edit-record/<int:rid>", methods=["GET","POST"])
@login_required
def edit_record(rid):
    if is_govt():
        flash("Government accounts have read-only access.", "error")
        return redirect(url_for("records"))
    with get_db() as db:
        rec = db.execute("SELECT * FROM weighbridge_records WHERE id=?", (rid,)).fetchone()
    if not rec:
        flash("Record not found.", "error")
        return redirect(url_for("records"))

    if request.method == "POST":
        try:
            g = float(request.form.get("gross_weight", rec["gross_weight"] or 0))
            t = float(request.form.get("tare_weight",  rec["tare_weight"]  or 0))
        except ValueError:
            flash("Weights must be numeric.", "error")
            return render_template("edit_record.html", rec=rec)
        if g < t:
            flash("Gross weight cannot be less than tare weight.", "error")
            return render_template("edit_record.html", rec=rec)
        net = round(g - t, 2)
        with get_db() as db:
            db.execute("""UPDATE weighbridge_records SET
                date=?, vehicle_number=?, party_name=?, material=?,
                gross_weight=?, tare_weight=?, net_weight=?, rfid_tag=?, driver=?
                WHERE id=?""",
                (request.form.get("date",           rec["date"]),
                 request.form.get("vehicle_number", rec["vehicle_number"]),
                 request.form.get("party_name",     rec["party_name"]),
                 request.form.get("material",        rec["material"]),
                 g, t, net,
                 request.form.get("rfid_tag",        rec["rfid_tag"]),
                 request.form.get("driver",           rec["driver"]),
                 rid))
            db.commit()
        flash(f"Record #{rec['challan_id']} updated.", "success")
        return redirect(url_for("records"))

    return render_template("edit_record.html", rec=rec)

@app.route("/delete-record/<int:rid>", methods=["POST"])
@login_required
def delete_record(rid):
    if is_govt():
        flash("Government accounts have read-only access.", "error")
        return redirect(url_for("records"))
    with get_db() as db:
        rec = db.execute("SELECT challan_id FROM weighbridge_records WHERE id=?", (rid,)).fetchone()
        if rec:
            db.execute("DELETE FROM weighbridge_records WHERE id=?", (rid,))
            db.commit()
            flash(f"Record #{rec['challan_id']} deleted.", "success")
    return redirect(url_for("records"))

# ── API sync ──────────────────────────────────────────────────────────────────
@app.route("/api/sync", methods=["POST"])
def api_sync():
    key = request.headers.get("X-License-Key", "")
    site_map = {
        os.environ.get("KEY_WB001","CARGO-WB001-SECRET"): "WB001",
        os.environ.get("KEY_WB002","CARGO-WB002-SECRET"): "WB002",
        os.environ.get("KEY_WB003","CARGO-WB003-SECRET"): "WB003",
    }
    site_id = site_map.get(key)
    if not site_id:
        return jsonify({"ok":False,"error":"Invalid API key"}), 403

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
                    (site_id,str(r.get("challan_id","")),r.get("date",""),
                     r.get("vehicle_number",""),r.get("party_name",""),r.get("material",""),
                     r.get("gross_weight",0),r.get("tare_weight",0),r.get("net_weight",0),
                     r.get("rfid_tag",""),r.get("gross_datetime",""),r.get("tare_datetime",""),
                     r.get("net_datetime",""),r.get("slip_type","CHALLAN"),r.get("driver","")))
                inserted += 1
        db.commit()
    return jsonify({"ok":True,"inserted":inserted})

# ── Admin ─────────────────────────────────────────────────────────────────────
@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        pw  = _hash(request.form.get("password",""))
        with get_db() as db:
            adm = db.execute(
                "SELECT * FROM admins WHERE username=? AND password=?",
                (request.form.get("username",""), pw)).fetchone()
        if adm:
            session["admin_id"]   = adm["id"]
            session["admin_user"] = adm["username"]
            return redirect(url_for("admin_panel"))
        flash("Invalid credentials.", "error")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_id", None)
    session.pop("admin_user", None)
    return redirect(url_for("admin_login"))

@app.route("/admin")
@admin_required
def admin_panel():
    with get_db() as db:
        users = db.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
        # Build the stats dict the template expects
        stats = {
            "total_users":   db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"],
            "total_records": db.execute("SELECT COUNT(*) as c FROM weighbridge_records").fetchone()["c"],
            "sites": {}
        }
        for sid in ["WB001","WB002","WB003"]:
            row = db.execute(
                "SELECT COUNT(*) as trips, COALESCE(SUM(net_weight),0) as net"
                " FROM weighbridge_records WHERE site_id=?", (sid,)).fetchone()
            stats["sites"][sid] = {"trips": row["trips"], "net": row["net"]}

    return render_template("admin_panel.html", users=users, stats=stats)

@app.route("/admin/user/add", methods=["POST"])
@admin_required
def admin_add_user():
    name  = request.form.get("name","").strip()
    email = request.form.get("email","").strip().lower()
    pw    = request.form.get("password","")
    role  = request.form.get("role","manager")
    if not name or not email or not pw:
        flash("All fields are required.", "error")
        return redirect(url_for("admin_panel"))
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO users (name,email,password,role,site_ids) VALUES (?,?,?,?,?)",
                (name, email, _hash(pw), role, "WB001,WB002,WB003"))
            db.commit()
        flash(f"User '{name}' created.", "success")
    except Exception as e:
        flash(f"Error: {e}", "error")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/toggle/<int:uid>")
@admin_required
def admin_toggle_user(uid):
    with get_db() as db:
        u = db.execute("SELECT is_active,name FROM users WHERE id=?", (uid,)).fetchone()
        if u:
            db.execute("UPDATE users SET is_active=? WHERE id=?",
                       (0 if u["is_active"] else 1, uid))
            db.commit()
            flash(f"User '{u['name']}' {'deactivated' if u['is_active'] else 'activated'}.", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/reset_password/<int:uid>", methods=["POST"])
@admin_required
def admin_reset_password(uid):
    new_pw = request.form.get("new_password","")
    if not new_pw:
        flash("Password cannot be empty.", "error")
        return redirect(url_for("admin_panel"))
    with get_db() as db:
        u = db.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()
        if u:
            db.execute("UPDATE users SET password=? WHERE id=?", (_hash(new_pw), uid))
            db.commit()
            flash(f"Password reset for '{u['name']}'.", "success")
    return redirect(url_for("admin_panel"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
