from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# זיכרון זמני (אם תאפס את השרת — זה ייעלם)
tasks = []  # כל פריט: dict כמפורט למטה
tz = pytz.timezone("Asia/Jerusalem")


def now():
    return datetime.now(tz)


def to_iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else None


def hhmmss(total_seconds: float) -> str:
    if total_seconds is None:
        return ""
    total_seconds = max(0, int(total_seconds))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def recompute_chain():
    """
    לולאת-שרת קטנה: מעדכנת זמנים נותרו, מסיימת משימות שרצו,
    ומפעילה אוטומטית את הבאה בתור.
    נקראת בכל /state (כי אנחנו ממילא שואלים כל שנייה).
    """
    for idx, t in enumerate(tasks):
        if t["status"] == "running":
            remaining = (t["end_time"] - now()).total_seconds()
            if remaining <= 0:
                # סיימנו
                t["remaining"] = 0
                t["status"] = "done"
                t["start_time"] = None
                t["end_time"] = None
                # הפעלה אוטומטית של הבאה
                if idx + 1 < len(tasks):
                    nxt = tasks[idx + 1]
                    if nxt["status"] == "pending":
                        nxt["start_time"] = now()
                        nxt["end_time"] = nxt["start_time"] + timedelta(seconds=nxt["remaining"])
                        nxt["status"] = "running"
            else:
                t["remaining"] = remaining


def overall_end_time():
    """
    מחשב את שעת הסיום הכוללת של כל המשימות מתורגם ל-Asia/Jerusalem.
    אם יש משימה רצה — משתמשים ב-end_time שלה ומוסיפים אחרי זה את כל הפנדינג.
    אם אין אף אחת רצה — מתחילים מעכשיו.
    """
    if not tasks:
        return None

    # נקודת התחלה: אם יש רצה — סוף שלה; אחרת עכשיו
    base = now()
    for t in tasks:
        if t["status"] == "running" and t["end_time"]:
            # השעה המאוחרת ביותר של משימה רצה (בדרך כלל תהיה אחת)
            if t["end_time"] > base:
                base = t["end_time"]

    # הוספת כל הפנדינג (והpaused) לפי הסדר
    for t in tasks:
        if t["status"] in ("pending", "paused"):
            base = base + timedelta(seconds=max(0, int(t["remaining"])))

    return base


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
    total = hours * 3600 + minutes * 60 + seconds
    total = max(0, total)

    task = {
        "id": (tasks[-1]["id"] + 1) if tasks else 1,
        "name": name,
        "duration": total,        # זמן מקורי בשניות
        "remaining": total,       # זמן נותר דינמי
        "start_time": None,       # datetime aware
        "end_time": None,         # datetime aware
        "status": "pending"       # pending | running | paused | done
    }
    tasks.append(task)
    return jsonify({"ok": True, "task": task})


@app.route("/start/<int:task_id>", methods=["POST"])
def start_task(task_id):
    for t in tasks:
        if t["id"] == task_id and t["status"] in ("pending", "paused"):
            t["start_time"] = now()
            t["end_time"] = t["start_time"] + timedelta(seconds=max(0, int(t["remaining"])))
            t["status"] = "running"
            break
    return jsonify({"ok": True})


@app.route("/pause/<int:task_id>", methods=["POST"])
def pause_task(task_id):
    for t in tasks:
        if t["id"] == task_id and t["status"] == "running":
            t["remaining"] = max(0, (t["end_time"] - now()).total_seconds())
            t["status"] = "paused"
            t["start_time"] = None
            t["end_time"] = None
            break
    return jsonify({"ok": True})


@app.route("/reset/<int:task_id>", methods=["POST"])
def reset_task(task_id):
    for t in tasks:
        if t["id"] == task_id:
            t["remaining"] = t["duration"]
            # איפוס + התחלה מחדש אוטומטית
            t["start_time"] = now()
            t["end_time"] = t["start_time"] + timedelta(seconds=t["remaining"])
            t["status"] = "running"
            break
    return jsonify({"ok": True})


@app.route("/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    global tasks
    tasks = [t for t in tasks if t["id"] != task_id]
    return jsonify({"ok": True})


@app.route("/update/<int:task_id>", methods=["POST"])
def update_task(task_id):
    """
    עדכון שם וזמן התחלתי — **רק אם המשימה לא פעילה**.
    קלט: name (אופציונלי), hours/minutes/seconds (אופציונלי)
    """
    data = request.json or {}
    for t in tasks:
        if t["id"] == task_id and t["status"] in ("pending", "paused", "done"):
            if "name" in data:
                nm = (data.get("name") or "").strip()
                t["name"] = nm or t["name"]

            # זמן חדש? נעדכן גם duration וגם remaining בהתאם
            if any(k in data for k in ("hours", "minutes", "seconds")):
                hours = int(data.get("hours") or 0)
                minutes = int(data.get("minutes") or 0)
                seconds = int(data.get("seconds") or 0)
                total = max(0, hours * 3600 + minutes * 60 + seconds)
                t["duration"] = total
                # אם היא done – נחזיר ל-pending עם הזמן החדש
                if t["status"] == "done":
                    t["status"] = "pending"
                t["remaining"] = total
                t["start_time"] = None
                t["end_time"] = None
            break
    return jsonify({"ok": True})


@app.route("/state")
def state():
    # מעדכן ריצות/רצף
    recompute_chain()

    # מחשב שעת סיום כוללת
    end_all = overall_end_time()
    if end_all:
        end_all_str = end_all.strftime("%H:%M:%S %d.%m.%Y")
    else:
        end_all_str = "-"

    # החזרה ללקוח
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
    app.run(host="0.0.0.0", port=5000)
