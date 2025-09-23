from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tasks.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ====== מודל משימה ======
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    duration_seconds = db.Column(db.Integer, nullable=False)
    remaining_seconds = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending, running, paused, done
    order_index = db.Column(db.Integer, nullable=False, default=0)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)

    def as_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "duration_seconds": self.duration_seconds,
            "remaining_seconds": self.remaining_seconds,
            "status": self.status,
            "order_index": self.order_index,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%H:%M:%S") if self.end_time else None,
        }

with app.app_context():
    db.create_all()

# ====== עזר ======
def recalc_end_times():
    now = datetime.now(ZoneInfo("Asia/Jerusalem"))
    for t in Task.query.order_by(Task.order_index).all():
        if t.status in ("pending", "paused"):
            now += timedelta(seconds=t.remaining_seconds)
            t.end_time = now
        elif t.status == "running" and t.end_time:
            now = t.end_time
    db.session.commit()

def start_next_task():
    next_task = Task.query.filter(Task.status == "pending").order_by(Task.order_index).first()
    if next_task:
        next_task.status = "running"
        next_task.start_time = datetime.now(ZoneInfo("Asia/Jerusalem"))
        next_task.end_time = next_task.start_time + timedelta(seconds=next_task.remaining_seconds)
        db.session.commit()
        recalc_end_times()

# ====== מסלולים ======
@app.route("/")
def index():
    tasks = Task.query.order_by(Task.order_index).all()
    total_end_time = None
    if tasks:
        last_end = None
        for t in tasks:
            if t.end_time:
                last_end = t.end_time
        if last_end:
            total_end_time = last_end.strftime("%H:%M:%S")
    return render_template("index.html", tasks=tasks, total_end_time=total_end_time)

@app.route("/tasks", methods=["POST"])
def create_task():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or "משימה"
    try:
        h = int(data.get("hours") or 0)
        m = int(data.get("minutes") or 0)
        s = int(data.get("seconds") or 0)
        duration_seconds = h * 3600 + m * 60 + s
    except (TypeError, ValueError):
        return jsonify({"error": "קלט זמן לא תקין"}), 400

    if duration_seconds <= 0:
        return jsonify({"error": "יש להגדיר זמן גדול מאפס"}), 400

    max_idx = db.session.query(db.func.max(Task.order_index)).scalar() or 0
    t = Task(
        name=name,
        duration_seconds=duration_seconds,
        remaining_seconds=duration_seconds,
        status="pending",
        order_index=max_idx + 1,
    )
    db.session.add(t)
    db.session.commit()
    recalc_end_times()
    return jsonify(t.as_dict()), 201

@app.route("/start/<int:task_id>", methods=["POST"])
def start_task(task_id):
    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    first = Task.query.order_by(Task.order_index).first()
    if task != first and task.status != "paused":
        return jsonify({"error": "אפשר להתחיל רק משימה ראשונה או מושהית"}), 400

    if Task.query.filter_by(status="running").first() and task.status != "paused":
        return jsonify({"error": "כבר יש משימה רצה"}), 400

    task.status = "running"
    task.start_time = datetime.now(ZoneInfo("Asia/Jerusalem"))
    task.end_time = task.start_time + timedelta(seconds=task.remaining_seconds)
    db.session.commit()
    recalc_end_times()
    return jsonify(task.as_dict())

@app.route("/pause/<int:task_id>", methods=["POST"])
def pause_task(task_id):
    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    if task.status != "running":
        return jsonify({"error": "לא ניתן להשהות משימה שאינה פעילה"}), 400

    now = datetime.now(ZoneInfo("Asia/Jerusalem"))
    task.remaining_seconds = max(0, int((task.end_time - now).total_seconds()))
    task.status = "paused"
    task.start_time = None
    task.end_time = None
    db.session.commit()
    recalc_end_times()
    return jsonify(task.as_dict())

@app.route("/finish/<int:task_id>", methods=["POST"])
def finish_task(task_id):
    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    task.status = "done"
    task.remaining_seconds = 0
    task.start_time = None
    task.end_time = datetime.now(ZoneInfo("Asia/Jerusalem"))
    db.session.commit()
    start_next_task()
    return jsonify(task.as_dict())

@app.route("/reset/<int:task_id>", methods=["POST"])
def reset_task(task_id):
    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    task.remaining_seconds = task.duration_seconds
    task.status = "pending"
    task.start_time = None
    task.end_time = None
    db.session.commit()
    recalc_end_times()
    return jsonify(task.as_dict())

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    db.session.delete(task)
    db.session.commit()
    recalc_end_times()
    return jsonify({"ok": True})

@app.route("/edit/<int:task_id>", methods=["POST"])
def edit_task(task_id):
    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    data = request.get_json(silent=True) or {}
    if "name" in data:
        task.name = data["name"].strip() or task.name
    try:
        h = int(data.get("hours") or 0)
        m = int(data.get("minutes") or 0)
        s = int(data.get("seconds") or 0)
        new_duration = h * 3600 + m * 60 + s
        if new_duration > 0:
            task.duration_seconds = new_duration
            task.remaining_seconds = new_duration
    except:
        pass
    task.status = "pending"
    task.start_time = None
    task.end_time = None
    db.session.commit()
    recalc_end_times()
    return jsonify(task.as_dict())

@app.route("/state")
def get_state():
    tasks = Task.query.order_by(Task.order_index).all()
    now = datetime.now(ZoneInfo("Asia/Jerusalem"))
    for t in tasks:
        if t.status == "running" and t.end_time:
            if t.end_time <= now:
                t.status = "done"
                t.remaining_seconds = 0
                db.session.commit()
                start_next_task()
    tasks = Task.query.order_by(Task.order_index).all()
    return jsonify([t.as_dict() for t in tasks])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
