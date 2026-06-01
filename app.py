import os, io, base64, requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
import qrcode

app = Flask(__name__)

# ── Database ─────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///jashn.db")
if DATABASE_URL.startswith("postgres://"):          # Render gives postgres://, SQLAlchemy needs postgresql://
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "jashn-jewels-secret-2024")

db = SQLAlchemy(app)

# ── Purity wastage map ────────────────────────────────────────────────────────
PURITY_WASTAGE = {"24k": 100, "22k": 92, "18k": 75, "14k": 59, "9k": 38}

# ── Models ────────────────────────────────────────────────────────────────────
class GoldRate(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    rate_24k     = db.Column(db.Float, nullable=False)   # ₹ per gram
    source       = db.Column(db.String(50))
    fetched_at   = db.Column(db.DateTime, default=datetime.utcnow)

class Product(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    sku             = db.Column(db.String(50), unique=True, nullable=False)
    name            = db.Column(db.String(200), nullable=False)
    purity          = db.Column(db.String(10), nullable=False)
    gross_weight    = db.Column(db.Float, default=0)
    stone_weight    = db.Column(db.Float, default=0)
    misc_weight     = db.Column(db.Float, default=0)
    purchase_premium = db.Column(db.Float, default=4)
    purchase_rate_24k = db.Column(db.Float, nullable=False)
    stone_cost      = db.Column(db.Float, default=0)
    misc_cost       = db.Column(db.Float, default=0)
    sell_driver     = db.Column(db.String(20), default="Wastage")
    sell_wastage    = db.Column(db.Float, default=102)
    sell_making_a   = db.Column(db.Float, default=300)
    sell_making_d   = db.Column(db.Float, default=0.11)
    notes           = db.Column(db.Text, default="")
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def net_gold_weight(self): return max(0, self.gross_weight - self.stone_weight - self.misc_weight)
    @property
    def purity_wastage(self): return PURITY_WASTAGE.get(self.purity, 92)
    @property
    def total_buy_wastage(self): return self.purity_wastage + self.purchase_premium
    @property
    def gold_cost(self): return self.net_gold_weight * (self.purchase_rate_24k * self.total_buy_wastage / 100)
    @property
    def total_cost(self): return self.gold_cost + self.stone_cost + self.misc_cost

    def sell_price_details(self, rate_24k):
        nw=self.net_gold_weight; pr=PURITY_WASTAGE.get(self.purity,92)
        purity_rate=rate_24k*pr/100
        if self.sell_driver=="Wastage": gold_val=nw*(rate_24k*self.sell_wastage/100)
        elif self.sell_driver=="Method A": gold_val=nw*(rate_24k+self.sell_making_a)
        else: gold_val=nw*purity_rate*(1+self.sell_making_d)
        gst=gold_val*0.03; sell_price=gold_val+gst; margin=sell_price-self.total_cost
        eff_wastage=(gold_val/(nw*rate_24k)*100) if (nw and rate_24k) else 0
        making_a_pg=(gold_val/nw-rate_24k) if nw else 0
        making_d_pct=((gold_val-nw*purity_rate)/(nw*purity_rate)) if (nw and purity_rate) else 0
        return {"rate_24k":rate_24k,"purity_rate":purity_rate,"net_weight":nw,"gold_val":gold_val,"gst":gst,"sell_price":sell_price,"margin":margin,"eff_wastage":eff_wastage,"a_gold_val":nw*rate_24k,"a_making_pg":making_a_pg,"a_making_tot":making_a_pg*nw,"d_purity_gold_val":nw*purity_rate,"d_making_pct":making_d_pct*100,"d_making_tot":nw*purity_rate*making_d_pct}

def fetch_gold_price():
    try:
        r1=requests.get("https://metals.live/api/latest",timeout=8)
        xau=r1.json().get("gold")
        if not xau: raise ValueError()
        r2=requests.get("https://api.exchangerate-api.com/v4/latest/USD",timeout=8)
        usd_inr=r2.json()["rates"]["INR"]
        rate=round((xau/31.1035)*usd_inr,2)
    except: return
    with app.app_context():
        db.session.add(GoldRate(rate_24k=rate,source="metals.live"));db.session.commit()

def current_gold_rate():
    row=GoldRate.query.order_by(GoldRate.fetched_at.desc()).first()
    return row.rate_24k if row else None

def current_gold_entry():
    return GoldRate.query.order_by(GoldRate.fetched_at.desc()).first()

def make_qr_b64(url):
    qr=qrcode.QRCode(box_size=6,border=2);qr.add_data(url);qr.make(fit=True)
    img=qr.make_image(fill_color="#7B5E00",back_color="white")
    buf=io.BytesIO();img.save(buf,format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

@app.route("/")
def dashboard():
    return render_template("dashboard.html",gold=current_gold_entry(),total_products=Product.query.count())

@app.route("/gold/update",methods=["POST"])
def gold_update():
    rate=request.form.get("rate",type=float)
    if rate and rate>0: db.session.add(GoldRate(rate_24k=rate,source="Manual"));db.session.commit()
    return redirect(url_for("dashboard"))

@app.route("/gold/fetch",methods=["POST"])
def gold_fetch(): fetch_gold_price();return redirect(url_for("dashboard"))

@app.route("/products")
def products():
    return render_template("products.html",products=Product.query.order_by(Product.created_at.desc()).all(),rate=current_gold_rate())

@app.route("/products/add",methods=["GET","POST"])
def add_product():
    if request.method=="POST":
        f=request.form
        p=Product(sku=f["sku"],name=f["name"],purity=f["purity"],gross_weight=float(f["gross_weight"]),stone_weight=float(f.get("stone_weight",0)),misc_weight=float(f.get("misc_weight",0)),purchase_premium=float(f.get("purchase_premium",4)),purchase_rate_24k=float(f["purchase_rate_24k"]),stone_cost=float(f.get("stone_cost",0)),misc_cost=float(f.get("misc_cost",0)),sell_driver=f.get("sell_driver","Wastage"),sell_wastage=float(f.get("sell_wastage",102)),sell_making_a=float(f.get("sell_making_a",300)),sell_making_d=float(f.get("sell_making_d",11))/100,notes=f.get("notes",""))
        db.session.add(p);db.session.commit()
        return redirect(url_for("products"))
    return render_template("product_form.html",product=None,rate=current_gold_rate() or 15000,purity_map=PURITUE_WASTAGE)

@app.route("/products/<int:pid>/edit",methods=["GET","POST"])
def edit_product(pid):
    p=Product.query.get_or_404(pid)
    if request.method=="POST":
        f=request.form
        p.sku=f["sku"];p.name=f["name"];p.purity=f["purity"];p.gross_weight=float(f["gross_weight"]);p.stone_weight=float(f.get("stone_weight",0));p.misc_weight=float(f.get("misc_weight",0));p.purchase_premium=float(f.get("purchase_premium",4));p.purchase_rate_24k=float(f["purchase_rate_24k"]);p.stone_cost=float(f.get("stone_cost",0));p.misc_cost=float(f.get("misc_cost",0));p.sell_driver=f.get("sell_driver","Wastage");p.sell_wastage=float(f.get("sell_wastage",102));p.sell_making_a=float(f.get("sell_making_a",300));p.sell_making_d=float(f.get("sell_making_d",11))/100;p.notes=f.get("notes","");p.updated_at=datetime.utcnow()
        db.session.commit();return redirect(url_for("products"))
    return render_template("product_form.html",product=p,rate=current_gold_rate() or 15000,purity_map=PURITUE_WASTAGE)

@app.route("/products/<int:pid>/delete",methods=["POST"])
def delete_product(pid):
    p=Product.query.get_or_404(pid);db.session.delete(p);db.session.commit()
    return redirect(url_for("products"))

@app.route("/product/<int:pid>")
def product_detail(pid):
    p=Product.query.get_or_404(pid);rate=current_gold_rate()
    if not rate: return render_template("product_detail.html",product=p,details=None,gold=None)
    details=p.sell_price_details(rate);gold=current_gold_entry()
    scan_url=request.url_root.rstrip("/")+url_for("product_detail",pid=pid)
    return render_template("product_detail.html",product=p,details=details,gold=gold,qr_b64=make_qr_b64(scan_url),scan_url=scan_url)

@app.route("/product/<int:pid>/qr")
def product_qr(pid):
    p=Product.query.get_or_404(pid)
    scan_url=request.url_root.rstrip("/")+url_for("product_detail",pid=pid)
    return render_template("qr_print.html",product=p,qr_b64=make_qr_b64(scan_url),scan_url=scan_url)

@app.route("/scan")
def scan(): return render_template("scan.html")

@app.route("/api/gold")
def api_gold():
    gold=current_gold_entry()
    if gold: return jsonify({"rate":gold.rate_24k,"fetched_at":gold.fetched_at.isoformat(),"source":gold.source})
    return jsonify({"rate":None}),404

@app.route("/api/product/<int:pid>")
def api_product(pid):
    p=Product.query.get_or_404(pid);rate=request.args.get("rate",type=float) or current_gold_rate()
    if not rate: abort(503,"Gold rate unavailable")
    d=p.sell_price_details(rate)
    return jsonify({"sku":p.sku,"name":p.name,"purity":p.purity,"net_weight":p.net_gold_weight,"sell_price":round(d["sell_price"],2),"margin":round(d["margin"],2),"gst":round(d["gst"],2),"method_a":{"gold_value":round(d["a_gold_val"],2),"making_pg":round(d["a_making_pg"],2)),"making_total":round(d["a_making_tot"],2)},"method_d":{"gold_value":round(d["d_purity_gold_val"],2),"making_pct":round(d["d_making_pct"],2)),"making_total":round(d["d_making_tot"],2)}})

def create_app():
    with app.app_context():
        db.create_all()
        if not GoldRate.query.first():
            db.session.add(GoldRate(rate_24k=15000,source="Default seed"));db.session.commit()
    scheduler=BackgroundScheduler()
    scheduler.add_job(fetch_gold_price,"interval",hours=1,id="gold_fetch",replace_existing=True)
    scheduler.start();return app

if __name__=="__main__":
    create_app();app.run(debug=True,use_reloader=False)
