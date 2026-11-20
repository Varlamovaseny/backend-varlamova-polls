import json
import logging
import os
import re
import sqlite3
import time

import jwt
from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

from db import connect, ensure_schema, transaction
from security import (check_settings, create_token, decode_token, dummy_verify, hash_password,
                      login_limiter, token_lifetime, verify_password)

app = Flask(__name__)
app.json.ensure_ascii = False
app.json.sort_keys = False

logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("polls")

STATUSES = ("open", "closed")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ensure_schema()
check_settings()


def ensure_admin():
    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")
    if not username or not password:
        return
    db = connect()
    try:
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
                       (username, hash_password(password)))
        elif row["role"] != "admin" or not verify_password(password, row["password_hash"]):
            db.execute("UPDATE users SET role = 'admin', password_hash = ? WHERE id = ?",
                       (hash_password(password), row["id"]))
    finally:
        db.close()


ensure_admin()


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def error(text, field=None, code=400, headers=None):
    body = {"error": text}
    if field is not None:
        body["field"] = field
    return jsonify(body), code, headers or {}


class ApiError(Exception):

    def __init__(self, response):
        self.response = response


@app.errorhandler(ApiError)
def on_api_error(exc):
    return exc.response


@app.errorhandler(HTTPException)
def on_http_error(exc):
    messages = {404: "не найдено", 405: "метод не поддерживается"}
    return error(messages.get(exc.code, exc.description), code=exc.code)


@app.errorhandler(Exception)
def on_unexpected(exc):
    log.exception("unexpected error")
    return error("внутренняя ошибка сервера", code=500)


@app.before_request
def start_timer():
    g.started = time.perf_counter()


@app.after_request
def log_request(response):
    ms = (time.perf_counter() - g.started) * 1000
    log.info("%s %s %s %.1fms", request.method, request.path, response.status_code, ms)
    return response


SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
PUBLIC_ENDPOINTS = {"register", "login"}
PROTECTED_READS = {"me"}
CHALLENGE = {"WWW-Authenticate": 'Basic realm="polls", Bearer realm="polls"'}
USERNAME_RE = re.compile(r"^[\w.@+-]{3,64}$")


def check_credentials(username, password):
    key = (request.remote_addr, username.strip().lower())
    wait = login_limiter.retry_after(key)
    if wait:
        raise ApiError(error("слишком много неудачных попыток входа, попробуйте позже", code=429,
                             headers={"Retry-After": str(wait)}))
    user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user is None:
        dummy_verify(password)
    if user is None or not verify_password(password, user["password_hash"]):
        login_limiter.register_failure(key)
        return None
    login_limiter.reset(key)
    return user


def wrong_credentials():
    return ApiError(error("неверный логин или пароль", code=401, headers=CHALLENGE))


def user_from_basic():
    auth = request.authorization
    if auth is None or auth.type != "basic" or not auth.username or auth.password is None:
        raise ApiError(error("нужна авторизация", code=401, headers=CHALLENGE))
    user = check_credentials(auth.username, auth.password)
    if user is None:
        raise wrong_credentials()
    return user


def user_from_token(token):
    try:
        user_id = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise ApiError(error("токен просрочен", code=401, headers=CHALLENGE))
    except jwt.PyJWTError:
        raise ApiError(error("недействительный токен", code=401, headers=CHALLENGE))
    user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        raise ApiError(error("недействительный токен", code=401, headers=CHALLENGE))
    return user


def authenticate():
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    scheme = scheme.lower()
    if scheme == "bearer":
        return user_from_token(value.strip())
    if scheme == "basic":
        return user_from_basic()
    raise ApiError(error("нужна авторизация", code=401, headers=CHALLENGE))


@app.before_request
def check_access():
    endpoint = request.endpoint
    if endpoint is None:
        return
    changes_data = request.method not in SAFE_METHODS and endpoint not in PUBLIC_ENDPOINTS
    if changes_data or endpoint in PROTECTED_READS:
        g.user = authenticate()


def require_owner(poll_row):
    if g.user["role"] == "admin":
        return
    if poll_row["owner_id"] != g.user["id"]:
        raise ApiError(error("нельзя изменять чужие записи", code=403))


