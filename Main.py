import sqlite3
import pytz
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template

# ================= הגדרות =================
DB_FILE = "tasks.db"
ISRAEL_TZ = pytz.timezone("Asia/Jerusalem")

app = Flask(__name__)

# ================= עזר =================
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        duration INTEGER,
        remaining INTEGER,
        status TEXT,
        end_time TEXT
    )
    """)
    conn.commit()
    conn.close()

def dict_from_row(row):
    return dict(row)

def now_il():
    return datetime.now(ISRAEL_TZ)

# ================= לוגיקה =================
def recalc_remaining(task):
    """מחשב remaining לפי end_time והשעה הנוכחית"""
    if task["status"] == "running" and task["end_time"]:
        end_time = datetime.fromisoformat(task["end_time"])
        now = now_il()
        if now >= end_time:
            task["status"] = "done"
            task["remaining"] = 0
        else:
            delta = end_time - now
            task["remaining"] = int(delta.total_seconds())
    return task

def get_all_tasks():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM tasks ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    tasks = [recalc_remaining(dict_from_row(r)) for r in rows]
    return tasks

def save_task(task):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE tasks SET name=?, duration=?, remaining=?, status=?, end_time=? WHERE id=?
    """, (task["name"], task["duration"], task["remaining"], task["status"], task["end_time"], task["id"]))
    conn.commit()
    conn.close()

# ================= ראוטים =================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/tasks", methods=["GET"])
def list_tasks():
    return jsonify(get_all_tasks())

@app.route("/add", methods=["POST"])
def add_task():
    data = request.json
    duration = int(data.get("duration", 0))
    task = {
        "name": data["name"],
        "duration": duration,
        "remaining": duration,
        "status": "pending",
        "end_time": None
    }
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO tasks (name, duration, remaining, status, end_time)
        VALUES (?, ?, ?, ?, ?)
    """, (task["name"], task["duration"], task["remaining"], task["status"], task["end_time"]))
    conn.commit()
    conn.close()
    return jsonify(get_all_tasks())

@app.route("/start/<int:task_id>", methods=["POST"])
def start_task(task_id):
    tasks = get_all_tasks()
    running = any(t["status"] in ["running", "paused"] for t in tasks)
    for t in tasks:
        if t["id"] == task_id and (not running or t["status"] in ["paused", "done"]):
            t["status"] = "running"
            t["end_time"] = (now_il() + timedelta(seconds=t["remaining"])).isoformat()
            save_task(t)
        elif t["status"] == "running" and t["id"] != task_id:
            # רק משימה אחת יכולה לרוץ
            t["status"] = "paused"
            save_task(t)
    return jsonify(get_all_tasks())

@app.route("/pause/<int:task_id>", methods=["POST"])
def pause_task(task_id):
    tasks = get_all_tasks()
    for t in tasks:
        if t["id"] == task_id and t["status"] == "running":
            t = recalc_remaining(t)
            t["status"] = "paused"
            t["end_time"] = None
            save_task(t)
    return jsonify(get_all_tasks())

@app.route("/reset/<int:task_id>", methods=["POST"])
def reset_task(task_id):
    tasks = get_all_tasks()
    for t in tasks:
        if t["id"] == task_id:
            t["remaining"] = t["duration"]
            t["status"] = "pending"
            t["end_time"] = None
            save_task(t)
    return jsonify(get_all_tasks())

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    return jsonify(get_all_tasks())

@app.route("/update/<int:task_id>", methods=["POST"])
def update_task(task_id):
    data = request.json
    tasks = get_all_tasks()
    for t in tasks:
        if t["id"] == task_id:
            if "name" in data:
                t["name"] = data["name"]
            if "duration" in data:
                t["duration"] = int(data["duration"])
                t["remaining"] = t["duration"]
            save_task(t)
    return jsonify(get_all_tasks())

@app.route("/extend/<int:task_id>", methods=["POST"])
def extend_task(task_id):
    data = request.json
    extra = int(data.get("seconds", 0))
    tasks = get_all_tasks()
    for t in tasks:
        if t["id"] == task_id:
            t = recalc_remaining(t)
            t["duration"] += extra
            t["remaining"] += extra
            if t["status"] == "running" and t["end_time"]:
                t["end_time"] = (datetime.fromisoformat(t["end_time"]) + timedelta(seconds=extra)).isoformat()
            save_task(t)
    return jsonify(get_all_tasks())

@app.route("/skip/<int:task_id>", methods=["POST"])
def skip_task(task_id):
    tasks = get_all_tasks()
    found = False
    for t in tasks:
        if t["id"] == task_id:
            t["status"] = "done"
            t["remaining"] = 0
            t["end_time"] = None
            save_task(t)
            found = True
    if found:
        # הפעל את המשימה הבאה
        for t in tasks:
            if t["status"] == "pending":
                t["status"] = "running"
                t["end_time"] = (now_il() + timedelta(seconds=t["remaining"])).isoformat()
                save_task(t)
                break
    return jsonify(get_all_tasks())

# ================= main =================
if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
