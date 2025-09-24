import sqlite3
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, render_template
import pytz

# ===== Settings =====
DB_FILE = "tasks.db"
TZ = pytz.timezone("Asia/Jerusalem")

app = Flask(__name__)

# ===== DB utils =====
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        name      TEXT NOT NULL,
        duration  INTEGER NOT NULL,
        remaining INTEGER NOT NULL,
        status    TEXT NOT NULL,  -- pending | running | paused | done
        end_time  TEXT            -- ISO8601 with tz
    )
    """)
    conn.commit()
    conn.close()

# >>> חשוב: נריץ יצירת טבלה גם כשעולים עם gunicorn (ברנדר)
init_db()

def row2dict(r): return dict(r)

# ===== Time helpers =====
def now():
    return datetime.now(TZ)

def to_iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else None

def from_iso(s):
    if not s: return None
    return datetime.fromisoformat(s)

def hhmmss(total_seconds):
    if total_seconds is None: return ""
    total_seconds = max(0, int(total_seconds))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ===== CRUD helpers =====
def fetch_all():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM tasks ORDER BY id ASC")
    rows = [row2dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def fetch_one(task_id):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
    r = c.fetchone()
    conn.close()
    return row2dict(r) if r else None

def save_task(t):
    conn = db()
    c = conn.cursor()
    c.execute("""UPDATE tasks
                 SET name=?, duration=?, remaining=?, status=?, end_time=?
                 WHERE id=?""",
              (t["name"], int(t["duration"]), int(t["remaining"]),
               t["status"], t["end_time"], t["id"]))
    conn.commit()
    conn.close()

def insert_task(name, duration):
    conn = db()
    c = conn.cursor()
    c.execute("""INSERT INTO tasks (name, duration, remaining, status, end_time)
                 VALUES (?, ?, ?, ?, ?)""",
              (name, int(duration), int(duration), "pending", None))
    conn.commit()
    conn.close()

def delete_task_db(task_id):
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

def any_running():
    return any(t["status"] == "running" for t in fetch_all())

def any_active():
    return any(t["status"] in ("running","paused") for t in fetch_all())

# ===== Chain logic =====
def recompute_chain_in_db():
    """
    מעדכן remaining למשימות רצות, מסיים כשעבר end_time,
    ומפעיל אוטומטית את המשימה הבאה (pending).
    """
    tasks = fetch_all()
    now_ts = now()

    for t in tasks:
        if t["status"] == "running" and t["end_time"]:
            et = from_iso(t["end_time"])
            rem = int((et - now_ts).total_seconds())
            if rem <= 0:
                # סיים
                t["remaining"] = 0
                t["status"] = "done"
                t["end_time"] = None
                save_task(t)

                # הפעל את הבאה
                tasks2 = fetch_all()
                ids = [x["id"] for x in tasks2]
                idx = ids.index(t["id"]) if t["id"] in ids else -1
                if idx != -1 and idx + 1 < len(tasks2):
                    nxt = fetch_one(tasks2[idx+1]["id"])
                    if nxt and nxt["status"] == "pending":
                        nxt["end_time"] = to_iso(now_ts + timedelta(seconds=int(nxt["remaining"])))
                        nxt["status"] = "running"
                        save_task(nxt)
            else:
                if rem != t["remaining"]:
                    t["remaining"] = rem
                    save_task(t)

def overall_end_time_calc():
    tasks = fetch_all()
    if not tasks: return None
    base = now()
    # סוף משימה רצה, אם יש
    for t in tasks:
        if t["status"] == "running" and t["end_time"]:
            et = from_iso(t["end_time"])
            if et and et > base:
                base = et
    # הוסף Pending/Paused לפי הסדר
    for t in tasks:
        if t["status"] in ("pending", "paused"):
            base = base + timedelta(seconds=int(max(0, int(t["remaining"]))))
    return base

# ===== Routes =====
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "").strip() or "משימה"
    # תמיכה ב־hours/minutes/seconds או duration
    hours   = int(data.get("hours") or 0)
    minutes = int(data.get("minutes") or 0)
    seconds = int(data.get("seconds") or 0)
    duration = int(data.get("duration") or (hours*3600 + minutes*60 + seconds))
    duration = max(0, duration)
    insert_task(name, duration)
    return jsonify({"ok": True})

@app.route("/start/<int:task_id>", methods=["POST"])
def start(task_id):
    t = fetch_one(task_id)
    if not t:
        return jsonify({"ok": False, "error": "not found"}), 404

    # Pending/Paused: רק אם אין רצה אחרת
    if t["status"] in ("pending","paused") and not any_running():
        t["end_time"] = to_iso(now() + timedelta(seconds=int(t["remaining"])))
        t["status"] = "running"
        save_task(t)
    # Done: רק אם אין משימות פעילות כלל
    elif t["status"] == "done" and not any_active():
        t["remaining"] = int(t["duration"])
        t["end_time"] = to_iso(now() + timedelta(seconds=int(t["remaining"])))
        t["status"] = "running"
        save_task(t)

    return jsonify({"ok": True})

@app.route("/pause/<int:task_id>", methods=["POST"])
def pause(task_id):
    t = fetch_one(task_id)
    if t and t["status"] == "running" and t["end_time"]:
        et = from_iso(t["end_time"])
        rem = int((et - now()).total_seconds())
        t["remaining"] = max(0, rem)
        t["status"] = "paused"
        t["end_time"] = None
        save_task(t)
    return jsonify({"ok": True})

@app.route("/reset/<int:task_id>", methods=["POST"])
def reset(task_id):
    t = fetch_one(task_id)
    if t:
        t["remaining"] = int(t["duration"])
        t["status"] = "running"
        t["end_time"] = to_iso(now() + timedelta(seconds=int(t["remaining"])))
        save_task(t)
    return jsonify({"ok": True})

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete(task_id):
    delete_task_db(task_id)
    return jsonify({"ok": True})

@app.route("/update/<int:task_id>", methods=["POST"])
def update(task_id):
    t = fetch_one(task_id)
    if not t:
        return jsonify({"ok": False, "error": "not found"}), 404

    # עריכה מותרת כשהיא לא רצה
    if t["status"] not in ("pending","paused","done"):
        return jsonify({"ok": False, "error": "cannot edit running task"}), 400

    data = request.json or {}
    if "name" in data:
        nm = (data.get("name") or "").strip()
        if nm: t["name"] = nm

    if any(k in data for k in ("hours","minutes","seconds","duration")):
        hours   = int(data.get("hours") or 0)
        minutes = int(data.get("minutes") or 0)
        seconds = int(data.get("seconds") or 0)
        duration = int(data.get("duration") or (hours*3600 + minutes*60 + seconds))
        duration = max(0, duration)
        t["duration"] = duration
        t["remaining"] = duration
        if t["status"] == "done":
            t["status"] = "pending"
        t["end_time"] = None

    save_task(t)
    return jsonify({"ok": True})

@app.route("/extend/<int:task_id>", methods=["POST"])
def extend(task_id):
    t = fetch_one(task_id)
    if not t:
        return jsonify({"ok": False, "error": "not found"}), 404

    data = request.json or {}
    extra = int(data.get("seconds") or 0)
    if extra <= 0:
        return jsonify({"ok": False, "error": "seconds must be > 0"}), 400

    t["duration"] = int(t["duration"]) + extra
    if t["status"] == "running" and t["end_time"]:
        et = from_iso(t["end_time"])
        rem = max(0, int((et - now()).total_seconds()))
        t["remaining"] = rem + extra
        t["end_time"] = to_iso(et + timedelta(seconds=extra))
    else:
        t["remaining"] = int(t["remaining"]) + extra

    save_task(t)
    return jsonify({"ok": True})

@app.route("/skip/<int:task_id>", methods=["POST"])
def skip(task_id):
    t = fetch_one(task_id)
    if t and t["status"] == "running":
        t["status"] = "done"
        t["remaining"] = 0
        t["end_time"] = None
        save_task(t)

        # הבא בתור
        tasks = fetch_all()
        ids = [x["id"] for x in tasks]
        idx = ids.index(task_id) if task_id in ids else -1
        if idx != -1 and idx + 1 < len(tasks):
            nxt = fetch_one(tasks[idx+1]["id"])
            if nxt and nxt["status"] == "pending":
                nxt["status"] = "running"
                nxt["end_time"] = to_iso(now() + timedelta(seconds=int(nxt["remaining"])))
                save_task(nxt)

    return jsonify({"ok": True})

@app.route("/set_pending/<int:task_id>", methods=["POST"])
def set_pending(task_id):
    t = fetch_one(task_id)
    if t and t["status"] in ("paused","done","pending"):
        if t["status"] == "done":
            t["remaining"] = int(t["duration"])
        t["status"] = "pending"
        t["end_time"] = None
        save_task(t)
    return jsonify({"ok": True})

@app.route("/state")
def state():
    # מעדכן ריצות/רצף ומחזיר מצב מלא
    recompute_chain_in_db()
    tasks = fetch_all()
    payload = []
    for t in tasks:
        rem = int(t["remaining"])
        if t["status"] == "running" and t["end_time"]:
            et = from_iso(t["end_time"])
            rem = max(0, int((et - now()).total_seconds()))
        payload.append({
            "id": t["id"],
            "name": t["name"],
            "status": t["status"],
            "duration": int(t["duration"]),
            "remaining": rem,
            "remaining_hhmmss": hhmmss(rem),
            "end_time": t["end_time"],
            "end_time_str": datetime.fromisoformat(t["end_time"]).astimezone(TZ).strftime("%H:%M:%S") if t["end_time"] else "-"
        })

    end_all = overall_end_time_calc()
    end_all_str = end_all.strftime("%H:%M:%S %d.%m.%Y") if end_all else "-"

    return jsonify({
        "ok": True,
        "tasks": payload,
        "overall_end_time": end_all_str,
        "now": now().strftime("%H:%M:%S %d.%m.%Y")
    })

if __name__ == "__main__":
    # להרצה מקומית
    app.run(host="0.0.0.0", port=5000)
