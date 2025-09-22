from flask import Flask, render_template, request, jsonify
import json, os, time

app = Flask(__name__)

TASKS_FILE = "tasks.json"

def load_tasks():
    if not os.path.exists(TASKS_FILE):
        return []
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_tasks(tasks):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/get_tasks")
def get_tasks():
    return jsonify(load_tasks())

@app.route("/add_task", methods=["POST"])
def add_task():
    data = request.get_json()
    name = data.get("name")
    seconds = int(data.get("seconds", 0))

    start_time = time.time()
    end_time = start_time + seconds

    task = {
        "id": int(start_time),
        "name": name,
        "duration": seconds,
        "status": "running",
        "start_time": start_time,
        "end_time": end_time,
        "paused_time": 0
    }

    tasks = load_tasks()
    tasks.append(task)
    save_tasks(tasks)

    return jsonify({"success": True, "task": task})

@app.route("/pause_task", methods=["POST"])
def pause_task():
    data = request.get_json()
    task_id = data.get("id")
    tasks = load_tasks()
    for task in tasks:
        if task["id"] == task_id and task["status"] == "running":
            task["status"] = "paused"
            task["paused_time"] = time.time()
    save_tasks(tasks)
    return jsonify({"success": True})

@app.route("/resume_task", methods=["POST"])
def resume_task():
    data = request.get_json()
    task_id = data.get("id")
    tasks = load_tasks()
    for task in tasks:
        if task["id"] == task_id and task["status"] == "paused":
            paused_duration = time.time() - task["paused_time"]
            task["end_time"] += paused_duration
            task["status"] = "running"
            task["paused_time"] = 0
    save_tasks(tasks)
    return jsonify({"success": True})

@app.route("/finish_task", methods=["POST"])
def finish_task():
    data = request.get_json()
    task_id = data.get("id")
    tasks = load_tasks()
    for task in tasks:
        if task["id"] == task_id:
            task["status"] = "finished"
    save_tasks(tasks)
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
