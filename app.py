"""
app.py  —  Cargo-Ledger Weighbridge Management System
Trionex Labs  |  Phase 8 — PostgreSQL + RST Slip Integration

Key changes from SQLite version:
  • sqlite3 replaced with PostgreSQL via Flask-SQLAlchemy
  • Data persists across Render restarts and redeploys
  • New rst_slips table — daily Google Drive links per site
  • RST slip displayed on dashboard per site card
  • Admin can add / update / delete RST slip links
  • All existing routes, templates, and behaviour preserved
  • Automatic SQLite fallback for local development
"""
import os, hashlib, secrets, json
from datetime import datetime, timedelta, date
from functools import wraps
from urllib.parse import urlencode

from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

# ─────────────────────────────────────────────────────────────────────────────
#  APP + DB CONFIG
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

_db_url = os.environ.get("DATABASE_URL", "")
# Render provides postgres:// — SQLAlchemy needs postgresql://
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
# Local dev fallback
if not _db_url:
    _path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "instance", "cargoledger.db")
    os.makedirs(os.path.dirname(_path), exist_ok=True)
    _db_url = f"sqlite:///{_path}"

app.config["SQLALCHEMY_DATABASE_URI"]        = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"]      = {
    "pool_pre_ping": True,
    "pool_recycle":  280,
}
db = SQLAlchemy(app)

# ─────────────────────────────────────────────────────────────────────────────
#  MODELS
# ─────────────────────────────────────────────────────────────────────────────
class User(db.Model):
    __tablename__ = "users"
    id         = db.Column(db.Integer,     primary_key=True)
    name       = db.Column(db.String(120), nullable=False)
    email      = db.Column(db.String(200), unique=True, nullable=False)
    password   = db.Column(db.String(64),  nullable=False)
    role       = db.Column(db.String(20),  nullable=False, default="manager")
    site_ids   = db.Column(db.Text,        default="WB001,WB002,WB003")
    is_active  = db.Column(db.Boolean,     default=True)
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)

class Admin(db.Model):
    __tablename__ = "admins"
    id       = db.Column(db.Integer,    primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(64), nullable=False)

class WeighbridgeRecord(db.Model):
    __tablename__  = "weighbridge_records"
    __table_args__ = (
        db.Index("idx_wr_site",    "site_id"),
        db.Index("idx_wr_date",    "date"),
        db.Index("idx_wr_challan", "challan_id"),
    )
    id             = db.Column(db.Integer,    primary_key=True)
    site_id        = db.Column(db.String(10), nullable=False, default="WB001")
    challan_id     = db.Column(db.String(50))
    date           = db.Column(db.String(20))   # dd-mm-yyyy (matches desktop app)
    vehicle_number = db.Column(db.String(50))
    party_name     = db.Column(db.String(120))
    material       = db.Column(db.String(120))
    gross_weight   = db.Column(db.Float)
    tare_weight    = db.Column(db.Float)
    net_weight     = db.Column(db.Float)
    rfid_tag       = db.Column(db.String(100))
    gross_datetime = db.Column(db.String(30))
    tare_datetime  = db.Column(db.String(30))
    net_datetime   = db.Column(db.String(30))
    slip_type      = db.Column(db.String(20), default="CHALLAN")
    driver         = db.Column(db.String(100))
    synced_at      = db.Column(db.DateTime,   default=datetime.utcnow)

class RstSlip(db.Model):
    """Daily Google Drive RST slip link — one per site per day."""
    __tablename__  = "rst_slips"
    __table_args__ = (
        db.UniqueConstraint("site_id", "date", name="uq_rst_site_date"),
    )
    id         = db.Column(db.Integer,    primary_key=True)
    site_id    = db.Column(db.String(10), nullable=False)
    date       = db.Column(db.Date,       nullable=False)
    drive_link = db.Column(db.Text,       nullable=False)
    added_at   = db.Column(db.DateTime,   default=datetime.utcnow)

# ─────────────────────────────────────────────────────────────────────────────
#  INIT DB
# ─────────────────────────────────────────────────────────────────────────────
def _hash(pw): return hashlib.sha256(pw.encode()).hexdigest()