def read_body():
    data = request.get_json(silent=True, force=True)
    if data is None:
        return None, error("некорректный JSON", "body")
    if not isinstance(data, dict):
        return None, error("тело запроса должно быть JSON-объектом", "body")
    return data, None


def validate(data, partial):
    clean = {}

    if "title" in data or not partial:
        title = data.get("title")
        if title is None and "title" not in data:
            return None, error("обязательное поле", "title")
        if not isinstance(title, str):
            return None, error("должно быть строкой", "title")
        title = title.strip()
        if not title:
            return None, error("не может быть пустым", "title")
        clean["title"] = title

    if "description" in data:
        if not isinstance(data["description"], str):
            return None, error("должно быть строкой", "description")
        clean["description"] = data["description"]

    if "options" in data or not partial:
        options = data.get("options")
        if options is None and "options" not in data:
            return None, error("обязательное поле", "options")
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            return None, error("должно быть списком строк", "options")
        options = [o.strip() for o in options]
        if len(options) < 2:
            return None, error("нужно минимум 2 варианта ответа", "options")
        if any(not o for o in options):
            return None, error("варианты ответа не могут быть пустыми", "options")
        if len(set(options)) != len(options):
            return None, error("варианты ответа не должны повторяться", "options")
        clean["options"] = options

    if "status" in data:
        if data["status"] not in STATUSES:
            return None, error("допустимы значения: open, closed", "status")
        clean["status"] = data["status"]

    return clean, None


def validate_user(data):
    username = data.get("username")
    if username is None:
        return None, error("обязательное поле", "username")
    if not isinstance(username, str):
        return None, error("должно быть строкой", "username")
    username = username.strip()
    if not USERNAME_RE.match(username):
        return None, error("от 3 до 64 символов: буквы, цифры и знаки . _ @ + -", "username")

    password = data.get("password")
    if password is None:
        return None, error("обязательное поле", "password")
    if not isinstance(password, str):
        return None, error("должно быть строкой", "password")
    if len(password) < 6:
        return None, error("минимум 6 символов", "password")
    if len(password.encode("utf-8")) > 72:
        return None, error("не длиннее 72 байт", "password")
    return {"username": username, "password": password}, None


def validate_vote(data, poll_options):
    option = data.get("option")
    if option is None:
        return None, error("обязательное поле", "option")
    if not isinstance(option, str):
        return None, error("должно быть строкой", "option")
    option = option.strip()
    if option not in poll_options:
        return None, error("такого варианта нет в опросе", "option")

    email = data.get("email")
    if email is None:
        return None, error("обязательное поле", "email")
    if not isinstance(email, str):
        return None, error("должно быть строкой", "email")
    email = email.strip()
    if not EMAIL_RE.match(email):
        return None, error("некорректный email", "email")

    return {"option": option, "email": email}, None


def int_param(name, default, minimum, maximum=None):
    raw = request.args.get(name)
    if raw is None:
        return default, None
    try:
        value = int(raw)
    except ValueError:
        return None, error("должно быть целым числом", name)
    if value < minimum or (maximum is not None and value > maximum):
        return None, error("значение вне допустимого диапазона", name)
    return value, None


def poll_to_dict(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "options": json.loads(row["options"]),
        "status": row["status"],
        "owner_id": row["owner_id"],
    }


def vote_to_dict(row):
    return {"id": row["id"], "poll_id": row["poll_id"], "option": row["option"], "email": row["email"]}


def find_poll(poll_id):
    return get_db().execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()


