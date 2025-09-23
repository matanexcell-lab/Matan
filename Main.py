from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# שעון ישראל
TZ = pytz.timezone("Asia/Jerusalem")

def now():
    return datetime.now(TZ)

def hhmmss(td: timedelta) -> str:
    total = max(int(td.total_seconds()), 0)
    h, r = divmod(total, 3600)
    m, s = divmod(r, 60)
    return f"{h:02}:{m:02}:{s:02}"

# זיכרון בזיכרון (ללא DB)
tasks = []  # כל משימה: dict כמוגדר בהמשך

def can_start_index(idx: int) -> bool:
    """מותר להתחיל רק אם כל מה שלפניה הסתיים."""
    for j in range(idx):
        if tasks[j]["status"] != "סיים":
            return False
    return True

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/add", methods=["POST"])
def add_task():
    name = request.form.get("name", "").strip() or "משימה חדשה"
    hours = int(request.form.get("hours", 0) or 0)
    minutes = int(request.form.get("minutes", 0) or 0)
    seconds = int(request.form.get("seconds", 0) or 0)

    duration = timedelta(hours=hours, minutes=minutes, seconds=seconds)

    task = {
        "name": name,
        "original_duration": duration,  # לשם איפוס
        "duration": duration,           # זמן עבודה נוכחי (כשמושהה/ממתין)
        "start_time": None,
        "end_time": None,
        "status": "ממתין",              # ממתין/רץ/מושהה/סיים
    }
    tasks.append(task)
    return jsonify(success=True)

@app.route("/start/<int:task_id>", methods=["POST", "GET"])
def start_task(task_id):
    if 0 <= task_id < len(tasks):
        t = tasks[task_id]
        # מותר להתחיל אם מושהה, או אם ממתין והוא ה"ראשון בתור"
        if t["status"] == "מושהה":
            t["start_time"] = now()
            t["end_time"] = t["start_time"] + t["duration"]
            t["status"] = "רץ"
            return jsonify(success=True)

        if t["status"] == "ממתין" and can_start_index(task_id):
            t["start_time"] = now()
            t["end_time"] = t["start_time"] + t["duration"]
            t["status"] = "רץ"
            return jsonify(success=True)

    return jsonify(success=False)

@app.route("/pause/<int:task_id>", methods=["POST", "GET"])
def pause_task(task_id):
    if 0 <= task_id < len(tasks):
        t = tasks[task_id]
        if t["status"] == "רץ":
            rem = t["end_time"] - now()
            t["duration"] = max(rem, timedelta(seconds=0))
            t["status"] = "מושהה"
            t["start_time"] = None
            t["end_time"] = None
            return jsonify(success=True)
    return jsonify(success=False)

@app.route("/reset/<int:task_id>", methods=["POST", "GET"])
def reset_task(task_id):
    """איפוס לזמן המקורי + התחלה מיידית מחדש אוטומטית."""
    if 0 <= task_id < len(tasks):
        t = tasks[task_id]
        t["duration"] = t["original_duration"]
        t["start_time"] = now()
        t["end_time"] = t["start_time"] + t["duration"]
        t["status"] = "רץ"
        return jsonify(success=True)
    return jsonify(success=False)

@app.route("/delete/<int:task_id>", methods=["POST", "GET"])
def delete_task(task_id):
    if 0 <= task_id < len(tasks):
        tasks.pop(task_id)
        return jsonify(success=True)
    return jsonify(success=False)

@app.route("/edit/<int:task_id>", methods=["POST"])
def edit_task(task_id):
    """
    עריכת שם/זמן מותרת רק אם המשימה לא רצה.
    שינוי זמן מאפס את המונה לזמן החדש בסטטוס 'ממתין'.
    """
    if 0 <= task_id < len(tasks):
        t = tasks[task_id]
        if t["status"] == "רץ":
            return jsonify(success=False, msg="cannot edit running task")

        name = request.form.get("name", None)
        if name is not None and name.strip() != "":
            t["name"] = name.strip()

        # אם שלחו שעות/דקות/שניות – מעדכנים.
        if any(k in request.form for k in ("hours", "minutes", "seconds")):
            hours = int(request.form.get("hours", 0) or 0)
            minutes = int(request.form.get("minutes", 0) or 0)
            seconds = int(request.form.get("seconds", 0) or 0)
            new_dur = timedelta(hours=hours, minutes=minutes, seconds=seconds)
            t["original_duration"] = new_dur
            t["duration"] = new_dur
            t["start_time"] = None
            t["end_time"] = None
            t["status"] = "ממתין"

        return jsonify(success=True)

    return jsonify(success=False)

@app.route("/state")
def state():
    """
    מצב המערכת: מחשבים טיימרים, סטטוסים ורצף אוטומטי.
    """
    cur = now()
    overall_end = cur
    result = []

    for i, t in enumerate(tasks):
        status = t["status"]

        # אם רץ – מחשבים נותר; אם נגמר – מסמנים 'סיים' ומתחילים את הבאה
        if status == "רץ" and t["end_time"]:
            remaining = t["end_time"] - cur
            if remaining.total_seconds() <= 0:
                t["status"] = "סיים"
                t["start_time"] = None
                t["end_time"] = None
                t["duration"] = timedelta(seconds=0)

                # רצף אוטומטי: מתחילים את הבאה (אם יש)
                if i + 1 < len(tasks):
                    nxt = tasks[i + 1]
                    if nxt["status"] == "ממתין":
                        nxt["start_time"] = now()
                        nxt["end_time"] = nxt["start_time"] + nxt["duration"]
                        nxt["status"] = "רץ"

        # עדכון שעת סיום כוללת
        if t["end_time"] is not None:
            # t["end_time"] יודעת-אופסט (TZ), cur גם – אין קונפליקט
            if t["end_time"] > overall_end:
                overall_end = t["end_time"]

        # תצוגת "נותר"
        if t["status"] == "רץ" and t["end_time"]:
            remaining_str = hhmmss(t["end_time"] - cur)
        elif t["status"] in ("מושהה", "ממתין"):
            remaining_str = hhmmss(t["duration"])
        else:
            remaining_str = "00:00:00"

        # ערכי זמן לעריכה (כשהמשימה לא רצה)
        dur_for_edit = t["duration"] if t["status"] in ("מושהה", "ממתין") else timedelta(seconds=0)
        total = int(dur_for_edit.total_seconds())
        eh, r = divmod(total, 3600)
        em, es = divmod(r, 60)

        result.append({
            "id": i,
            "name": t["name"],
            "status": t["status"],
            "initial": hhmmss(t["original_duration"]),
            "end_time": t["end_time"].strftime("%H:%M:%S") if t["end_time"] else "",
            "remaining": remaining_str,
            "editable_hours": eh,
            "editable_minutes": em,
            "editable_seconds": es,
        })

    return jsonify(
        tasks=result,
        overall_end=overall_end.strftime("%H:%M:%S")
    )

if __name__ == "__main__":
    # להרצה מקומית
    app.run(host="0.0.0.0", port=5000)
