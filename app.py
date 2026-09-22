import os, re, sqlite3, secrets, hashlib, hmac, logging
from pathlib import Path
from datetime import timedelta, datetime, timezone
from functools import wraps
from urllib.parse import urlsplit
import click
from flask import Flask, render_template, request, session, redirect, url_for, flash, abort, g
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from posts import POSTS


def create_app(test_config=None):
    app = Flask(__name__)
    production = os.getenv("APP_ENV") == "production"
    data_dir = Path(os.getenv("DATA_DIR", str(Path(__file__).parent / "instance")))
    data_dir.mkdir(parents=True, exist_ok=True)
    secret = os.getenv("SECRET_KEY")
    if not secret:
        if production:
            raise RuntimeError("Set a random SECRET_KEY before starting production")
        secret_path = data_dir / "session.key"
        if not secret_path.exists():
            secret_path.write_text(secrets.token_hex(32)); secret_path.chmod(0o600)
        secret = secret_path.read_text().strip()
    if len(secret) < 32:
        raise RuntimeError("SECRET_KEY must be at least 32 characters")
    app.config.update(SECRET_KEY=secret, DATABASE=str(data_dir / "analytics.db"),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production, PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
        SESSION_REFRESH_EACH_REQUEST=False, MAX_CONTENT_LENGTH=32*1024,
        CONTACT_PHONE=os.getenv("CONTACT_PHONE", ""), CONTACT_EMAIL=os.getenv("CONTACT_EMAIL", "info@joyfulridesshuttle.co.za"),
        WHATSAPP_NUMBER=re.sub(r"\D", "", os.getenv("WHATSAPP_NUMBER", "")))
    if test_config: app.config.update(test_config)
    # Enable only for the exact number of trusted reverse proxies in your deployment.
    hops = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))
    if hops: app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops)

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"], timeout=10)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA journal_mode=WAL")
        return g.db

    @app.teardown_appcontext
    def close_db(error):
        conn = g.pop("db", None)
        if conn is not None: conn.close()

    with app.app_context():
        db().executescript("""
        CREATE TABLE IF NOT EXISTS owners(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS enquiries(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
          name TEXT NOT NULL, email TEXT, phone TEXT, school TEXT, pickup TEXT, dropoff TEXT, message TEXT,
          status TEXT NOT NULL DEFAULT 'new', source TEXT, medium TEXT, campaign TEXT, visitor TEXT, submission_id TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS page_views(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, path TEXT,
          visitor TEXT, source TEXT, medium TEXT, campaign TEXT);
        CREATE INDEX IF NOT EXISTS pv_date ON page_views(created_at);
        CREATE TABLE IF NOT EXISTS request_errors(id INTEGER PRIMARY KEY, created_at TEXT, path TEXT, status INTEGER);
        CREATE TABLE IF NOT EXISTS rate_events(id INTEGER PRIMARY KEY, created_at INTEGER, bucket TEXT, identity TEXT);
        CREATE INDEX IF NOT EXISTS rate_lookup ON rate_events(bucket,identity,created_at);
        """); db().commit()

    def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    def csrf():
        if "csrf" not in session: session["csrf"] = secrets.token_hex(32)
        return session["csrf"]
    app.jinja_env.globals["csrf_token"] = csrf

    def limited(bucket, maximum, seconds):
        conn=db(); stamp=int(datetime.now(timezone.utc).timestamp())
        identity=hmac.new(app.secret_key.encode(), (request.remote_addr or "unknown").encode(), hashlib.sha256).hexdigest()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM rate_events WHERE created_at < ?", (stamp-86400,))
            count=conn.execute("SELECT COUNT(*) FROM rate_events WHERE bucket=? AND identity=? AND created_at>?",(bucket,identity,stamp-seconds)).fetchone()[0]
            if count >= maximum:
                conn.commit(); return True
            conn.execute("INSERT INTO rate_events(created_at,bucket,identity) VALUES(?,?,?)",(stamp,bucket,identity)); conn.commit()
            return False
        except Exception:
            conn.rollback(); raise

    @app.before_request
    def prepare():
        if request.method == "POST":
            expected=session.get("csrf", ""); provided=request.form.get("csrf_token", "")
            if not expected or not hmac.compare_digest(expected,provided): abort(400, "Form expired. Reload the page and try again.")
        if request.endpoint in {"home","blog","blog_post","soe_blog_alias"}:
            if "visitor" not in session: session["visitor"]=secrets.token_hex(16)
            if request.args.get("utm_source") or request.args.get("utm_campaign"):
                session["attribution"]={k:request.args.get("utm_"+k, "")[:100] for k in ("source","medium","campaign")}
            elif "attribution" not in session:
                ref=urlsplit(request.referrer or "").hostname
                session["attribution"]={"source":ref if ref and ref!=request.host.split(":")[0] else "direct", "medium":"", "campaign":""}

    @app.after_request
    def track(response):
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["X-Frame-Options"]="SAMEORIGIN"
        response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
        response.headers["Cache-Control"]="no-store" if not request.path.startswith("/static/") else "public, max-age=3600"
        if request.path.startswith(("/admin", "/login")): response.headers["X-Robots-Tag"]="noindex, nofollow"
        try:
            if request.method == "GET" and response.status_code >= 400 and not request.path.startswith(("/static/","/admin","/login")):
                db().execute("INSERT INTO request_errors(created_at,path,status) VALUES(?,?,?)",(now(),request.path[:300],response.status_code)); db().commit()
            ua=request.user_agent.string.lower()
            bot=not ua or any(x in ua for x in ("bot","spider","crawler","headless","curl","wget","python-requests"))
            if request.method=="GET" and response.status_code==200 and request.endpoint in {"home","blog","blog_post","soe_blog_alias"} and not bot:
                a=session.get("attribution",{})
                db().execute("INSERT INTO page_views(created_at,path,visitor,source,medium,campaign) VALUES(?,?,?,?,?,?)",
                    (now(),request.path,session.get("visitor"),a.get("source","direct"),a.get("medium",""),a.get("campaign",""))); db().commit()
        except sqlite3.Error:
            app.logger.exception("Analytics write failed")
        return response

    def owner_required(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            if not session.get("owner") or not db().execute("SELECT id FROM owners WHERE id=?",(session["owner"],)).fetchone():
                return redirect(url_for("login"))
            return fn(*args,**kwargs)
        return wrapped

    @app.get("/")
    def home(): return render_template("index.html")
    @app.get("/blog")
    def blog(): return render_template("blog.html",posts=sorted(POSTS,key=lambda x:x["date"],reverse=True))
    @app.get("/soe")
    def soe_blog_alias(): return redirect(url_for("blog"),301)
    @app.get("/blog/<slug>")
    def blog_post(slug):
        post=next((p for p in POSTS if p["slug"]==slug),None)
        if not post: abort(404)
        return render_template("post.html",post=post)

    @app.route("/enquiry/<kind>",methods=["GET","POST"])
    def enquiry(kind):
        if kind not in ("quote","contact"): abort(404)
        values={}; errors=[]
        if request.method=="GET": session["submission_id"]=secrets.token_hex(24)
        else:
            fields=("name","email","phone","school","pickup","dropoff","message")
            values={k:request.form.get(k,"").strip() for k in fields}
            token=request.form.get("submission_id","")
            if token and db().execute("SELECT id FROM enquiries WHERE submission_id=?",(token,)).fetchone(): return redirect(url_for("thank_you"))
            if not token or token!=session.get("submission_id"): abort(400,"Reload the enquiry form before submitting.")
            if request.form.get("website"): abort(400)
            if limited("enquiry",10,3600): abort(429,"Too many submissions. Please try again later.")
            if not 2<=len(values["name"])<=120: errors.append("Enter a name between 2 and 120 characters.")
            if not values["email"] and not values["phone"]: errors.append("Enter an email address or phone number so we can reply.")
            if values["email"] and (len(values["email"])>254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",values["email"])): errors.append("Enter a valid email address.")
            if values["phone"] and (not re.fullmatch(r"[+()0-9 .-]{7,30}",values["phone"]) or len(re.sub(r"\D","",values["phone"]))<7): errors.append("Enter a valid phone number.")
            if any(len(values[k])>160 for k in ("school","pickup","dropoff")): errors.append("School and area fields must be at most 160 characters.")
            if len(values["message"])>3000: errors.append("Keep the message below 3,000 characters.")
            if kind=="quote" and not all(values[k] for k in ("school","pickup","dropoff")): errors.append("Enter the school, pickup area and drop-off area.")
            if kind=="contact" and not values["message"]: errors.append("Enter your message.")
            if not request.form.get("consent"): errors.append("Please agree to be contacted about this enquiry.")
            if not errors:
                a=session.get("attribution",{})
                db().execute("INSERT INTO enquiries(created_at,kind,name,email,phone,school,pickup,dropoff,message,source,medium,campaign,visitor,submission_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now(),kind,*[values[k] for k in fields],a.get("source","direct"),a.get("medium",""),a.get("campaign",""),session.get("visitor"),token)); db().commit()
                session.pop("submission_id",None)
                return redirect(url_for("thank_you"),303)
        return render_template("enquiry.html",kind=kind,values=values,errors=errors),422 if errors else 200

    @app.get("/thank-you")
    def thank_you(): return render_template("thank_you.html")

    @app.route("/login",methods=["GET","POST"])
    def login():
        error=None
        if request.method=="POST":
            if limited("login",5,900): abort(429,"Too many attempts. Try again in 15 minutes.")
            user=db().execute("SELECT * FROM owners WHERE username=?",(request.form.get("username", ""),)).fetchone()
            if user and check_password_hash(user["password_hash"],request.form.get("password","")):
                session.clear(); session["owner"]=user["id"]; session.permanent=True; csrf()
                return redirect(url_for("admin_analytics"))
            error="Username or password is incorrect."
        return render_template("login.html",error=error),401 if error else 200

    @app.post("/logout")
    def logout(): session.clear(); return redirect(url_for("home"))

    @app.get("/admin/analytics")
    @owner_required
    def admin_analytics():
        since=(datetime.now(timezone.utc)-timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
        conn=db()
        views=conn.execute("SELECT COUNT(*),COUNT(DISTINCT visitor) FROM page_views WHERE created_at>=?",(since,)).fetchone()
        leads=conn.execute("SELECT COUNT(*),SUM(status='won'),COUNT(DISTINCT visitor) FROM enquiries WHERE created_at>=?",(since,)).fetchone()
        converted=conn.execute("SELECT COUNT(DISTINCT e.visitor) FROM enquiries e JOIN page_views p ON e.visitor=p.visitor WHERE e.created_at>=? AND p.created_at>=?",(since,since)).fetchone()[0]
        daily=conn.execute("SELECT substr(created_at,1,10) day,COUNT(*) count FROM page_views WHERE created_at>=? GROUP BY day ORDER BY day",(since,)).fetchall()
        campaigns=conn.execute("SELECT source,medium,campaign,COUNT(*) leads,SUM(status='won') won FROM enquiries WHERE created_at>=? GROUP BY source,medium,campaign ORDER BY leads DESC",(since,)).fetchall()
        traffic=conn.execute("SELECT source,medium,campaign,COUNT(*) views FROM page_views WHERE created_at>=? GROUP BY source,medium,campaign ORDER BY views DESC",(since,)).fetchall()
        errors=conn.execute("SELECT status,COUNT(*) count FROM request_errors WHERE created_at>=? GROUP BY status",(since,)).fetchall()
        return render_template("admin/dashboard.html",views=views,leads=leads,conversion=round(100*converted/views[1],1) if views[1] else 0,daily=daily,campaigns=campaigns,traffic=traffic,errors=errors)

    @app.get("/admin/enquiries")
    @owner_required
    def admin_enquiries():
        page=max(1,request.args.get("page",1,type=int)); rows=db().execute("SELECT * FROM enquiries ORDER BY id DESC LIMIT 51 OFFSET ?",((page-1)*50,)).fetchall()
        return render_template("admin/enquiries.html",rows=rows[:50],page=page,has_next=len(rows)>50)

    @app.post("/admin/enquiries/<int:enquiry_id>/status")
    @owner_required
    def enquiry_status(enquiry_id):
        status=request.form.get("status")
        if status not in ("new","contacted","won","lost"): abort(400)
        db().execute("UPDATE enquiries SET status=? WHERE id=?",(status,enquiry_id)); db().commit()
        return redirect(url_for("admin_enquiries"),303)

    @app.cli.command("create-owner")
    @click.option("--username",prompt=True)
    @click.password_option(confirmation_prompt=True)
    def create_owner(username,password):
        if len(password)<14: raise click.ClickException("Use a unique password of at least 14 characters.")
        if not username.strip(): raise click.ClickException("Username is required.")
        if db().execute("SELECT 1 FROM owners WHERE username=?",(username,)).fetchone(): raise click.ClickException("Owner already exists.")
        db().execute("INSERT INTO owners(username,password_hash) VALUES(?,?)",(username,generate_password_hash(password))); db().commit()
        click.echo("Owner created. Password stored as a hash.")
    return app

app=create_app()
if __name__=="__main__": app.run(port=int(os.getenv("PORT","5000")),debug=False)