def save_poll(poll_id, poll):
    get_db().execute(
        "UPDATE polls SET title = ?, description = ?, options = ?, status = ? WHERE id = ?",
        (poll["title"], poll["description"], json.dumps(poll["options"], ensure_ascii=False),
         poll["status"], poll_id),
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/polls")
def create_poll():
    data, err = read_body()
    if err:
        return err
    clean, err = validate(data, partial=False)
    if err:
        return err
    poll = {"description": "", "status": "open", **clean}

    cur = get_db().execute(
        "INSERT INTO polls (title, description, options, status, owner_id) VALUES (?, ?, ?, ?, ?)",
        (poll["title"], poll["description"], json.dumps(poll["options"], ensure_ascii=False),
         poll["status"], g.user["id"]),
    )
    return jsonify(poll_to_dict(find_poll(cur.lastrowid))), 201


@app.get("/api/polls")
def list_polls():
    limit, err = int_param("limit", 10, 1, 100)
    if err:
        return err
    offset, err = int_param("offset", 0, 0)
    if err:
        return err

    where, params = [], []
    status = request.args.get("status")
    if status is not None:
        if status not in STATUSES:
            return error("допустимы значения: open, closed", "status")
        where.append("status = ?")
        params.append(status)
    title = request.args.get("title")
    if title is not None:
        where.append("title = ?")
        params.append(title)
    q = request.args.get("q", "").strip()
    if q:


        escaped = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("py_lower(title) LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM polls" + clause, params).fetchone()[0]
    rows = db.execute(
        "SELECT * FROM polls" + clause + " ORDER BY id LIMIT ? OFFSET ?", params + [limit, offset]
    ).fetchall()

    response = jsonify([poll_to_dict(r) for r in rows])
    response.headers["X-Total-Count"] = str(total)
    return response


@app.get("/api/polls/<int:poll_id>")
def get_poll(poll_id):
    row = find_poll(poll_id)
    if row is None:
        return error("опрос не найден", code=404)
    return jsonify(poll_to_dict(row))


@app.put("/api/polls/<int:poll_id>")
def replace_poll(poll_id):
    row = find_poll(poll_id)
    if row is None:
        return error("опрос не найден", code=404)
    require_owner(row)
    data, err = read_body()
    if err:
        return err
    clean, err = validate(data, partial=False)
    if err:
        return err
    save_poll(poll_id, {"description": "", "status": "open", **clean})
    return jsonify(poll_to_dict(find_poll(poll_id)))


@app.patch("/api/polls/<int:poll_id>")
def update_poll(poll_id):
    row = find_poll(poll_id)
    if row is None:
        return error("опрос не найден", code=404)
    require_owner(row)
    data, err = read_body()
    if err:
        return err
    clean, err = validate(data, partial=True)
    if err:
        return err
    save_poll(poll_id, {**poll_to_dict(row), **clean})
    return jsonify(poll_to_dict(find_poll(poll_id)))


@app.delete("/api/polls/<int:poll_id>")
def delete_poll(poll_id):
    row = find_poll(poll_id)
    if row is None:
        return error("опрос не найден", code=404)
    require_owner(row)
    get_db().execute("DELETE FROM polls WHERE id = ?", (poll_id,))
    return Response(status=204)


@app.post("/auth/register")
def register():
    data, err = read_body()
    if err:
        return err
    clean, err = validate_user(data)
    if err:
        return err
    try:
        cur = get_db().execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (clean["username"], hash_password(clean["password"])),
        )
    except sqlite3.IntegrityError:
        return error("пользователь с таким логином уже существует", "username", 409)
    return jsonify({"id": cur.lastrowid, "username": clean["username"]}), 201


@app.post("/auth/login")
def login():
    data, err = read_body()
    if err:
        return err
    for field in ("username", "password"):
        if data.get(field) is None:
            return error("обязательное поле", field)
        if not isinstance(data[field], str):
            return error("должно быть строкой", field)
    user = check_credentials(data["username"], data["password"])
    if user is None:
        raise wrong_credentials()
    return jsonify({
        "access_token": create_token(user["id"]),
        "token_type": "bearer",
        "expires_in": token_lifetime(),
    })


@app.get("/auth/me")
def me():

    return jsonify({"id": g.user["id"], "username": g.user["username"], "role": g.user["role"]})


@app.post("/api/polls/<int:poll_id>/votes")
def create_vote(poll_id):
    data, err = read_body()
    if err:
        return err
    db = get_db()


    with transaction(db):
        row = db.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
        if row is None:
            raise ApiError(error("опрос не найден", code=404))
        clean, err = validate_vote(data, json.loads(row["options"]))
        if err:
            raise ApiError(err)
        if row["status"] == "closed":
            raise ApiError(error("опрос закрыт, голосовать нельзя", code=409))
        cur = db.execute(
            "INSERT INTO votes (poll_id, option, email) VALUES (?, ?, ?)",
            (poll_id, clean["option"], clean["email"]),
        )
    return jsonify({"id": cur.lastrowid, "poll_id": poll_id, **clean}), 201


