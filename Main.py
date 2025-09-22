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
    tasks = load_tasks()
    now = time.time()
    changed = False
    for task in tasks:
        if task["status"] == "running" and now >= task["end_time"]:
            task["status"] = "finished"
            changed = True
    if changed:
        save_tasks(tasks)
    return jsonify(tasks)

@app.route("/add_task", methods=["POST"])
def add_task():
    data = request.get_json()
    name = data.get("name")
    seconds = int(data.get("seconds", 0))
    task = {
        "id": int(time.time() * 1000),
        "name": name,
        "duration": seconds,
        "status": "waiting",
        "start_time": 0,
        "end_time": 0,
        "paused_time": 0
    }
    tasks = load_tasks()
    tasks.append(task)
    save_tasks(tasks)
    return jsonify({"success": True})

@app.route("/start_task", methods=["POST"])
def start_task():
    data = request.get_json()
    task_id = int(data.get("id"))
    tasks = load_tasks()
    for task in tasks:
        if task["id"] == task_id and task["status"] == "waiting":
            task["status"] = "running"
            task["start_time"] = time.time()
            task["end_time"] = task["start_time"] + task["duration"]
            break
    save_tasks(tasks)
    return jsonify({"success": True})

@app.route("/pause_task", methods=["POST"])
def pause_task():
    data = request.get_json()
    task_id = int(data.get("id"))
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
    task_id = int(data.get("id"))
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
    task_id = int(data.get("id"))
    tasks = load_tasks()
    for task in tasks:
        if task["id"] == task_id:
            task["status"] = "finished"
    save_tasks(tasks)
    return jsonify({"success": True})

@app.route("/delete_task", methods=["POST"])
def delete_task():
    data = request.get_json()
    task_id = int(data.get("id"))
    tasks = load_tasks()
    tasks = [t for t in tasks if t["id"] != task_id]
    save_tasks(tasks)
    return jsonify({"success": True})

@app.route("/edit_task", methods=["POST"])
def edit_task():
    data = request.get_json()
    task_id = int(data.get("id"))
    new_name = data.get("name")
    new_seconds = int(data.get("seconds", 0))

    tasks = load_tasks()
    for task in tasks:
        if task["id"] == task_id:
            task["name"] = new_name
            task["duration"] = new_seconds
            if task["status"] in ["waiting", "paused"]:
                task["start_time"] = 0
                task["end_time"] = 0
            elif task["status"] == "running":
                task["start_time"] = time.time()
                task["end_time"] = task["start_time"] + new_seconds
    save_tasks(tasks)
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
