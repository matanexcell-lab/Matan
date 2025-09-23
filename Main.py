from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

tz = pytz.timezone("Asia/Jerusalem")

tasks = []


def now():
    return datetime.now(tz)


def format_td(td: timedelta):
    total_seconds = int(td.total_seconds())
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02}:{m:02}:{s:02}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/add", methods=["POST"])
def add_task():
    name = request.form.get("name", "משימה חדשה")
    hours = int(request.form.get("hours", 0) or 0)
    minutes = int(request.form.get("minutes", 0) or 0)
    seconds = int(request.form.get("seconds", 0) or 0)

    duration = timedelta(hours=hours, minutes=minutes, seconds=seconds)

    task = {
        "name": name,
        "original_duration": duration,
        "duration": duration,
        "start_time": None,
        "end_time": None,
        "status": "ממתין"
    }
    tasks.append(task)
    return jsonify(success=True)


@app.route("/start/<int:task_id>")
def start_task(task_id):
    if 0 <= task_id < len(tasks):
        task = tasks[task_id]
        if task["status"] in ["ממתין", "מושהה"]:
            task["start_time"] = now()
            task["end_time"] = task["start_time"] + task["duration"]
            task["status"] = "רץ"
            return jsonify(success=True)
    return jsonify(success=False)


@app.route("/pause/<int:task_id>")
def pause_task(task_id):
    if 0 <= task_id < len(tasks):
        task = tasks[task_id]
        if task["status"] == "רץ":
            remaining = task["end_time"] - now()
            task["duration"] = max(remaining, timedelta(seconds=0))
            task["status"] = "מושהה"
            return jsonify(success=True)
    return jsonify(success=False)


@app.route("/reset/<int:task_id>")
def reset_task(task_id):
    if 0 <= task_id < len(tasks):
        task = tasks[task_id]
        task["duration"] = task["original_duration"]
        task["start_time"] = None
        task["end_time"] = None
        task["status"] = "ממתין"
        return jsonify(success=True)
    return jsonify(success=False)


@app.route("/delete/<int:task_id>")
def delete_task(task_id):
    if 0 <= task_id < len(tasks):
        tasks.pop(task_id)
        return jsonify(success=True)
    return jsonify(success=False)


@app.route("/edit/<int:task_id>", methods=["POST"])
def edit_task(task_id):
    if 0 <= task_id < len(tasks):
        task = tasks[task_id]
        if task["status"] not in ["רץ"]:  # אפשר לערוך רק אם לא רץ
            new_name = request.form.get("name", task["name"])
            hours = int(request.form.get("hours", 0) or 0)
            minutes = int(request.form.get("minutes", 0) or 0)
            seconds = int(request.form.get("seconds", 0) or 0)
            new_duration = timedelta(hours=hours, minutes=minutes, seconds=seconds)

            task["name"] = new_name
            task["original_duration"] = new_duration
            task["duration"] = new_duration
            task["start_time"] = None
            task["end_time"] = None
            task["status"] = "ממתין"
            return jsonify(success=True)
    return jsonify(success=False)


@app.route("/state")
def state():
    data = []
    current_time = now()
    overall_end = current_time

    for i, task in enumerate(tasks):
        status = task["status"]
        remaining = None
        end_time = task["end_time"]

        if status == "רץ" and end_time:
            remaining_td = end_time - current_time
            remaining = max(remaining_td.total_seconds(), 0)

            if remaining == 0:
                task["status"] = "סיים"

                # התחלת המשימה הבאה אוטומטית
                if i + 1 < len(tasks):
                    next_task = tasks[i + 1]
                    if next_task["status"] == "ממתין":
                        next_task["start_time"] = now()
                        next_task["end_time"] = next_task["start_time"] + next_task["duration"]
                        next_task["status"] = "רץ"

        if task["end_time"]:
            overall_end = max(overall_end, task["end_time"])

        data.append({
            "id": i,
            "name": task["name"],
            "status": task["status"],
            "initial_duration": format_td(task["original_duration"]),
            "end_time": task["end_time"].strftime("%H:%M:%S") if task["end_time"] else None,
            "remaining": format_td(task["end_time"] - current_time) if status == "רץ" and task["end_time"] else (
                format_td(task["duration"]) if status in ["מושהה", "ממתין"] else "00:00:00"
            )
        })

    return jsonify(tasks=data, overall_end=overall_end.strftime("%H:%M:%S"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
