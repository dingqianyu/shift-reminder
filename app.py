from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).parent
STATIC = ROOT / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "shifts.db"
APP_PASSWORD = os.environ.get("APP_PASSWORD", "change-me")
SESSIONS: dict[str, float] = {}
SESSION_LOCK = threading.Lock()
LOCAL_TZ = timezone(timedelta(hours=8))


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shifts (
                employee TEXT NOT NULL DEFAULT '默认员工',
                day TEXT NOT NULL,
                shift TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (employee, day)
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS employees (
                name TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                action TEXT NOT NULL,
                shift TEXT,
                employee TEXT,
                editor TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                delivery_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            """
        )
        shift_cols = [row["name"] for row in conn.execute("PRAGMA table_info(shifts)")]
        if "employee" not in shift_cols:
            conn.executescript(
                """
                ALTER TABLE shifts RENAME TO shifts_old;
                CREATE TABLE shifts (
                    employee TEXT NOT NULL DEFAULT '默认员工',
                    day TEXT NOT NULL,
                    shift TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (employee, day)
                );
                INSERT OR REPLACE INTO shifts(employee, day, shift, updated_by, updated_at)
                SELECT COALESCE(NULLIF(updated_by, ''), '默认员工'), day, shift, updated_by, updated_at
                FROM shifts_old;
                DROP TABLE shifts_old;
                """
            )
        audit_cols = [row["name"] for row in conn.execute("PRAGMA table_info(audit)")]
        if "employee" not in audit_cols:
            conn.execute("ALTER TABLE audit ADD COLUMN employee TEXT")
        now = local_now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT OR IGNORE INTO employees(name, created_at) VALUES (?, ?)",
            ("默认员工", now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO employees(name, created_at)
            SELECT employee, ? FROM shifts
            WHERE employee IS NOT NULL AND employee != ''
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO employees(name, created_at)
            SELECT employee, ? FROM audit
            WHERE employee IS NOT NULL AND employee != ''
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO employees(name, created_at)
            SELECT editor, ? FROM audit
            WHERE editor IS NOT NULL AND editor != ''
            """,
            (now,),
        )
        defaults = {
            "reminder_times": "18:00",
            "server_chan_key": "",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def parse_cookies(header: str) -> dict[str, str]:
    result = {}
    for part in header.split(";"):
        if "=" in part:
            key, value = part.strip().split("=", 1)
            result[key] = value
    return result


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    with SESSION_LOCK:
        expires = SESSIONS.get(token)
        if not expires or expires < time.time():
            SESSIONS.pop(token, None)
            return False
        SESSIONS[token] = time.time() + 30 * 86400
        return True


def send_server_chan(send_key: str, title: str, body: str) -> None:
    encoded = urllib.parse.urlencode({"title": title, "desp": body}).encode()
    request = urllib.request.Request(
        f"https://sctapi.ftqq.com/{send_key}.send",
        data=encoded,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code", 0) != 0:
        raise RuntimeError(payload.get("message", "Server酱推送失败"))


def reminder_loop() -> None:
    while True:
        try:
            now = local_now()
            current_time = now.strftime("%H:%M")
            tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            with db() as conn:
                settings = {
                    row["key"]: row["value"]
                    for row in conn.execute("SELECT key, value FROM settings")
                }
                times = {
                    item.strip()
                    for item in settings.get("reminder_times", "").replace("，", ",").split(",")
                    if item.strip()
                }
                send_key = settings.get("server_chan_key", "").strip()
                delivery_key = f"{now:%Y-%m-%d}-{current_time}"
                delivered = conn.execute(
                    "SELECT 1 FROM deliveries WHERE delivery_key = ?", (delivery_key,)
                ).fetchone()
                rows = conn.execute(
                    "SELECT employee, shift FROM shifts WHERE day = ? ORDER BY employee",
                    (tomorrow,),
                ).fetchall()
                if current_time in times and send_key and rows and not delivered:
                    lines = [f"{row['employee']}：{row['shift']}" for row in rows]
                    title_items = [f"{row['employee']}{row['shift']}" for row in rows]
                    title_summary = "、".join(title_items)
                    if len(title_summary) > 38:
                        title_summary = "、".join(title_items[:3])
                        if len(rows) > 3:
                            title_summary += f"等{len(rows)}人"
                    send_server_chan(
                        send_key,
                        f"明日班次：{title_summary}",
                        f"日期：{tomorrow}\n\n" + "\n".join(lines),
                    )
                    conn.execute(
                        "INSERT INTO deliveries(delivery_key, created_at) VALUES (?, ?)",
                        (delivery_key, now.isoformat(timespec="seconds")),
                    )
                    conn.execute(
                        "DELETE FROM deliveries WHERE created_at < ?",
                        ((now - timedelta(days=14)).isoformat(timespec="seconds"),),
                    )
        except Exception as exc:
            print(f"[reminder] {exc}", flush=True)
        time.sleep(20)


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = urllib.parse.urlparse(path).path
        relative = Path(clean.lstrip("/") or "index.html")
        if ".." in relative.parts:
            return str(STATIC / "__not_found__")
        return str(STATIC / relative)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[http] {self.address_string()} {fmt % args}", flush=True)

    def send_json(self, value, status=HTTPStatus.OK, headers=None) -> None:
        payload = json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def session_token(self):
        return parse_cookies(self.headers.get("Cookie", "")).get("shift_session")

    def require_auth(self) -> bool:
        if valid_session(self.session_token()):
            return True
        self.send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
        return False

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return super().do_GET()
        if parsed.path == "/api/session":
            return self.send_json({"authenticated": valid_session(self.session_token())})
        if not self.require_auth():
            return
        if parsed.path == "/api/shifts":
            month = urllib.parse.parse_qs(parsed.query).get("month", [""])[0]
            employee = urllib.parse.parse_qs(parsed.query).get("employee", ["默认员工"])[0].strip()[:30] or "默认员工"
            with db() as conn:
                rows = conn.execute(
                    "SELECT employee, day, shift, updated_by, updated_at FROM shifts "
                    "WHERE employee = ? AND day LIKE ? ORDER BY day",
                    (employee, f"{month}-%"),
                ).fetchall()
            return self.send_json([dict(row) for row in rows])
        if parsed.path == "/api/employees":
            with db() as conn:
                rows = conn.execute(
                    "SELECT name FROM employees ORDER BY name"
                ).fetchall()
            return self.send_json([dict(row) for row in rows if row["name"]])
        if parsed.path == "/api/settings":
            with db() as conn:
                values = {
                    row["key"]: row["value"]
                    for row in conn.execute("SELECT key, value FROM settings")
                }
            values["server_chan_key"] = bool(values.get("server_chan_key"))
            return self.send_json(values)
        if parsed.path == "/api/audit":
            with db() as conn:
                rows = conn.execute(
                    "SELECT day, action, shift, employee, editor, created_at FROM audit "
                    "ORDER BY id DESC LIMIT 20"
                ).fetchall()
            return self.send_json([dict(row) for row in rows])
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/login":
            data = self.read_json()
            provided = str(data.get("password", ""))
            if not hmac.compare_digest(
                hashlib.sha256(provided.encode()).digest(),
                hashlib.sha256(APP_PASSWORD.encode()).digest(),
            ):
                return self.send_json({"error": "密码错误"}, HTTPStatus.UNAUTHORIZED)
            token = secrets.token_urlsafe(32)
            with SESSION_LOCK:
                SESSIONS[token] = time.time() + 30 * 86400
            return self.send_json(
                {"ok": True},
                headers={
                    "Set-Cookie": f"shift_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
                },
            )
        if not self.require_auth():
            return
        data = self.read_json()
        if parsed.path == "/api/shift":
            day = str(data.get("day", ""))
            shift = str(data.get("shift", ""))
            employee = str(data.get("employee", "默认员工")).strip()[:30] or "默认员工"
            editor = str(data.get("editor", "匿名")).strip()[:30] or "匿名"
            if shift not in {"早班", "白班", "夜班", "转班"}:
                return self.send_json({"error": "无效班次"}, HTTPStatus.BAD_REQUEST)
            now = local_now().isoformat(timespec="seconds")
            with db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO employees(name, created_at) VALUES (?, ?)",
                    (employee, now),
                )
                conn.execute(
                    "INSERT INTO shifts(employee, day, shift, updated_by, updated_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(employee, day) DO UPDATE SET shift=excluded.shift, "
                    "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                    (employee, day, shift, editor, now),
                )
                conn.execute(
                    "INSERT INTO audit(day, action, shift, employee, editor, created_at) VALUES (?, 'set', ?, ?, ?, ?)",
                    (day, shift, employee, editor, now),
                )
            return self.send_json({"ok": True})
        if parsed.path == "/api/settings":
            times = str(data.get("reminder_times", "")).strip()
            key = str(data.get("server_chan_key", "")).strip()
            parts = [x.strip() for x in times.replace("，", ",").split(",") if x.strip()]
            if not parts or len(parts) > 6:
                return self.send_json({"error": "请填写 1–6 个提醒时间"}, HTTPStatus.BAD_REQUEST)
            for item in parts:
                try:
                    datetime.strptime(item, "%H:%M")
                except ValueError:
                    return self.send_json(
                        {"error": f"时间格式错误：{item}"}, HTTPStatus.BAD_REQUEST
                    )
            with db() as conn:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES ('reminder_times', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (", ".join(dict.fromkeys(parts)),),
                )
                if key:
                    conn.execute(
                        "INSERT INTO settings(key, value) VALUES ('server_chan_key', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key,),
                    )
            return self.send_json({"ok": True})
        if parsed.path == "/api/employee":
            name = str(data.get("name", "")).strip()[:30]
            if not name:
                return self.send_json({"error": "请填写员工姓名"}, HTTPStatus.BAD_REQUEST)
            now = local_now().isoformat(timespec="seconds")
            with db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO employees(name, created_at) VALUES (?, ?)",
                    (name, now),
                )
            return self.send_json({"ok": True})
        if parsed.path == "/api/test-push":
            key = str(data.get("server_chan_key", "")).strip()
            if not key:
                with db() as conn:
                    row = conn.execute(
                        "SELECT value FROM settings WHERE key = 'server_chan_key'"
                    ).fetchone()
                key = row["value"] if row else ""
            try:
                send_server_chan(key, "排班提醒测试成功", "多人协作排班工具已连接微信。")
            except Exception as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        if not self.require_auth():
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/employee":
            params = urllib.parse.parse_qs(parsed.query)
            name = params.get("name", [""])[0].strip()[:30]
            if not name:
                return self.send_json({"error": "请选择要删除的员工"}, HTTPStatus.BAD_REQUEST)
            with db() as conn:
                count = conn.execute("SELECT COUNT(*) AS n FROM employees").fetchone()["n"]
                if count <= 1:
                    return self.send_json({"error": "至少保留一个员工"}, HTTPStatus.BAD_REQUEST)
                conn.execute("DELETE FROM employees WHERE name = ?", (name,))
                conn.execute("DELETE FROM shifts WHERE employee = ?", (name,))
            return self.send_json({"ok": True})
        if parsed.path != "/api/shift":
            return self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        params = urllib.parse.parse_qs(parsed.query)
        day = params.get("day", [""])[0]
        employee = params.get("employee", ["默认员工"])[0].strip()[:30] or "默认员工"
        editor = params.get("editor", ["匿名"])[0][:30]
        now = local_now().isoformat(timespec="seconds")
        with db() as conn:
            conn.execute("DELETE FROM shifts WHERE employee = ? AND day = ?", (employee, day))
            conn.execute(
                "INSERT INTO audit(day, action, shift, employee, editor, created_at) "
                "VALUES (?, 'delete', NULL, ?, ?, ?)",
                (day, employee, editor, now),
            )
        self.send_json({"ok": True})


if __name__ == "__main__":
    init_db()
    threading.Thread(target=reminder_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print("Shift Reminder listening on http://0.0.0.0:8080", flush=True)
    server.serve_forever()
