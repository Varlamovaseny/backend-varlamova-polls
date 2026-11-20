import json

from db import connect, ensure_schema, transaction
from security import hash_password

DEMO_USER = "demo"
DEMO_PASSWORD = "demo-pass-123"

POLLS = [
    {
        "title": "Любимое время года",
        "description": "Выберите один вариант",
        "options": ["Зима", "Весна", "Лето", "Осень"],
        "status": "open",
        "votes": [("Лето", "anna@example.com"), ("Лето", "boris@example.com"),
                  ("Зима", "vera@example.com"), ("Осень", "gleb@example.com")],
    },
    {
        "title": "Какой язык программирования учить первым",
        "description": "Для новичков",
        "options": ["Python", "JavaScript", "Go"],
        "status": "open",
        "votes": [("Python", "dina@example.com"), ("Python", "egor@example.com"),
                  ("JavaScript", "zoya@example.com")],
    },
    {
        "title": "Куда поедем в отпуск",
        "description": "Голосование уже завершено",
        "options": ["Море", "Горы"],
        "status": "closed",
        "votes": [("Море", "ivan@example.com"), ("Горы", "kira@example.com"),
                  ("Море", "lena@example.com")],
    },
    {
        "title": "Кофе или чай",
        "description": "",
        "options": ["Кофе", "Чай"],
        "status": "open",
        "votes": [],
    },
]


def main():
    ensure_schema()
    db = connect()
    with transaction(db):
        if db.execute("SELECT COUNT(*) FROM polls").fetchone()[0] > 0:
            print("База не пуста, сиды пропущены.")
            return

        user = db.execute("SELECT id FROM users WHERE username = ?", (DEMO_USER,)).fetchone()
        if user is None:
            user_id = db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (DEMO_USER, hash_password(DEMO_PASSWORD)),
            ).lastrowid
        else:
            user_id = user["id"]
        for poll in POLLS:
            cur = db.execute(
                "INSERT INTO polls (title, description, options, status, owner_id) VALUES (?, ?, ?, ?, ?)",
                (poll["title"], poll["description"],
                 json.dumps(poll["options"], ensure_ascii=False), poll["status"], user_id),
            )
            db.executemany(
                "INSERT INTO votes (poll_id, option, email) VALUES (?, ?, ?)",
                [(cur.lastrowid, option, email) for option, email in poll["votes"]],
            )
    print(f"Готово: добавлено опросов: {len(POLLS)}.")
    print(f"Демо-пользователь: {DEMO_USER} / {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
