import logging
import time

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

app = Flask(__name__)
app.json.ensure_ascii = False
app.json.sort_keys = False

logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("polls")

STATUSES = ("open", "closed")


polls = {}
next_id = 1


#ошибки
def error(text, field=None, code=400):
    body = {"error": text}
    if field is not None:
        body["field"] = field
    return jsonify(body), code


@app.errorhandler(HTTPException)
def on_http_error(exc):
    messages = {404: "не найдено", 405: "метод не поддерживается"}
    return error(messages.get(exc.code, exc.description), code=exc.code)


@app.errorhandler(Exception)
def on_unexpected(exc):
    log.exception("unexpected error")
    return error("внутренняя ошибка сервера", code=500)


#лог запросов
@app.before_request
def start_timer():
    g.started = time.perf_counter()


@app.after_request
def log_request(response):
    ms = (time.perf_counter() - g.started) * 1000
    log.info("%s %s %s %.1fms", request.method, request.path, response.status_code, ms)
    return response


#валидация
def read_body():
    """Возвращает (словарь, None) или (None, ответ-ошибка)."""
    data = request.get_json(silent=True, force=True)
    if data is None:
        return None, error("некорректный JSON", "body")
    if not isinstance(data, dict):
        return None, error("тело запроса должно быть JSON-объектом", "body")
    return data, None


def validate(data, partial):
    """Проверяет поля опроса. Возвращает (чистые данные, ошибка)."""
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


#эндпоинты
@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/polls")
def create_poll():
    global next_id
    data, err = read_body()
    if err:
        return err
    clean, err = validate(data, partial=False)
    if err:
        return err
    poll = {"id": next_id, "description": "", "status": "open", **clean}
    polls[next_id] = poll
    next_id += 1
    return jsonify(poll), 201


@app.get("/api/polls")
def list_polls():
    limit, err = int_param("limit", 10, 1, 100)
    if err:
        return err
    offset, err = int_param("offset", 0, 0)
    if err:
        return err

    items = list(polls.values())
    status = request.args.get("status")
    if status is not None:
        if status not in STATUSES:
            return error("допустимы значения: open, closed", "status")
        items = [p for p in items if p["status"] == status]
    title = request.args.get("title")
    if title is not None:
        items = [p for p in items if p["title"] == title]

    response = jsonify(items[offset:offset + limit])
    response.headers["X-Total-Count"] = str(len(items))
    return response


@app.get("/api/polls/<int:poll_id>")
def get_poll(poll_id):
    if poll_id not in polls:
        return error("опрос не найден", code=404)
    return jsonify(polls[poll_id])


@app.put("/api/polls/<int:poll_id>")
def replace_poll(poll_id):
    if poll_id not in polls:
        return error("опрос не найден", code=404)
    data, err = read_body()
    if err:
        return err
    clean, err = validate(data, partial=False)
    if err:
        return err
    polls[poll_id] = {"id": poll_id, "description": "", "status": "open", **clean}
    return jsonify(polls[poll_id])


@app.patch("/api/polls/<int:poll_id>")
def update_poll(poll_id):
    if poll_id not in polls:
        return error("опрос не найден", code=404)
    data, err = read_body()
    if err:
        return err
    clean, err = validate(data, partial=True)
    if err:
        return err
    polls[poll_id].update(clean)
    return jsonify(polls[poll_id])


@app.delete("/api/polls/<int:poll_id>")
def delete_poll(poll_id):
    if poll_id not in polls:
        return error("опрос не найден", code=404)
    del polls[poll_id]
    return Response(status=204)


#документация OpenAPI
def _ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def _responses(*codes):
    names = {200: "OK", 201: "Создано", 204: "Удалено", 400: "Ошибка валидации", 404: "Не найдено"}
    out = {}
    for c in codes:
        out[str(c)] = {"description": names[c]}
        if c == 400:
            out[str(c)]["content"] = {"application/json": {"schema": _ref("Error")}}
    return out


def _body(schema):
    return {"required": True, "content": {"application/json": {"schema": _ref(schema)}}}


ID_PARAM = {"name": "poll_id", "in": "path", "required": True, "schema": {"type": "integer"}}

OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Polls API", "version": "1.0", "description": "Опросы: лабораторная 1, данные в памяти"},
    "paths": {
        "/health": {"get": {"summary": "Проверка сервиса", "responses": _responses(200)}},
        "/api/polls": {
            "get": {
                "summary": "Список опросов",
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100}},
                    {"name": "offset", "in": "query", "schema": {"type": "integer", "default": 0, "minimum": 0}},
                    {"name": "status", "in": "query", "schema": {"type": "string", "enum": list(STATUSES)}},
                    {"name": "title", "in": "query", "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK", "content": {"application/json": {
                    "schema": {"type": "array", "items": _ref("Poll")}}}}, **_responses(400)},
            },
            "post": {
                "summary": "Создать опрос",
                "requestBody": _body("PollIn"),
                "responses": {"201": {"description": "Создано", "content": {"application/json": {
                    "schema": _ref("Poll")}}}, **_responses(400)},
            },
        },
        "/api/polls/{poll_id}": {
            "get": {"summary": "Получить опрос", "parameters": [ID_PARAM], "responses": _responses(200, 404)},
            "put": {"summary": "Заменить опрос", "parameters": [ID_PARAM],
                    "requestBody": _body("PollIn"), "responses": _responses(200, 400, 404)},
            "patch": {"summary": "Изменить часть полей", "parameters": [ID_PARAM],
                      "requestBody": _body("PollPatch"), "responses": _responses(200, 400, 404)},
            "delete": {"summary": "Удалить опрос", "parameters": [ID_PARAM], "responses": _responses(204, 404)},
        },
    },
    "components": {"schemas": {
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
            "allOf": [_ref("PollIn"), {"type": "object", "properties": {"id": {"type": "integer"}}}],
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
