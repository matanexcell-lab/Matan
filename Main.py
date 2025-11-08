import os, json
from contextlib import contextmanager
from datetime import datetime, timedelta
import pytz
from flask import Flask, jsonify, render_template, request, make_response

# ===== הגדרות =====
TZ = pytz.timezone("Asia/Jerusalem")
DATA_FILE = "tasks_data.json"

app = Flask(__name__)
tasks = []

# ===== פונקציות עזר =====
def now():
    return datetime.now(TZ)

def hhmmss(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("⚠️ שמירה נכשלה:", e)

def load_data():
    global tasks
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                tasks[:] = json.load(f)
        except Exception:
            tasks[:] = []
    else:
        tasks[:] = []

def recompute_chain():
    """מעודכן אוטומטית כשהזמן עובר"""
    now_ts = now()
    changed = False
    for i, t in enumerate(tasks):
        if t["status"] == "running" and t.get("end_time"):
            end_time = datetime.fromisoformat(t["end_time"])
            rem = int((end_time - now_ts).total_seconds())
            if rem <= 0:
                t["status"] = "done"
                t["remaining"] = 0
                t["end_time"] = None
                changed = True
                if i + 1 < len(tasks):
                    nxt = tasks[i + 1]
                    if nxt["status"] == "pending":
                        nxt["status"] = "running"
                        nxt["end_time"] = (now_ts + timedelta(seconds=nxt["remaining"])).isoformat()
            else:
                t["remaining"] = rem
    if changed:
        save_data()

def work_total_seconds():
    return sum(int(x["duration"]) for x in tasks if x.get("is_work"))

# ===== דפים =====
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/state")
def state():
    recompute_chain()
    return jsonify({
        "ok": True,
        "tasks": tasks,
        "work_total_seconds": work_total_seconds(),
        "work_total_hhmmss": hhmmss(work_total_seconds()),
        "now": now().strftime("%H:%M:%S %d.%m.%Y")
    })

# ===== פעולות =====
@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "משימה חדשה").strip()
    h, m, s = int(data.get("hours",0)), int(data.get("minutes",0)), int(data.get("seconds",0))
    duration = h*3600 + m*60 + s
    task = {
        "id": int(datetime.now().timestamp()*1000),
        "name": name,
        "duration": duration,
        "remaining": duration,
        "status": "pending",
        "end_time": None,
        "position": len(tasks),
        "is_work": False
    }
    tasks.append(task)
    save_data()
    return jsonify({"ok": True})

@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    for t in tasks:
        if t["id"] == tid:
            t["status"] = "running"
            t["end_time"] = (now() + timedelta(seconds=t["remaining"])).isoformat()
    save_data()
    return jsonify({"ok": True})

@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    for t in tasks:
        if t["id"] == tid and t["end_time"]:
            end = datetime.fromisoformat(t["end_time"])
            rem = max(0, int((end - now()).total_seconds()))
            t["remaining"] = rem
            t["status"] = "paused"
            t["end_time"] = None
    save_data()
    return jsonify({"ok": True})

@app.route("/done/<int:tid>", methods=["POST"])
def done(tid):
    for t in tasks:
        if t["id"] == tid:
            t["status"] = "done"
            t["remaining"] = 0
            t["end_time"] = None
    save_data()
    return jsonify({"ok": True})

@app.route("/set_pending/<int:tid>", methods=["POST"])
def set_pending(tid):
    for t in tasks:
        if t["id"] == tid:
            t["status"] = "pending"
            t["end_time"] = None
    save_data()
    return jsonify({"ok": True})

@app.route("/update/<int:tid>", methods=["POST"])
def update(tid):
    data = request.json or {}
    for t in tasks:
        if t["id"] == tid:
            t["name"] = data.get("name", t["name"])
            h, m, s = int(data.get("hours",0)), int(data.get("minutes",0)), int(data.get("seconds",0))
            duration = h*3600 + m*60 + s
            if duration > 0:
                t["duration"] = duration
                t["remaining"] = duration
    save_data()
    return jsonify({"ok": True})

@app.route("/extend/<int:tid>", methods=["POST"])
def extend(tid):
    data = request.json or {}
    extra = int(data.get("hours",0))*3600 + int(data.get("minutes",0))*60 + int(data.get("seconds",0))
    for t in tasks:
        if t["id"] == tid:
            t["duration"] += extra
            t["remaining"] += extra
            if t["end_time"]:
                t["end_time"] = (datetime.fromisoformat(t["end_time"]) + timedelta(seconds=extra)).isoformat()
    save_data()
    return jsonify({"ok": True})

@app.route("/delete/<int:tid>", methods=["POST"])
def delete(tid):
    global tasks
    tasks = [t for t in tasks if t["id"] != tid]
    save_data()
    return jsonify({"ok": True})

@app.route("/workflag/<int:tid>", methods=["POST"])
def workflag(tid):
    data = request.json or {}
    for t in tasks:
        if t["id"] == tid:
            t["is_work"] = bool(data.get("is_work", False))
    save_data()
    return jsonify({"ok": True})

@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    data = request.json or {}
    tid = data.get("task_id")
    new_pos = int(data.get("new_position", 1)) - 1
    ids = [t["id"] for t in tasks]
    if tid not in ids:
        return jsonify({"ok": False}), 404
    old_idx = ids.index(tid)
    ids.insert(new_pos, ids.pop(old_idx))
    tasks.sort(key=lambda x: ids.index(x["id"]))
    for i, t in enumerate(tasks):
        t["position"] = i
    save_data()
    return jsonify({"ok": True})

@app.route("/export")
def export():
    save_data()
    raw = json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2)
    resp = make_response(raw)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=tasks_export.json"
    return resp

@app.route("/import", methods=["POST"])
def import_tasks():
    data = request.json or {}
    items = data.get("tasks", [])
    global tasks
    tasks = items
    save_data()
    return jsonify({"ok": True})

if __name__ == "__main__":
    load_data()
    app.run(host="0.0.0.0", port=5000)