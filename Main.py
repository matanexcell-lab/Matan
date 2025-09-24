from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import pytz, json, os

app = Flask(__name__)

DATA_FILE = "data.json"
tz = pytz.timezone("Asia/Jerusalem")
tasks = []  # נטען מקובץ בהפעלת השרת


def now():
    return datetime.now(tz)


def to_iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else None


def from_iso(s):
    if not s:
        return None
    # תאריכים נשמרים ISO-8601 עם timezone
    return datetime.fromisoformat(s)


def hhmmss(total_seconds: float) -> str:
    if total_seconds is None:
        return ""
    total_seconds = max(0, int(total_seconds))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------- התמדה ----------

def save_state():
    blob = []
    for t in tasks:
        blob.append({
            "id": t["id"],
            "name": t["name"],
            "duration": int(t["duration"]),
            "remaining": int(t["remaining"]),
            "status": t["status"],
            "start_time": to_iso(t["start_time"]),
            "end_time": to_iso(t["end_time"]),
        })
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"tasks": blob}, f, ensure_ascii=False)


def load_state():
    global tasks
    if not os.path.exists(DATA_FILE):
        tasks = []
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        tasks = []
        for t in data.get("tasks", []):
            tasks.append({
                "id": t["id"],
                "name": t["name"],
                "duration": int(t["duration"]),
                "remaining": int(t["remaining"]),
                "status": t["status"],
                "start_time": from_iso(t.get("start_time")),
                "end_time": from_iso(t.get("end_time")),
            })
    except Exception:
        tasks = []


# ---------- לוגיקת טיימר/רצף ----------

def recompute_chain():
    """
    מעדכן טיימרים, מסיים משימה שהגיעה ל-0,
    ומפעיל אוטומטית את הבאה בתור.
    נקרא בכל /state.
    """
    changed = False
    for idx, t in enumerate(tasks):
        if t["status"] == "running":
            remaining = (t["end_time"] - now()).total_seconds()
            if remaining <= 0:
                # סיימנו
                t["remaining"] = 0
                t["status"] = "done"
                t["start_time"] = None
                t["end_time"] = None
                changed = True
                # הפעלה אוטומטית של הבאה
                if idx + 1 < len(tasks):
                    nxt = tasks[idx + 1]
                    if nxt["status"] == "pending":
                        nxt["start_time"] = now()
                        nxt["end_time"] = nxt["start_time"] + timedelta(seconds=nxt["remaining"])
                        nxt["status"] = "running"
                        changed = True
            else:
                t["remaining"] = remaining
    if changed:
        save_state()


def overall_end_time():
    """שעת סיום כוללת: סוף הרצה + סכום remaining של Pending/Paused."""
    if not tasks:
        return None

    base = now()
    for t in tasks:
        if t["status"] == "running" and t["end_time"]:
            if t["end_time"] > base:
                base = t["end_time"]

    for t in tasks:
        if t["status"] in ("pending", "paused"):
            base = base + timedelta(seconds=max(0, int(t["remaining"])))

    return base


def any_running():
    return any(t["status"] == "running" for t in tasks)


def any_active():
    return any(t["status"] in ("running", "paused") for t in tasks)


# ---------- ראוטים ----------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/add", methods=["POST"])
def add_task():
    data = request.json or {}
    name = (data.get("name") or "").strip() or "משימה"
    hours = int(data.get("hours") or 0)
    minutes = int(data.get("minutes") or 0)
    seconds = int(data.get("seconds") or 0)
    total = max(0, hours * 3600 + minutes * 60 + seconds)

    task = {
        "id": (tasks[-1]["id"] + 1) if tasks else 1,
        "name": name,
        "duration": total,
        "remaining": total,
        "start_time": None,
        "end_time": None,
        "status": "pending"
    }
    tasks.append(task)  # חדשה תמיד למטה
    save_state()
    return jsonify({"ok": True, "task": task})


@app.route("/start/<int:task_id>", methods=["POST"])
def start_task(task_id):
    """
    התחלה:
    - Pending/Paused: מותר רק אם אין משימה רצה.
    - Done: מותר רק אם אין משימות פעילות בכלל (לא running ולא paused).
    """
    changed = False
    for t in tasks:
        if t["id"] == task_id:
            if t["status"] in ("pending", "paused") and not any_running():
                t["start_time"] = now()
                t["end_time"] = t["start_time"] + timedelta(seconds=max(0, int(t["remaining"])))
                t["status"] = "running"
                changed = True
            elif t["status"] == "done" and not any_active():
                t["remaining"] = t["duration"]
                t["start_time"] = now()
                t["end_time"] = t["start_time"] + timedelta(seconds=t["duration"])
                t["status"] = "running"
                changed = True
            break
    if changed:
        save_state()
    return jsonify({"ok": True})


@app.route("/pause/<int:task_id>", methods=["POST"])
def pause_task(task_id):
    changed = False
    for t in tasks:
        if t["id"] == task_id and t["status"] == "running":
            t["remaining"] = max(0, (t["end_time"] - now()).total_seconds())
            t["status"] = "paused"
            t["start_time"] = None
            t["end_time"] = None
            changed = True
            break
    if changed:
        save_state()
    return jsonify({"ok": True})