@app.get("/api/polls/<int:poll_id>/votes")
def list_votes(poll_id):
    if find_poll(poll_id) is None:
        return error("опрос не найден", code=404)
    limit, err = int_param("limit", 10, 1, 100)
    if err:
        return err
    offset, err = int_param("offset", 0, 0)
    if err:
        return err
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM votes WHERE poll_id = ?", (poll_id,)).fetchone()[0]
    rows = db.execute(
        "SELECT * FROM votes WHERE poll_id = ? ORDER BY id LIMIT ? OFFSET ?", (poll_id, limit, offset)
    ).fetchall()
    response = jsonify([vote_to_dict(r) for r in rows])
    response.headers["X-Total-Count"] = str(total)
    return response


@app.get("/api/polls/stats")
def stats():
    db = get_db()

    by_status = {s: 0 for s in STATUSES}
    for r in db.execute("SELECT status, COUNT(*) AS n FROM polls GROUP BY status"):
        by_status[r["status"]] = r["n"]


    totals = db.execute(
        """
        SELECT COUNT(*)                    AS polls,
               COALESCE(SUM(cnt), 0)       AS votes,
               COALESCE(ROUND(AVG(cnt), 2), 0) AS avg_votes
        FROM (SELECT COUNT(v.id) AS cnt
              FROM polls p LEFT JOIN votes v ON v.poll_id = p.id
              GROUP BY p.id)
        """
    ).fetchone()


    top = db.execute(
        """
        SELECT p.id, p.title, COUNT(v.id) AS votes
        FROM polls p LEFT JOIN votes v ON v.poll_id = p.id
        GROUP BY p.id
        ORDER BY votes DESC, p.id
        LIMIT 5
        """
    ).fetchall()

    return jsonify({
        "polls_total": totals["polls"],
        "by_status": by_status,
        "votes_total": totals["votes"],
        "avg_votes_per_poll": totals["avg_votes"],
        "top_polls": [{"poll_id": r["id"], "title": r["title"], "votes": r["votes"]} for r in top],
    })


def _ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def _responses(*codes):
    names = {200: "OK", 201: "Создано", 204: "Удалено", 400: "Ошибка валидации", 401: "Нужен вход",
             403: "Чужая запись", 404: "Не найдено", 409: "Конфликт",
             429: "Слишком много попыток входа"}
    out = {}
    for c in codes:
        out[str(c)] = {"description": names[c]}
        if c == 400:
            out[str(c)]["content"] = {"application/json": {"schema": _ref("Error")}}
    return out


def _body(schema):
    return {"required": True, "content": {"application/json": {"schema": _ref(schema)}}}


SECURITY = [{"basicAuth": []}, {"bearerAuth": []}]
ID_PARAM = {"name": "poll_id", "in": "path", "required": True, "schema": {"type": "integer"}}
PAGING = [
    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100}},
    {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0, "minimum": 0}},
]

OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Polls API", "version": "3.2", "description": "Опросы, голоса и пользователи, данные хранятся в SQLite"},
    "paths": {
        "/health": {"get": {"summary": "Проверка сервиса", "responses": _responses(200)}},
        "/api/polls": {
            "get": {
                "summary": "Список опросов",
                "parameters": PAGING + [
                    {"name": "status", "in": "query", "schema": {"type": "string", "enum": list(STATUSES)}},
                    {"name": "title", "in": "query", "schema": {"type": "string"}},
                    {"name": "q", "in": "query", "description": "поиск по части названия без учёта регистра",
                     "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK", "content": {"application/json": {
                    "schema": {"type": "array", "items": _ref("Poll")}}}}, **_responses(400)},
            },
            "post": {
                "summary": "Создать опрос",
                "security": SECURITY,
                "requestBody": _body("PollIn"),
                "responses": {"201": {"description": "Создано", "content": {"application/json": {
                    "schema": _ref("Poll")}}}, **_responses(400, 401)},
            },
        },
        "/api/polls/stats": {"get": {"summary": "Статистика по опросам и голосам", "responses": _responses(200)}},
        "/api/polls/{poll_id}": {
            "get": {"summary": "Получить опрос", "parameters": [ID_PARAM], "responses": _responses(200, 404)},
            "put": {"summary": "Заменить опрос", "security": SECURITY, "parameters": [ID_PARAM],
                    "requestBody": _body("PollIn"), "responses": _responses(200, 400, 401, 403, 404)},
            "patch": {"summary": "Изменить часть полей", "security": SECURITY, "parameters": [ID_PARAM],
                      "requestBody": _body("PollPatch"), "responses": _responses(200, 400, 401, 403, 404)},
            "delete": {"summary": "Удалить опрос", "security": SECURITY, "parameters": [ID_PARAM],
                       "responses": _responses(204, 401, 403, 404)},
        },
        "/auth/register": {
            "post": {"summary": "Регистрация", "requestBody": _body("UserIn"),
                     "responses": {"201": {"description": "Создано", "content": {"application/json": {
                         "schema": _ref("User")}}}, **_responses(400, 409)}},
        },
        "/auth/login": {
            "post": {"summary": "Вход: выдаёт JWT-токен", "requestBody": _body("UserIn"),
                     "responses": {"200": {"description": "OK", "content": {"application/json": {
                         "schema": _ref("Token")}}}, **_responses(400, 401, 429)}},
        },
        "/auth/me": {
            "get": {"summary": "Текущий пользователь", "security": SECURITY,
                    "responses": {"200": {"description": "OK", "content": {"application/json": {
                        "schema": _ref("User")}}}, **_responses(401)}},
        },
        "/api/polls/{poll_id}/votes": {
            "get": {"summary": "Голоса опроса", "parameters": [ID_PARAM] + PAGING,
                    "responses": {"200": {"description": "OK", "content": {"application/json": {
                        "schema": {"type": "array", "items": _ref("Vote")}}}}, **_responses(400, 404)}},
            "post": {"summary": "Проголосовать", "security": SECURITY, "parameters": [ID_PARAM], "requestBody": _body("VoteIn"),
                     "responses": {"201": {"description": "Создано", "content": {"application/json": {
                         "schema": _ref("Vote")}}}, **_responses(400, 401, 404, 409)}},
        },
    },
    "components": {"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"},
                            "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}, "schemas": {
        "PollIn": {
            "type": "object", "required": ["title", "options"],
            "properties": {
                "title": {"type": "string", "example": "Любимое время года"},
                "description": {"type": "string"},
                "options": {"type": "array", "minItems": 2, "items": {"type": "string"}, "example": ["Зима", "Лето"]},
                "status": {"type": "string", "enum": list(STATUSES), "default": "open"},
            },
        },
        "PollPatch": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "options": {"type": "array", "minItems": 2, "items": {"type": "string"}},
                "status": {"type": "string", "enum": list(STATUSES)},
            },
        },
        "Poll": {
            "allOf": [_ref("PollIn"), {"type": "object", "properties": {
                "id": {"type": "integer"}, "owner_id": {"type": "integer", "nullable": True, "readOnly": True}}}],
        },
        "VoteIn": {
            "type": "object", "required": ["option", "email"],
            "properties": {
                "option": {"type": "string", "example": "Зима"},
                "email": {"type": "string", "example": "ivan@example.com"},
            },
        },
        "Vote": {
            "allOf": [_ref("VoteIn"), {"type": "object", "properties": {
                "id": {"type": "integer"}, "poll_id": {"type": "integer"}}}],
        },
        "UserIn": {
            "type": "object", "required": ["username", "password"],
            "properties": {
                "username": {"type": "string", "example": "ivan"},
                "password": {"type": "string", "format": "password", "example": "secret-pass"},
            },
        },
        "Token": {
            "type": "object",
            "properties": {
                "access_token": {"type": "string"},
                "token_type": {"type": "string", "example": "bearer"},
                "expires_in": {"type": "integer", "description": "время жизни токена в секундах"},
            },
        },
        "User": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "username": {"type": "string"},
                           "role": {"type": "string", "enum": ["user", "admin"]}},
        },
        "Error": {
            "type": "object",
            "properties": {"error": {"type": "string"}, "field": {"type": "string"}},
        },
    }},
}

DOCS_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Polls API: документация</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>SwaggerUIBundle({url: "/openapi.json", dom_id: "#swagger-ui"});</script>
</body>
</html>"""


@app.get("/openapi.json")
def openapi():
    return jsonify(OPENAPI)


@app.get("/docs")
def docs():
    return Response(DOCS_HTML, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)