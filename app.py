"""Flask application — reselling order management with multi-user auth."""
import os
import io
import csv
import uuid
import functools
from datetime import datetime, timedelta, timezone

import jwt
from flask import Flask, request, jsonify, send_from_directory, send_file, g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

import config
import database as db
from ocr_service import ocr_image

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
app.secret_key = config.JWT_SECRET
CORS(app)

os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
db.init_db()

# ── JWT helpers ────────────────────────────────────────────────────────────

JWT_SECRET = config.JWT_SECRET
JWT_ALGO = "HS256"
JWT_EXPIRY_HOURS = 24 * 30  # 30 days


def _make_token(user_id: int) -> str:
    payload = {
        "uid": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def _decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return payload["uid"]
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError):
        return None


def require_auth(f):
    """Decorator: extract user_id from Authorization header, set g.user_id."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        uid = _decode_token(token)
        if uid is None:
            return jsonify({"ok": False, "error": "请先登录"}), 401
        g.user_id = uid
        return f(*args, **kwargs)
    return wrapper


# ── Helpers ────────────────────────────────────────────────────────────────

def allowed_file(filename: str) -> bool:
    return "." in filename and \
           filename.rsplit(".", 1)[1].lower() in config.ALLOWED_EXTENSIONS


def _json_ok(data, status=200):
    return jsonify({"ok": True, "data": data}), status


def _json_err(msg, status=400):
    return jsonify({"ok": False, "error": msg}), status


# ── Serve SPA ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ── Auth ───────────────────────────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True)
    if not data:
        return _json_err("请提供用户名和密码", 400)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if len(username) < 2:
        return _json_err("用户名至少2个字符", 400)
    if len(password) < 4:
        return _json_err("密码至少4个字符", 400)

    existing = db.get_user_by_username(username)
    if existing:
        return _json_err("用户名已存在", 409)

    pw_hash = generate_password_hash(password)
    uid = db.create_user(username, pw_hash)
    if uid is None:
        return _json_err("注册失败，请重试", 500)

    token = _make_token(uid)
    return _json_ok({"token": token, "user": {"id": uid, "username": username}}, 201)


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True)
    if not data:
        return _json_err("请提供用户名和密码", 400)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user = db.get_user_by_username(username)
    if not user or not check_password_hash(user["password_hash"], password):
        return _json_err("用户名或密码错误", 401)

    token = _make_token(user["id"])
    return _json_ok({"token": token, "user": {"id": user["id"], "username": user["username"]}})


@app.route("/api/auth/me")
@require_auth
def auth_me():
    user = db.get_user_by_id(g.user_id)
    if not user:
        return _json_err("用户不存在", 404)
    return _json_ok(user)


# ── Order CRUD (all require auth) ──────────────────────────────────────────

@app.route("/api/orders", methods=["GET"])
@require_auth
def list_orders():
    page = request.args.get("page", 1, type=int)
    limit = request.args.get("limit", 20, type=int)
    status = request.args.get("status") or None
    platform = request.args.get("platform") or None
    keyword = request.args.get("keyword") or None
    rows, total = db.list_orders(g.user_id, page, limit, status, platform, keyword)
    return _json_ok({
        "orders": rows, "total": total, "page": page,
        "limit": limit, "total_pages": max(1, (total + limit - 1) // limit),
    })


@app.route("/api/orders", methods=["POST"])
@require_auth
def create_order():
    data = request.form.to_dict()
    file = request.files.get("image")

    image_file = None
    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit(".", 1)[1].lower()
        image_file = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(config.UPLOAD_FOLDER, image_file))

    for key in ("expense", "received", "fee"):
        if key in data and data[key]:
            try:
                data[key] = float(data[key])
            except (ValueError, TypeError):
                data[key] = None
        elif key in data:
            data[key] = None
    data["image_file"] = image_file

    try:
        oid = db.create_order(g.user_id, data)
        order = db.get_order(g.user_id, oid)
        return _json_ok(order, 201)
    except Exception as e:
        return _json_err(str(e), 500)


@app.route("/api/orders/<int:order_id>", methods=["GET"])
@require_auth
def get_order(order_id):
    order = db.get_order(g.user_id, order_id)
    if not order:
        return _json_err("订单不存在", 404)
    return _json_ok(order)


@app.route("/api/orders/<int:order_id>", methods=["PUT"])
@require_auth
def update_order(order_id):
    order = db.get_order(g.user_id, order_id)
    if not order:
        return _json_err("订单不存在", 404)
    data = request.get_json(silent=True) or request.form.to_dict()
    if not data:
        return _json_err("无更新数据", 400)
    for key in ("expense", "received", "fee"):
        if key in data and data[key] not in (None, "", "null"):
            try:
                data[key] = float(data[key])
            except (ValueError, TypeError):
                data[key] = None
        elif key in data:
            data[key] = None
    try:
        db.update_order(g.user_id, order_id, data)
        return _json_ok(db.get_order(g.user_id, order_id))
    except Exception as e:
        return _json_err(str(e), 500)


@app.route("/api/orders/<int:order_id>", methods=["DELETE"])
@require_auth
def delete_order(order_id):
    image_file = db.delete_order(g.user_id, order_id)
    if image_file is None:
        return _json_err("订单不存在", 404)
    if image_file:
        img_path = os.path.join(config.UPLOAD_FOLDER, image_file)
        if os.path.exists(img_path):
            os.remove(img_path)
    return _json_ok({"deleted": order_id})


# ── OCR ────────────────────────────────────────────────────────────────────

@app.route("/api/ocr", methods=["POST"])
def ocr_upload():
    """OCR does NOT require auth — it's stateless image processing."""
    file = request.files.get("image")
    if not file or not file.filename:
        return _json_err("请上传图片", 400)
    if not allowed_file(file.filename):
        return _json_err("不支持的图片格式", 400)

    ext = file.filename.rsplit(".", 1)[1].lower()
    temp_name = f"_ocr_{uuid.uuid4().hex}.{ext}"
    temp_path = os.path.join(config.UPLOAD_FOLDER, temp_name)
    file.save(temp_path)

    try:
        result = ocr_image(temp_path)
        return _json_ok(result)
    except Exception as e:
        return _json_err(f"OCR 识别失败: {str(e)}", 500)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# ── Statistics ─────────────────────────────────────────────────────────────