@app.route("/reset/<int:task_id>", methods=["POST"])
def reset_task(task_id):
    changed = False
    for t in tasks:
        if t["id"] == task_id:
            t["remaining"] = t["duration"]
            t["start_time"] = now()
            t["end_time"] = t["start_time"] + timedelta(seconds=t["remaining"])
            t["status"] = "running"
            changed = True
            break
    if changed:
        save_state()
    return jsonify({"ok": True})


@app.route("/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    global tasks
    before = len(tasks)
    tasks = [t for t in tasks if t["id"] != task_id]
    if len(tasks) != before:
        save_state()
    return jsonify({"ok": True})


@app.route("/update/<int:task_id>", methods=["POST"])
def update_task(task_id):
    """
    עריכת שם/זמן (מותר כשלא רצה).
    אם Done וקיבל זמן חדש—הופך ל-Pending עם remaining חדש.
    """
    data = request.json or {}
    changed = False
    for t in tasks:
        if t["id"] == task_id and t["status"] in ("pending", "paused", "done"):
            if "name" in data:
                nm = (data.get("name") or "").strip()
                if nm and nm != t["name"]:
                    t["name"] = nm
                    changed = True

            if any(k in data for k in ("hours", "minutes", "seconds")):
                hours = int(data.get("hours") or 0)
                minutes = int(data.get("minutes") or 0)
                seconds = int(data.get("seconds") or 0)
                total = max(0, hours * 3600 + minutes * 60 + seconds)
                t["duration"] = total
                t["remaining"] = total
                if t["status"] == "done":
                    t["status"] = "pending"
                t["start_time"] = None
                t["end_time"] = None
                changed = True
            break
    if changed:
        save_state()
    return jsonify({"ok": True})


@app.route("/extend/<int:task_id>", methods=["POST"])
def extend_task(task_id):
    """
    הארכת משימה — מוסיף שניות ל-duration ול-remaining.
    אם רצה: end_time זז קדימה וה-remaining מתעדכן.
    """
    data = request.json or {}
    extra = int(data.get("seconds") or 0)
    if extra <= 0:
        return jsonify({"ok": False, "error": "seconds must be > 0"}), 400

    changed = False
    for t in tasks:
        if t["id"] == task_id:
            t["duration"] += extra
            if t["status"] == "running":
                t["remaining"] = max(0, (t["end_time"] - now()).total_seconds()) + extra
                t["end_time"] = t["end_time"] + timedelta(seconds=extra)
            else:
                t["remaining"] += extra
            changed = True
            break

    if changed:
        save_state()
    return jsonify({"ok": True})


@app.route("/skip/<int:task_id>", methods=["POST"])
def skip_task(task_id):
    """
    דלג: מסמן המשימה הנוכחית כ-done ומפעיל אוטומטית את הבאה (אם קיימת).
    """
    changed = False
    for idx, t in enumerate(tasks):
        if t["id"] == task_id and t["status"] == "running":
            t["remaining"] = 0
            t["status"] = "done"
            t["start_time"] = None
            t["end_time"] = None
            changed = True
            if idx + 1 < len(tasks):
                nxt = tasks[idx + 1]
                if nxt["status"] == "pending":
                    nxt["start_time"] = now()
                    nxt["end_time"] = nxt["start_time"] + timedelta(seconds=nxt["remaining"])
                    nxt["status"] = "running"
            break
    if changed:
        save_state()
    return jsonify({"ok": True})


@app.route("/set_pending/<int:task_id>", methods=["POST"])
def set_pending(task_id):
    """
    הפוך ל-Pending (כשלא רצה):
    - מ- done: remaining = duration
    - מ- paused: שומר remaining
    """
    changed = False
    for t in tasks:
        if t["id"] == task_id and t["status"] in ("paused", "done", "pending"):
            if t["status"] == "done":
                t["remaining"] = t["duration"]
            t["status"] = "pending"
            t["start_time"] = None
            t["end_time"] = None
            changed = True
            break
    if changed:
        save_state()
    return jsonify({"ok": True})


@app.route("/state")
def state():
    # נקודת מצב ללקוח (הפולינג), מעדכנת רצף ורצה
    recompute_chain()
    end_all = overall_end_time()
    end_all_str = end_all.strftime("%H:%M:%S %d.%m.%Y") if end_all else "-"

    payload = []
    for t in tasks:
        payload.append({
            "id": t["id"],
            "name": t["name"],
            "status": t["status"],
            "duration": t["duration"],
            "remaining": max(0, int(t["remaining"])) if t["remaining"] is not None else 0,
            "remaining_hhmmss": hhmmss(t["remaining"]),
            "start_time": to_iso(t["start_time"]),
            "end_time": to_iso(t["end_time"]),
            "end_time_str": t["end_time"].astimezone(tz).strftime("%H:%M:%S") if t["end_time"] else "-"
        })

    return jsonify({
        "ok": True,
        "tasks": payload,
        "overall_end_time": end_all_str,
        "now": now().strftime("%H:%M:%S %d.%m.%Y")
    })


if __name__ == "__main__":
    load_state()
    # במידה ואתה מריץ ב־flask dev server, בטל debug/reload כדי שלא ימחק state בזיכרון
    app.run(host="0.0.0.0", port=5000)
