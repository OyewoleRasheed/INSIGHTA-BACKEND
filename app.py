import os
import csv
import secrets
import hashlib
import base64
from io import StringIO
import logging
from datetime import datetime, timezone, timedelta
from functools import wraps
from urllib.parse import urlencode
from werkzeug.middleware.proxy_fix import ProxyFix

from flask import Flask, jsonify, request, g, Response, make_response, redirect
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import uuid6
import jwt
from database import get_db_connection, create_profiles_table, create_users_table, create_refresh_tokens_table

from nlp_parser import parse_query

# ---------------------------------------------------------------------------
# App Initialization & Config
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.json.sort_keys = False

JWT_SECRET = os.environ.get("JWT_SECRET", "super-secret-change-in-prod")
GITHUB_AUTH_BASE = os.environ.get("GITHUB_AUTH_BASE", "https://github.com/login/oauth/authorize")
GITHUB_TOKEN_URL = os.environ.get("GITHUB_TOKEN_URL", "https://github.com/login/oauth/access_token")
GITHUB_USER_URL  = os.environ.get("GITHUB_USER_URL", "https://api.github.com/user")
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "your_client_id")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "your_client_secret")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
ACCESS_TOKEN_MINUTES = int(os.environ.get("ACCESS_TOKEN_MINUTES", 3))
REFRESH_TOKEN_MINUTES = int(os.environ.get("REFRESH_TOKEN_MINUTES", 5)) 

# Allowed CORS origins
CORS(app,
     resources={r"/*": {"origins": [FRONTEND_URL, "http://localhost:3000", "http://localhost:5173"]}},
     supports_credentials=True, 
     allow_headers=["Content-Type", "Authorization", "X-Client-Type", "X-CSRF-Token", "X-API-Version"],
     methods=["GET", "POST", "DELETE", "OPTIONS"])

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri="memory://"
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

create_profiles_table()
create_users_table()
create_refresh_tokens_table()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_GENDERS    = {"male", "female"}
VALID_AGE_GROUPS = {"child", "teenager", "adult", "senior"}
VALID_SORT_BY    = {"age", "created_at", "gender_probability"}
VALID_ORDERS     = {"asc", "desc"}
MAX_LIMIT        = 50

COUNTRY_NAMES = {
    "NG": "Nigeria", "US": "United States", "GB": "United Kingdom",
    "GH": "Ghana", "KE": "Kenya", "ZA": "South Africa", "IN": "India",
    "CA": "Canada", "AU": "Australia", "DE": "Germany", "FR": "France",
    "BR": "Brazil", "MX": "Mexico", "JP": "Japan", "CN": "China",
    "EG": "Egypt", "ET": "Ethiopia", "TZ": "Tanzania", "UG": "Uganda",
}

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
@app.before_request
def log_request_info():
    app.logger.info(f"{request.method} {request.path} ip={request.remote_addr} ua={request.user_agent.string[:60]}")

@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# ---------------------------------------------------------------------------
# Helper Utilities
# ---------------------------------------------------------------------------
def classify_age(age: int) -> str:
    if age <= 12: return "child"
    elif age <= 19: return "teenager"
    elif age <= 59: return "adult"
    else: return "senior"

def get_country_name(code: str) -> str:
    return COUNTRY_NAMES.get(code.upper(), code)

def build_filter_query(args: dict):
    conditions = ["1=1"]
    params = []
    for field, col, cast in [
        ("gender", "LOWER(gender) = ?", None),
        ("age_group", "LOWER(age_group) = ?", None),
        ("country_id", "UPPER(country_id) = ?", None),
    ]:
        val = args.get(field)
        if val:
            conditions.append(col)
            params.append(val)

    if args.get("min_age") is not None:
        conditions.append("age >= ?")
        params.append(int(args["min_age"]))
    if args.get("max_age") is not None:
        conditions.append("age <= ?")
        params.append(int(args["max_age"]))
    if args.get("min_gender_probability") is not None:
        conditions.append("gender_probability >= ?")
        params.append(float(args["min_gender_probability"]))
    if args.get("min_country_probability") is not None:
        conditions.append("country_probability >= ?")
        params.append(float(args["min_country_probability"]))

    return " AND ".join(conditions), params