@app.route("/api/stats/overview")
@require_auth
def stats_overview():
    return _json_ok(db.stats_overview(g.user_id))


@app.route("/api/stats/monthly")
@require_auth
def stats_monthly():
    return _json_ok(db.stats_monthly(g.user_id))


@app.route("/api/stats/platform")
@require_auth
def stats_platform():
    return _json_ok(db.stats_platform(g.user_id))


@app.route("/api/stats/pending-received")
@require_auth
def stats_pending():
    return _json_ok(db.stats_pending_received(g.user_id))


# ── Export ─────────────────────────────────────────────────────────────────

@app.route("/api/export/csv")
@require_auth
def export_csv():
    rows = db.export_csv_data(g.user_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "型号", "码数", "平台", "支出", "进货日期",
        "到手金额", "卖出日期", "手续费", "利润", "状态", "备注", "创建时间",
    ])
    for r in rows:
        writer.writerow([
            r["id"], r["model"], r["size"], r["platform"], r["expense"],
            r["order_date"], r["received"], r["sell_date"], r["fee"],
            r["profit"], r["status"], r["note"], r["created_at"],
        ])
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        mimetype="text/csv", as_attachment=True,
        download_name=f"orders_{datetime.now().strftime('%Y%m%d')}.csv",
    )


# ── Uploaded images ────────────────────────────────────────────────────────

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(config.UPLOAD_FOLDER, filename)


# ── SPA fallback (MUST be last — catches all non-API, non-file routes) ──────

@app.route("/<path:path>")
def static_files(path):
    full = os.path.join("static", path)
    if os.path.isfile(full):
        return send_from_directory("static", path)
    return send_from_directory("static", "index.html")


# ── Entry ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n  🧱 搬砖记账系统启动")
    print(f"  ───────────────────────────────")
    print(f"  本地访问:  http://127.0.0.1:{config.PORT}")
    print(f"  ───────────────────────────────\n")
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
