import json, os
from datetime import datetime, timedelta
import pytz
from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder="templates")
TZ = pytz.timezone("Asia/Jerusalem")
DATA_FILE = "tasks_data.json"

def now():
    return datetime.now(TZ)

def hhmmss(sec):
    sec = int(max(0, sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"tasks": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/state")
def state():
    data = load_data()
    tasks = data.get("tasks", [])
    now_ts = now()

    for t in tasks:
        if t["status"] == "running" and t.get("end_time"):
            end = datetime.fromisoformat(t["end_time"])
            if now_ts >= end:
                t["status"] = "done"
                t["remaining"] = 0
                t["end_time"] = None
            else:
                t["remaining"] = int((end - now_ts).total_seconds())

    save_data(data)

    # חישוב שעת סיום כוללת
    total_rem = sum(t["remaining"] for t in tasks if t["status"] in ["running", "paused", "pending"])
    overall_end = now_ts + timedelta(seconds=total_rem) if total_rem > 0 else None
    end_str = overall_end.strftime("%H:%M:%S %d.%m.%Y") if overall_end else "-"

    # חישוב זמן עבודה כולל (רק משימות שסומנו כעבודה)
    total_work = sum(t["duration"] for t in tasks if t.get("is_work"))
    work_hhmmss = hhmmss(total_work)

    return jsonify({"ok": True, "tasks": tasks, "overall_end_time": end_str, "total_work_time": work_hhmmss})

@app.route("/add", methods=["POST"])
def add():
    data = load_data()
    j = request.json or {}
    name = (j.get("name") or "משימה חדשה").strip()
    h = int(j.get("hours") or 0)
    m = int(j.get("minutes") or 0)
    s = int(j.get("seconds") or 0)
    dur = h * 3600 + m * 60 + s
    pos = len(data["tasks"])
    t = {
        "id": int(datetime.now().timestamp() * 1000),
        "name": name,
        "duration": dur,
        "remaining": dur,
        "status": "pending",
        "position": pos,
        "end_time": None,
        "is_work": False
    }
    data["tasks"].append(t)
    save_data(data)
    return jsonify({"ok": True})

@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    data = load_data()
    for t in data["tasks"]:
        if t["id"] == tid:
            t["status"] = "running"
            t["end_time"] = (now() + timedelta(seconds=t["remaining"])).isoformat()
    save_data(data)
    return jsonify({"ok": True})

@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    data = load_data()
    for t in data["tasks"]:
        if t["id"] == tid and t["status"] == "running" and t["end_time"]:
            rem = int((datetime.fromisoformat(t["end_time"]) - now()).total_seconds())
            t["remaining"] = max(0, rem)
            t["status"] = "paused"
            t["end_time"] = None
    save_data(data)
    return jsonify({"ok": True})

@app.route("/set_work/<int:tid>", methods=["POST"])
def set_work(tid):
    data = load_data()
    for t in data["tasks"]:
        if t["id"] == tid:
            t["is_work"] = not t.get("is_work", False)
    save_data(data)
    return jsonify({"ok": True})

@app.route("/delete/<int:tid>", methods=["POST"])
def delete(tid):
    data = load_data()
    data["tasks"] = [t for t in data["tasks"] if t["id"] != tid]
    save_data(data)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)