from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# טיימזון לישראל
tz = pytz.timezone("Asia/Jerusalem")

# רשימת משימות
tasks = []
paused_task = None


def now():
    """זמן נוכחי עם טיימזון ישראל"""
    return datetime.now(tz)


@app.route("/")
def index():
    return render_template("index.html", tasks=tasks)


@app.route("/add", methods=["POST"])
def add_task():
    global tasks
    name = request.form.get("name", "משימה חדשה")
    hours = int(request.form.get("hours", 0) or 0)
    minutes = int(request.form.get("minutes", 0) or 0)
    seconds = int(request.form.get("seconds", 0) or 0)

    duration = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    start_time = None
    end_time = None

    # הוסף משימה לרשימה
    task = {
        "name": name,
        "duration": duration,
        "start_time": start_time,
        "end_time": end_time,
        "status": "ממתין"
    }
    tasks.append(task)

    print(f"[ADD] נוספה משימה: {task}")
    return jsonify(success=True)


@app.route("/start/<int:task_id>")
def start_task(task_id):
    global tasks, paused_task
    if 0 <= task_id < len(tasks):
        task = tasks[task_id]
        if task["status"] in ["ממתין", "מושהה"]:
            task["start_time"] = now()
            task["end_time"] = task["start_time"] + task["duration"]
            task["status"] = "רץ"
            paused_task = None
            print(f"[START] התחיל טיימר למשימה {task_id} | {task}")
            return jsonify(success=True)
    return jsonify(success=False)


@app.route("/pause/<int:task_id>")
def pause_task(task_id):
    global tasks, paused_task
    if 0 <= task_id < len(tasks):
        task = tasks[task_id]
        if task["status"] == "רץ":
            remaining = task["end_time"] - now()
            task["duration"] = remaining
            task["status"] = "מושהה"
            paused_task = task_id
            print(f"[PAUSE] השהינו את המשימה {task_id} | {task}")
            return jsonify(success=True)
    return jsonify(success=False)


@app.route("/reset/<int:task_id>")
def reset_task(task_id):
    global tasks
    if 0 <= task_id < len(tasks):
        task = tasks[task_id]
        task["start_time"] = None
        task["end_time"] = None
        task["status"] = "ממתין"
        print(f"[RESET] איפוס למשימה {task_id} | {task}")
        return jsonify(success=True)
    return jsonify(success=False)


@app.route("/delete/<int:task_id>")
def delete_task(task_id):
    global tasks
    if 0 <= task_id < len(tasks):
        removed = tasks.pop(task_id)
        print(f"[DELETE] נמחקה משימה {task_id} | {removed}")
        return jsonify(success=True)
    return jsonify(success=False)


@app.route("/state")
def state():
    global tasks
    data = []
    current_time = now()
    print(f"[STATE] בדיקת מצב בשעה {current_time}")

    for i, task in enumerate(tasks):
        status = task["status"]
        remaining = None
        end_time = task["end_time"]

        if status == "רץ" and end_time:
            try:
                remaining_td = end_time - current_time
                remaining = max(remaining_td.total_seconds(), 0)
                if remaining == 0:
                    task["status"] = "סיים"
                    print(f"[DONE] משימה {i} הסתיימה")
            except Exception as e:
                print(f"[ERROR] בעיה בחישוב זמן למשימה {i}: {e}")

        data.append({
            "id": i,
            "name": task["name"],
            "status": status,
            "end_time": end_time.strftime("%H:%M:%S") if end_time else None,
            "remaining": int(remaining) if remaining is not None else None
        })

    return jsonify(data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