def paginate_query(base_query, params, sort_by, order, page, limit):
    sort_by = sort_by if sort_by in VALID_SORT_BY else "created_at"
    order   = order   if order   in VALID_ORDERS  else "asc"

    conn = get_db_connection()
    cur  = conn.cursor()

    cur.execute(f"SELECT COUNT(*) as cnt FROM ({base_query})", params)
    total = cur.fetchone()["cnt"]

    full_query = f"{base_query} ORDER BY {sort_by} {order.upper()} LIMIT ? OFFSET ?"
    cur.execute(full_query, params + [limit, (page - 1) * limit])
    rows = cur.fetchall()
    conn.close()
    return rows, total

def profile_to_dict(p) -> dict:
    return {
        "id": p["id"], "name": p["name"], "gender": p["gender"],
        "gender_probability": p["gender_probability"], "age": p["age"],
        "age_group": p["age_group"], "country_id": p["country_id"],
        "country_name": p["country_name"], "country_probability": p["country_probability"],
        "created_at": p["created_at"],
    }

def parse_int_param(val, name, default=None, min_val=None, max_val=None):
    if val is None: return default, None
    try: v = int(val)
    except (ValueError, TypeError):
        return None, ({"status": "error", "message": f"'{name}' must be an integer"}, 422)
    if min_val is not None and v < min_val:
        return None, ({"status": "error", "message": f"'{name}' must be >= {min_val}"}, 400)
    if max_val is not None and v > max_val: v = max_val
    return v, None

def parse_float_param(val, name):
    if val is None: return None, None
    try: return float(val), None
    except (ValueError, TypeError):
        return None, ({"status": "error", "message": f"'{name}' must be a number"}, 422)

def get_pagination_fields(page, limit, total):
    total_pages = max(1, (total + limit - 1) // limit)
    
    def make_url(p):
        if p < 1 or p > total_pages: return None
        args = request.args.copy()
        args['page'] = p
        args['limit'] = limit
        return f"{request.path}?{urlencode(args)}"

    return {
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": {
            "self": make_url(page),
            "next": make_url(page + 1) if page < total_pages else None,
            "prev": make_url(page - 1) if page > 1 else None
        }
    }

def issue_tokens(user_id: str, role: str):
    now = datetime.now(timezone.utc)
    access_token = jwt.encode({
        "user_id": user_id, "role": role,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
        "iat": now, "jti": secrets.token_hex(16),
    }, JWT_SECRET, algorithm="HS256")

    refresh_jti = secrets.token_hex(32)
    refresh_token = jwt.encode({
        "user_id": user_id, "jti": refresh_jti,
        "exp": now + timedelta(minutes=REFRESH_TOKEN_MINUTES),
        "iat": now,
    }, JWT_SECRET, algorithm="HS256")
    
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO refresh_tokens (token, user_id, expires_at, revoked) VALUES (?,?,?,0)",
        (refresh_jti, user_id, (now + timedelta(minutes=REFRESH_TOKEN_MINUTES)).isoformat())
    )
    conn.commit()
    conn.close()
    return access_token, refresh_token