def init_db():
    db.create_all()
    if not Admin.query.filter_by(username="admin").first():
        db.session.add(Admin(username="admin", password=_hash("admin@cargo2024")))
    if not User.query.filter_by(email="manager@cargo.com").first():
        db.session.add(User(name="Site Manager", email="manager@cargo.com",
                            password=_hash("manager123"), role="manager",
                            site_ids="WB001,WB002,WB003"))
    if not User.query.filter_by(email="govt@cargo.com").first():
        db.session.add(User(name="Govt Officer", email="govt@cargo.com",
                            password=_hash("govt1234"), role="govt",
                            site_ids="WB001,WB002,WB003"))
    db.session.commit()

with app.app_context():
    try:
        init_db()
    except Exception as e:
        print(f"[WARN] DB init: {e}")

# ─────────────────────────────────────────────────────────────────────────────
#  CONTEXT PROCESSORS
# ─────────────────────────────────────────────────────────────────────────────
@app.context_processor
def inject_globals():
    return {"now": datetime.now()}

@app.template_filter("yesterday")
def yesterday_filter(dt):
    return (dt - timedelta(days=1)).strftime("%Y-%m-%d")

# ─────────────────────────────────────────────────────────────────────────────
#  AUTH
# ─────────────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if "user_id" not in session: return redirect(url_for("login"))
        return f(*a, **kw)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **kw):
        if "admin_id" not in session: return redirect(url_for("admin_login"))
        return f(*a, **kw)
    return w

def is_govt(): return session.get("role") == "govt"

# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index(): return render_template("landing.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(
            email=request.form.get("email","").strip().lower(),
            password=_hash(request.form.get("password","")),
            is_active=True).first()
        if u:
            session.update({"user_id":u.id,"user_name":u.name,
                            "role":u.role,"site_ids":u.site_ids})
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# ─────────────────────────────────────────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    # Date
    dr = request.args.get("date","").strip()
    if dr:
        try:
            filter_date     = datetime.strptime(dr,"%Y-%m-%d").strftime("%d-%m-%Y")
            filter_date_iso = dr
        except ValueError:
            filter_date     = datetime.now().strftime("%d-%m-%Y")
            filter_date_iso = datetime.now().strftime("%Y-%m-%d")
    else:
        filter_date     = datetime.now().strftime("%d-%m-%Y")
        filter_date_iso = datetime.now().strftime("%Y-%m-%d")

    # Sites
    user_sites  = [s.strip() for s in session.get("site_ids","WB001,WB002,WB003").split(",") if s.strip()]
    site_filter = request.args.get("site","all")
    if site_filter != "all" and site_filter in user_sites:
        active_sites = [site_filter]
    else:
        site_filter  = "all"
        active_sites = user_sites

    bq = WeighbridgeRecord.query.filter(WeighbridgeRecord.site_id.in_(active_sites))

    day_rows = bq.filter(WeighbridgeRecord.date == filter_date)\
                 .order_by(WeighbridgeRecord.id.desc()).all()

    month_str = datetime.strptime(filter_date,"%d-%m-%Y").strftime("%m-%Y")
    month_net = (db.session.query(func.coalesce(func.sum(WeighbridgeRecord.net_weight),0))
                 .filter(WeighbridgeRecord.site_id.in_(active_sites),
                         WeighbridgeRecord.date.like(f"%-{month_str}"))
                 .scalar() or 0)

    total_trips = bq.count()

    site_breakdown = {}
    for sid in user_sites:
        rs = WeighbridgeRecord.query.filter_by(site_id=sid)\
               .filter(WeighbridgeRecord.date==filter_date).all()
        site_breakdown[sid] = {
            "trips": len(rs),
            "net":   sum(r.net_weight   or 0 for r in rs),
            "gross": sum(r.gross_weight or 0 for r in rs),
        }

    base_dt = datetime.strptime(filter_date,"%d-%m-%Y")
    trend_labels, trend_data = [], []
    for i in range(6,-1,-1):
        d   = (base_dt - timedelta(days=i)).strftime("%d-%m-%Y")
        lbl = (base_dt - timedelta(days=i)).strftime("%d %b")
        n   = (db.session.query(func.coalesce(func.sum(WeighbridgeRecord.net_weight),0))
               .filter(WeighbridgeRecord.site_id.in_(active_sites),
                       WeighbridgeRecord.date==d).scalar() or 0)
        trend_labels.append(lbl)
        trend_data.append(round(float(n)/1000,2))

    parties = [{"party_name":r.party_name,"total":r.total or 0}
               for r in (db.session.query(
                   WeighbridgeRecord.party_name,
                   func.sum(WeighbridgeRecord.net_weight).label("total"))
               .filter(WeighbridgeRecord.site_id.in_(active_sites))
               .group_by(WeighbridgeRecord.party_name)
               .order_by(func.sum(WeighbridgeRecord.net_weight).desc())
               .limit(5).all())]

    # RST slips for today per site
    today_d = datetime.strptime(filter_date,"%d-%m-%Y").date()
    rst_slips = {}
    for sid in active_sites:
        slip = RstSlip.query.filter_by(site_id=sid, date=today_d).first()
        if slip:
            rst_slips[sid] = slip.drive_link

    return render_template("dashboard.html",
        recent=day_rows,
        today_trips=len(day_rows),
        today_net=sum(r.net_weight   or 0 for r in day_rows),
        today_gross=sum(r.gross_weight or 0 for r in day_rows),
        today_tare=sum(r.tare_weight  or 0 for r in day_rows),
        month_net=float(month_net),
        total_trips=total_trips,
        site_breakdown=site_breakdown,
        site_ids=user_sites, user_sites=user_sites,
        site_filter=site_filter, active_sites=active_sites,
        trend_labels=json.dumps(trend_labels),
        trend_data=json.dumps(trend_data),
        parties=parties,
        filter_date=filter_date, filter_date_iso=filter_date_iso,
        rst_slips=rst_slips,
        is_govt=is_govt(),
    )

