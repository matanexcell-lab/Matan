from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# כל המשימות נשמרות כאן בזיכרון (בשרת אמיתי היינו שמים DB)
tasks = []
tz = pytz.timezone("Asia/Jerusalem")


def now():
    return datetime.now(tz)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/add", methods=["POST"])
def add_task():
    data = request.json
    name = data.get("name")
    hours = int(data.get("hours", 0))
    minutes = int(data.get("minutes", 0))
    seconds = int(data.get("seconds", 0))
    duration = timedelta(hours=hours, minutes=minutes, seconds=seconds)

    task = {
        "id": len(tasks) + 1,
        "name": name,
        "duration": duration.total_seconds(),
        "remaining": duration.total_seconds(),
        "start_time": None,
        "end_time": None,
        "status": "pending",
    }
    tasks.append(task)
    return jsonify({"message": "משימה נוספה", "task": task})


@app.route("/start/<int:task_id>", methods=["POST"])
def start_task(task_id):
    for t in tasks:
        if t["id"] == task_id:
            if t["status"] in ["pending", "paused"]:
                t["start_time"] = now()
                t["end_time"] = now() + timedelta(seconds=t["remaining"])
                t["status"] = "running"
            break
    return jsonify(tasks)


@app.route("/pause/<int:task_id>", methods=["POST"])
def pause_task(task_id):
    for t in tasks:
        if t["id"] == task_id and t["status"] == "running":
            # לחשב כמה זמן נשאר ברגע העצירה
            t["remaining"] = (t["end_time"] - now()).total_seconds()
            t["status"] = "paused"
            t["start_time"] = None
            t["end_time"] = None
            break
    return jsonify(tasks)


@app.route("/reset/<int:task_id>", methods=["POST"])
def reset_task(task_id):
    for t in tasks:
        if t["id"] == task_id:
            # לאפס לזמן המקורי
            t["remaining"] = t["duration"]
            t["status"] = "pending"
            t["start_time"] = None
            t["end_time"] = None
            break
    return jsonify(tasks)


@app.route("/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    global tasks
    tasks = [t for t in tasks if t["id"] != task_id]
    return jsonify({"message": "משימה נמחקה", "tasks": tasks})


@app.route("/state")
def state():
    # מחשב כמה זמן נשאר לכל משימה
    for t in tasks:
        if t["status"] == "running":
            remaining = (t["end_time"] - now()).total_seconds()
            if remaining <= 0:
                t["remaining"] = 0
                t["status"] = "done"
                t["start_time"] = None
                t["end_time"] = None
                # מפעיל אוטומטית את המשימה הבאה
                next_index = tasks.index(t) + 1
                if next_index < len(tasks):
                    next_task = tasks[next_index]
                    if next_task["status"] == "pending":
                        next_task["start_time"] = now()
                        next_task["end_time"] = now() + timedelta(
                            seconds=next_task["remaining"]
                        )
                        next_task["status"] = "running"
            else:
                t["remaining"] = remaining
    return jsonify(tasks)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