# ---------------------------------------------------------------------------
# Auth Decorators
# ---------------------------------------------------------------------------
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get("access_token") # Web
        if not token:
            auth_header = request.headers.get("Authorization", "") # CLI
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ", 1)[1]
        if not token:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            
            conn = get_db_connection()
            user_row = conn.execute("SELECT is_active FROM users WHERE id = ?", (payload["user_id"],)).fetchone()
            conn.close()
            
            if not user_row or not user_row["is_active"]:
                return jsonify({"status": "error", "message": "Account is disabled"}), 403
                
            g.user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({"status": "error", "message": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"status": "error", "message": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated

def require_role(*roles):
    def decorator(f):
        @wraps(f)
        def inner(*args, **kwargs):
            user_role = g.user.get("role", "")
            if user_role not in roles and user_role != "admin":
                return jsonify({"status": "error", "message": "Forbidden"}), 403
            return f(*args, **kwargs)
        return inner
    return decorator

def csrf_protect(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        client_type = request.headers.get("X-Client-Type", "web")
        if client_type == "cli":
            return f(*args, **kwargs)
        csrf_cookie  = request.cookies.get("csrf_token", "")
        csrf_header  = request.headers.get("X-CSRF-Token", "")
        if not csrf_cookie or not secrets.compare_digest(csrf_cookie, csrf_header):
            return jsonify({"status": "error", "message": "CSRF validation failed"}), 403
        return f(*args, **kwargs)
    return decorated

def require_version(version="1"):
    def decorator(f):
        @wraps(f)
        def inner(*args, **kwargs):
            if request.headers.get("X-API-Version") != version:
                return jsonify({"status": "error", "message": f"API version {version} required via X-API-Version header"}), 400
            return f(*args, **kwargs)
        return inner
    return decorator

# ---------------------------------------------------------------------------
# Auth Routes (UNVERSIONED, standard paths)
# ---------------------------------------------------------------------------

@app.route("/auth/github", methods=["GET"])
@limiter.limit("10 per 5 seconds")
def github_login():
    """Initiates the GitHub OAuth PKCE flow."""
    state = secrets.token_urlsafe(16)
    code_verifier = secrets.token_urlsafe(32)
    
    # Generate PKCE code challenge
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('ascii')).digest()
    ).decode('ascii').rstrip('=')

    params = {
        "client_id": GITHUB_CLIENT_ID,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": "read:user user:email",
    }
    
    url = f"{GITHUB_AUTH_BASE}?{urlencode(params)}"
    
    # Redirect the browser to GitHub
    resp = make_response(redirect(url))
    resp.set_cookie("oauth_state", state, httponly=True, secure=True, samesite="None", max_age=600)
    resp.set_cookie("code_verifier", code_verifier, httponly=True, secure=True, samesite="None", max_age=600)
    return resp


@app.route("/auth/github/callback", methods=["GET", "POST"])
def github_callback():
    client_type = request.headers.get("X-Client-Type", "web")
    
    # Handle both POST (CLI API call) and GET (Browser Redirect)
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        code = data.get("code")
        state = data.get("state")
        code_verifier = data.get("code_verifier")
    else:
        code = request.args.get("code")
        state = request.args.get("state")
        # Extract cookies set in /auth/github
        code_verifier = request.cookies.get("code_verifier")
        expected_state = request.cookies.get("oauth_state")
        
        # Strict state validation for browser flow
        if expected_state and state != expected_state:
            return jsonify({"status": "error", "message": "Invalid state"}), 400

    # 1. Strict Requirement: Reject missing code/state
    if not code:
        return jsonify({"status": "error", "message": "Missing code"}), 400
    if not state:
        return jsonify({"status": "error", "message": "Missing state"}), 400

    # 2. Token Exchange
    token_payload = {
        "client_id": GITHUB_CLIENT_ID,
        "client_secret": GITHUB_CLIENT_SECRET,
        "code": code,
    }
    if code_verifier:
        token_payload["code_verifier"] = code_verifier

    token_resp = requests.post(
        GITHUB_TOKEN_URL,
        headers={"Accept": "application/json"},
        data=token_payload,
        timeout=10,
    ).json()

    gh_token = token_resp.get("access_token")
    if not gh_token:
        error_msg = token_resp.get("error_description", "GitHub authentication failed")
        return jsonify({"status": "error", "message": error_msg}), 401

    user_resp = requests.get(
        GITHUB_USER_URL,
        headers={"Authorization": f"Bearer {gh_token}"},
        timeout=10,
    ).json()

    github_id = str(user_resp.get("id"))
    username  = user_resp.get("login", "unknown")

    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, role FROM users WHERE github_id = ?", (github_id,))
    user = cursor.fetchone()

    if not user:
        # Check how many users exist
        cursor.execute("SELECT COUNT(*) as cnt FROM users")
        user_count = cursor.fetchone()["cnt"]
        
        user_id = str(uuid6.uuid7())
        # Make the very first user an admin, everyone else an analyst
        role    = "admin" if user_count == 0 else "analyst" 
        
        cursor.execute(
            "INSERT INTO users (id, github_id, username, role, created_at) VALUES (?,?,?,?,?)",
            (user_id, github_id, username, role, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    else:
        user_id = user["id"]
        role    = user["role"]
    conn.close()

    access_token, refresh_token = issue_tokens(user_id, role)

    if client_type == "cli" or request.method == "POST":
        return jsonify({
            "status": "success",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": {"id": user_id, "username": username, "role": role},
        }), 200

    # Web Flow: Redirect back to frontend dashboard with HTTP-only cookies
    csrf_token = secrets.token_hex(32)
    resp = make_response(redirect(f"{FRONTEND_URL}/dashboard")) 
    
    resp.set_cookie("access_token", access_token, httponly=True, secure=True, samesite="None", max_age=ACCESS_TOKEN_MINUTES * 60)
    resp.set_cookie("refresh_token", refresh_token, httponly=True, secure=True, samesite="None", max_age=REFRESH_TOKEN_MINUTES * 60)
    resp.set_cookie("csrf_token", csrf_token, httponly=False, secure=True, samesite="None", max_age=ACCESS_TOKEN_MINUTES * 60)
    
    return resp


@app.route("/auth/refresh", methods=["POST"])
def refresh_tokens_route():
    client_type = request.headers.get("X-Client-Type", "web")

    if client_type == "cli":
        data = request.get_json(silent=True) or {}
        raw_refresh = data.get("refresh_token")
    else:
        raw_refresh = request.cookies.get("refresh_token")

    if not raw_refresh:
        return jsonify({"status": "error", "message": "No refresh token"}), 401

    try:
        payload = jwt.decode(raw_refresh, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return jsonify({"status": "error", "message": "Refresh token expired"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"status": "error", "message": "Invalid refresh token"}), 401

    jti     = payload.get("jti")
    user_id = payload.get("user_id")

    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT revoked FROM refresh_tokens WHERE token = ?", (jti,))
    row = cursor.fetchone()

    if not row or row["revoked"]:
        conn.close()
        return jsonify({"status": "error", "message": "Refresh token revoked"}), 401

    conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE token = ?", (jti,))
    conn.commit()

    cursor.execute("SELECT role FROM users WHERE id = ?", (user_id,))
    user_row = cursor.fetchone()
    conn.close()

    if not user_row:
        return jsonify({"status": "error", "message": "User not found"}), 404

    access_token, new_refresh = issue_tokens(user_id, user_row["role"])

    if client_type == "cli":
        return jsonify({"access_token": access_token, "refresh_token": new_refresh}), 200

    resp = make_response(jsonify({"status": "success", "message": "Tokens refreshed"}))
    resp.set_cookie("access_token", access_token, httponly=True, secure=True, samesite="None", max_age=ACCESS_TOKEN_MINUTES * 60)
    resp.set_cookie("refresh_token", new_refresh, httponly=True, secure=True, samesite="None", max_age=REFRESH_TOKEN_MINUTES * 60)
    return resp, 200


@app.route("/auth/logout", methods=["POST"])
@require_auth
def logout():
    client_type = request.headers.get("X-Client-Type", "web")
    raw_refresh = request.cookies.get("refresh_token") if client_type == "web" else (request.get_json(silent=True) or {}).get("refresh_token")

    if raw_refresh:
        try:
            payload = jwt.decode(raw_refresh, JWT_SECRET, algorithms=["HS256"])
            jti = payload.get("jti")
            conn = get_db_connection()
            conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE token = ?", (jti,))
            conn.commit()
            conn.close()
        except jwt.InvalidTokenError:
            pass

    if client_type == "web":
        resp = make_response(jsonify({"status": "success", "message": "Logged out"}))
        resp.delete_cookie("access_token")
        resp.delete_cookie("refresh_token")
        resp.delete_cookie("csrf_token")
        return resp, 200

    return jsonify({"status": "success", "message": "Logged out"}), 200


@app.route("/api/users/me", methods=["GET"])
@require_auth
def me():
    user_id = g.user.get("user_id")
    conn = get_db_connection()
    row  = conn.execute("SELECT id, username, role, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"status": "error", "message": "User not found"}), 404
    return jsonify({"status": "success", "data": dict(row)}), 200


# ---------------------------------------------------------------------------
# Profile Routes (With Header Versioning)
# ---------------------------------------------------------------------------

@app.route("/api/profiles", methods=["POST"])
@require_auth
@require_version("1")
@require_role("admin")
@csrf_protect
def create_profile():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"status": "error", "message": "Name is required"}), 400

    gender              = body.get("gender", "unknown")
    gender_probability  = float(body.get("gender_probability", 0.0))
    age                 = int(body.get("age", 0))
    country_id          = (body.get("country_id") or "").upper()
    country_probability = float(body.get("country_probability", 0.0))

    profile_id = str(uuid6.uuid7())
    age_group  = classify_age(age)
    country_name = get_country_name(country_id) if country_id else ""
    created_at = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection()
    conn.execute(
        """INSERT INTO profiles
           (id, name, gender, gender_probability, age, age_group, country_id, country_name, country_probability, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (profile_id, name, gender, gender_probability, age, age_group, country_id, country_name, country_probability, created_at),
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "success", "data": {
        "id": profile_id, "name": name, "gender": gender,
        "gender_probability": gender_probability, "age": age,
        "age_group": age_group, "country_id": country_id,
        "country_name": country_name, "country_probability": country_probability,
        "created_at": created_at,
    }}), 201


@app.route("/api/profiles/<profile_id>", methods=["GET"])
@require_auth
@require_version("1")
@require_role("admin", "analyst")
def get_profile(profile_id):
    conn = get_db_connection()
    row  = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"status": "error", "message": "Profile not found"}), 404
        
    return jsonify({
        "status": "success", 
        "data": profile_to_dict(row)
    }), 200

@app.route("/api/profiles", methods=["GET"])
@require_auth
@require_version("1")
@require_role("admin", "analyst")
def get_profiles():
    args = request.args

    gender    = args.get("gender",    "").strip().lower()
    age_group = args.get("age_group", "").strip().lower()
    country_id = args.get("country_id", "").strip().upper()

    if gender and gender not in VALID_GENDERS:
        return jsonify({"status": "error", "message": "Invalid gender"}), 400
    if age_group and age_group not in VALID_AGE_GROUPS:
        return jsonify({"status": "error", "message": "Invalid age_group"}), 400

    min_age, err = parse_int_param(args.get("min_age"), "min_age", min_val=0)
    if err: return jsonify(err[0]), err[1]
    max_age, err = parse_int_param(args.get("max_age"), "max_age", min_val=0)
    if err: return jsonify(err[0]), err[1]

    min_gp, err = parse_float_param(args.get("min_gender_probability"), "min_gender_probability")
    if err: return jsonify(err[0]), err[1]
    min_cp, err = parse_float_param(args.get("min_country_probability"), "min_country_probability")
    if err: return jsonify(err[0]), err[1]

    sort_by = args.get("sort_by", "created_at").strip().lower()
    order   = args.get("order",   "asc").strip().lower()
    page,  err = parse_int_param(args.get("page",  "1"),  "page",  default=1,  min_val=1)
    if err: return jsonify(err[0]), err[1]
    limit, err = parse_int_param(args.get("limit", "10"), "limit", default=10, min_val=1, max_val=MAX_LIMIT)
    if err: return jsonify(err[0]), err[1]

    filter_args = {
        "gender": gender, "age_group": age_group, "country_id": country_id,
        "min_age": min_age, "max_age": max_age,
        "min_gender_probability": min_gp, "min_country_probability": min_cp,
    }
    where, params = build_filter_query(filter_args)
    base_query = f"SELECT * FROM profiles WHERE {where}"
    rows, total = paginate_query(base_query, params, sort_by, order, page, limit)

    pagination_data = get_pagination_fields(page, limit, total)

    return jsonify({
        "status": "success",
        **pagination_data, 
        "data":   [profile_to_dict(r) for r in rows],
    }), 200


@app.route("/api/profiles/search", methods=["GET"])
@require_auth
@require_version("1")
@require_role("admin", "analyst")
def search_profiles():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"status": "error", "message": "Query parameter 'q' is required"}), 400

    filters = parse_query(query)

    page,  err = parse_int_param(request.args.get("page",  "1"),  "page",  default=1,  min_val=1)
    if err: return jsonify(err[0]), err[1]
    limit, err = parse_int_param(request.args.get("limit", "10"), "limit", default=10, min_val=1, max_val=MAX_LIMIT)
    if err: return jsonify(err[0]), err[1]

    sort_by = request.args.get("sort_by", "created_at").strip().lower()
    order   = request.args.get("order",   "asc").strip().lower()

    where, params = build_filter_query(filters)
    base_query = f"SELECT * FROM profiles WHERE {where}"
    rows, total = paginate_query(base_query, params, sort_by, order, page, limit)

    pagination_data = get_pagination_fields(page, limit, total)

    return jsonify({
        "status":          "success",
        "parsed_filters":  filters,
        **pagination_data, 
        "data":            [profile_to_dict(r) for r in rows],
    }), 200


@app.route("/api/profiles/export", methods=["GET"])
@require_auth
@require_version("1")
@require_role("admin", "analyst")
def export_profiles():
    args = request.args
    filter_args = {
        "gender":    args.get("gender",    "").strip().lower(),
        "age_group": args.get("age_group", "").strip().lower(),
        "country_id": args.get("country_id", "").strip().upper(),
    }
    where, params = build_filter_query(filter_args)
    base_query = f"SELECT * FROM profiles WHERE {where} ORDER BY created_at DESC"

    conn = get_db_connection()
    rows = conn.execute(base_query, params).fetchall()
    conn.close()

    def generate():
        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(["ID", "Name", "Gender", "Gender Probability", "Age", "Age Group",
                         "Country ID", "Country Name", "Country Probability", "Created At"])
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)

        for row in rows:
            writer.writerow([
                row["id"], row["name"], row["gender"], row["gender_probability"],
                row["age"], row["age_group"], row["country_id"], row["country_name"],
                row["country_probability"], row["created_at"],
            ])
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)

    return Response(
        generate(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=profiles_export.csv"},
    )


@app.route("/api/profiles/<profile_id>", methods=["DELETE"])
@require_auth
@require_version("1")
@require_role("admin")
@csrf_protect
def delete_profile(profile_id):
    conn = get_db_connection()
    cur  = conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return jsonify({"status": "error", "message": "Profile not found"}), 404
    return jsonify({"status": "success", "message": "Profile deleted"}), 200

# ---------------------------------------------------------------------------
# Admin: role management
# ---------------------------------------------------------------------------
@app.route("/api/admin/users", methods=["GET"])
@require_auth
@require_role("admin")
def list_users():
    conn  = get_db_connection()
    rows  = conn.execute("SELECT id, username, role, created_at FROM users").fetchall()
    conn.close()
    return jsonify({"status": "success", "data": [dict(r) for r in rows]}), 200


@app.route("/api/admin/users/<user_id>/role", methods=["POST"])
@require_auth
@require_role("admin")
@csrf_protect
def set_user_role(user_id):
    body = request.get_json(silent=True) or {}
    role = body.get("role", "").strip().lower()
    if role not in ("admin", "analyst"):
        return jsonify({"status": "error", "message": "Role must be 'admin' or 'analyst'"}), 400
    conn = get_db_connection()
    cur  = conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return jsonify({"status": "error", "message": "User not found"}), 404
    return jsonify({"status": "success", "message": f"Role updated to {role}"}), 200

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": "1.0.0"}), 200

# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return jsonify({"status": "error", "message": "Resource not found"}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"status": "error", "message": "Method not allowed"}), 405

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"status": "error", "message": "Rate limit exceeded. Please slow down."}), 429

@app.errorhandler(500)
def internal_error(e):
    app.logger.error(f"Internal error: {e}")
    return jsonify({"status": "error", "message": "Internal server error"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)