# ─────────────────────────────────────────────────────────────────────────────
#  RECORDS
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/records")
@login_required
def records():
    user_sites  = [s.strip() for s in session.get("site_ids","WB001,WB002,WB003").split(",") if s.strip()]
    page        = max(1, int(request.args.get("page",1)))
    search      = request.args.get("search","").strip()
    date_from   = request.args.get("date_from","").strip()
    date_to     = request.args.get("date_to","").strip()
    sort_col    = request.args.get("sort","id")
    sort_dir    = request.args.get("dir","desc")
    site_filter = request.args.get("site","all")
    per_page    = 25

    if site_filter != "all" and site_filter in user_sites:
        active_sites = [site_filter]
    else:
        site_filter  = "all"
        active_sites = user_sites

    q = WeighbridgeRecord.query.filter(WeighbridgeRecord.site_id.in_(active_sites))

    if search:
        like = f"%{search}%"
        q = q.filter(db.or_(
            WeighbridgeRecord.vehicle_number.ilike(like),
            WeighbridgeRecord.party_name.ilike(like),
            WeighbridgeRecord.material.ilike(like),
            WeighbridgeRecord.rfid_tag.ilike(like),
            WeighbridgeRecord.challan_id.ilike(like),
        ))
    if date_from:
        try:
            q = q.filter(WeighbridgeRecord.date >=
                         datetime.strptime(date_from,"%Y-%m-%d").strftime("%d-%m-%Y"))
        except ValueError: pass
    if date_to:
        try:
            q = q.filter(WeighbridgeRecord.date <=
                         datetime.strptime(date_to,"%Y-%m-%d").strftime("%d-%m-%Y"))
        except ValueError: pass

    _smap = {"id":WeighbridgeRecord.id,"date":WeighbridgeRecord.date,
             "vehicle_number":WeighbridgeRecord.vehicle_number,
             "party_name":WeighbridgeRecord.party_name,
             "material":WeighbridgeRecord.material,
             "gross_weight":WeighbridgeRecord.gross_weight,
             "tare_weight":WeighbridgeRecord.tare_weight,
             "net_weight":WeighbridgeRecord.net_weight,
             "site_id":WeighbridgeRecord.site_id,
             "challan_id":WeighbridgeRecord.challan_id}
    col = _smap.get(sort_col, WeighbridgeRecord.id)
    q   = q.order_by(col.asc() if sort_dir=="asc" else col.desc())

    total = q.count()
    agg   = q.with_entities(
        func.coalesce(func.sum(WeighbridgeRecord.gross_weight),0).label("tg"),
        func.coalesce(func.sum(WeighbridgeRecord.tare_weight), 0).label("tt"),
        func.coalesce(func.sum(WeighbridgeRecord.net_weight),  0).label("tn"),
    ).first()
    totals = {"tg":float(agg.tg),"tt":float(agg.tt),"tn":float(agg.tn)}
    pages  = max(1,(total+per_page-1)//per_page)
    rows   = q.offset((page-1)*per_page).limit(per_page).all()

    def qs(**ov):
        p={"search":search,"date_from":date_from,"date_to":date_to,
           "sort":sort_col,"dir":sort_dir,"site":site_filter}
        p.update(ov)
        return urlencode({k:v for k,v in p.items() if v})

    return render_template("records.html",
        rows=rows, page=page, pages=pages,
        search=search, date_from=date_from, date_to=date_to,
        sort_col=sort_col, sort_dir=sort_dir,
        site_filter=site_filter, site_ids=user_sites,
        total=total, totals=totals, qs=qs,
        is_govt=is_govt(),
    )

# ─────────────────────────────────────────────────────────────────────────────
#  EDIT / DELETE
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/edit-record/<int:rid>", methods=["GET","POST"])
@login_required
def edit_record(rid):
    if is_govt():
        flash("Government accounts have read-only access.","error")
        return redirect(url_for("records"))
    rec = WeighbridgeRecord.query.get_or_404(rid)
    if request.method == "POST":
        try:
            g = float(request.form.get("gross_weight", rec.gross_weight or 0))
            t = float(request.form.get("tare_weight",  rec.tare_weight  or 0))
        except ValueError:
            flash("Weights must be numeric.","error")
            return render_template("edit_record.html", rec=rec)
        if g < t:
            flash("Gross weight cannot be less than tare weight.","error")
            return render_template("edit_record.html", rec=rec)
        rec.date           = request.form.get("date",           rec.date)
        rec.vehicle_number = request.form.get("vehicle_number", rec.vehicle_number)
        rec.party_name     = request.form.get("party_name",     rec.party_name)
        rec.material       = request.form.get("material",       rec.material)
        rec.rfid_tag       = request.form.get("rfid_tag",       rec.rfid_tag)
        rec.driver         = request.form.get("driver",         rec.driver)
        rec.gross_weight   = g
        rec.tare_weight    = t
        rec.net_weight     = round(g-t,2)
        db.session.commit()
        flash(f"Record #{rec.challan_id} updated.","success")
        return redirect(url_for("records"))
    return render_template("edit_record.html", rec=rec)

@app.route("/delete-record/<int:rid>", methods=["POST"])
@login_required
def delete_record(rid):
    if is_govt():
        flash("Government accounts have read-only access.","error")
        return redirect(url_for("records"))
    rec = WeighbridgeRecord.query.get_or_404(rid)
    cid = rec.challan_id
    db.session.delete(rec)
    db.session.commit()
    flash(f"Record #{cid} deleted.","success")
    return redirect(url_for("records"))

# ─────────────────────────────────────────────────────────────────────────────
#  API SYNC
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/sync", methods=["POST"])
def api_sync():
    key = request.headers.get("X-License-Key","")
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
    for r in data:
        cid = str(r.get("challan_id",""))
        if cid and not WeighbridgeRecord.query.filter_by(challan_id=cid,site_id=site_id).first():
            db.session.add(WeighbridgeRecord(
                site_id=site_id, challan_id=cid,
                date=r.get("date",""), vehicle_number=r.get("vehicle_number",""),
                party_name=r.get("party_name",""), material=r.get("material",""),
                gross_weight=r.get("gross_weight",0), tare_weight=r.get("tare_weight",0),
                net_weight=r.get("net_weight",0), rfid_tag=r.get("rfid_tag",""),
                gross_datetime=r.get("gross_datetime",""), tare_datetime=r.get("tare_datetime",""),
                net_datetime=r.get("net_datetime",""), slip_type=r.get("slip_type","CHALLAN"),
                driver=r.get("driver","")))
            inserted += 1
    db.session.commit()
    return jsonify({"ok":True,"inserted":inserted})

# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        adm = Admin.query.filter_by(
            username=request.form.get("username",""),
            password=_hash(request.form.get("password",""))).first()
        if adm:
            session["admin_id"]   = adm.id
            session["admin_user"] = adm.username
            return redirect(url_for("admin_panel"))
        flash("Invalid credentials.","error")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_id",None); session.pop("admin_user",None)
    return redirect(url_for("admin_login"))

@app.route("/admin")
@admin_required
def admin_panel():
    users = User.query.order_by(User.id.desc()).all()
    stats = {
        "total_users":   User.query.count(),
        "total_records": WeighbridgeRecord.query.count(),
        "sites": {}
    }
    for sid in ["WB001","WB002","WB003"]:
        rs = WeighbridgeRecord.query.filter_by(site_id=sid).all()
        stats["sites"][sid] = {"trips":len(rs),"net":sum(r.net_weight or 0 for r in rs)}
    recent_slips = RstSlip.query.order_by(RstSlip.date.desc(),RstSlip.site_id).limit(30).all()
    return render_template("admin_panel.html",
        users=users, stats=stats, recent_slips=recent_slips)

@app.route("/admin/user/add", methods=["POST"])
@admin_required
def admin_add_user():
    name  = request.form.get("name","").strip()
    email = request.form.get("email","").strip().lower()
    pw    = request.form.get("password","")
    role  = request.form.get("role","manager")
    if not name or not email or not pw:
        flash("All fields are required.","error")
        return redirect(url_for("admin_panel"))
    if User.query.filter_by(email=email).first():
        flash(f"Email {email} already exists.","error")
        return redirect(url_for("admin_panel"))
    db.session.add(User(name=name,email=email,password=_hash(pw),
                        role=role,site_ids="WB001,WB002,WB003"))
    db.session.commit()
    flash(f"User '{name}' created.","success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/toggle/<int:uid>")
@admin_required
def admin_toggle_user(uid):
    u = User.query.get_or_404(uid)
    u.is_active = not u.is_active
    db.session.commit()
    flash(f"User '{u.name}' {'activated' if u.is_active else 'deactivated'}.","success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/reset_password/<int:uid>", methods=["POST"])
@admin_required
def admin_reset_password(uid):
    pw = request.form.get("new_password","")
    if not pw:
        flash("Password cannot be empty.","error")
        return redirect(url_for("admin_panel"))
    u = User.query.get_or_404(uid)
    u.password = _hash(pw)
    db.session.commit()
    flash(f"Password reset for '{u.name}'.","success")
    return redirect(url_for("admin_panel"))

# ── RST Slip routes ───────────────────────────────────────────────────────────
@app.route("/admin/rst/add", methods=["POST"])
@admin_required
def admin_add_rst():
    site_id    = request.form.get("site_id","").strip()
    date_str   = request.form.get("date","").strip()
    drive_link = request.form.get("drive_link","").strip()
    if not site_id or not date_str or not drive_link:
        flash("Site, date, and Google Drive link are all required.","error")
        return redirect(url_for("admin_panel"))
    if not drive_link.startswith("http"):
        flash("Drive link must be a valid URL starting with https://","error")
        return redirect(url_for("admin_panel"))
    try:
        slip_date = datetime.strptime(date_str,"%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date format.","error")
        return redirect(url_for("admin_panel"))
    existing = RstSlip.query.filter_by(site_id=site_id,date=slip_date).first()
    if existing:
        existing.drive_link = drive_link
        existing.added_at   = datetime.utcnow()
        flash(f"RST slip updated for {site_id} on {date_str}.","success")
    else:
        db.session.add(RstSlip(site_id=site_id,date=slip_date,drive_link=drive_link))
        flash(f"RST slip added for {site_id} on {date_str}.","success")
    db.session.commit()
    return redirect(url_for("admin_panel"))

@app.route("/admin/rst/delete/<int:rid>")
@admin_required
def admin_delete_rst(rid):
    slip = RstSlip.query.get_or_404(rid)
    db.session.delete(slip)
    db.session.commit()
    flash("RST slip link deleted.","success")
    return redirect(url_for("admin_panel"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
