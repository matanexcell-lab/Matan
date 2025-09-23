from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tasks.db"
db = SQLAlchemy(app)

tz = pytz.timezone("Asia/Jerusalem")

def now_il():
    return datetime.now(tz)

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.Integer, nullable=False)  # שניות
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    remaining_seconds = db.Column(db.Integer, default=0)
    is_paused = db.Column(db.Boolean, default=False)

with app.app_context():
    db.create_all()

@app.route("/")
def home():
    tasks = Task.query.order_by(Task.id).all()
    return render_template("index.html", tasks=tasks)

# הוספת משימה
@app.route("/add", methods=["POST"])
def add_task():
    name = request.form.get("name")
    hours = int(request.form.get("hours") or 0)
    minutes = int(request.form.get("minutes") or 0)
    seconds = int(request.form.get("seconds") or 0)
    duration = hours * 3600 + minutes * 60 + seconds
    task = Task(name=name, duration=duration)
    db.session.add(task)
    db.session.commit()
    return jsonify({"message": "Task added"})

# התחלה
@app.route("/start/<int:task_id>", methods=["POST"])
def start_task(task_id):
    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    if task.is_paused:
        task.end_time = now_il() + timedelta(seconds=task.remaining_seconds)
        task.is_paused = False
    else:
        if not task.start_time:
            task.start_time = now_il()
        task.end_time = task.start_time + timedelta(seconds=task.duration)
    db.session.commit()

    return jsonify({
        "id": task.id,
        "start_time": task.start_time.isoformat(),
        "end_time": task.end_time.isoformat()
    })

# השהיה
@app.route("/pause/<int:task_id>", methods=["POST"])
def pause_task(task_id):
    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    if not task.end_time:
        return jsonify({"error": "Task not started"}), 400

    remaining = (task.end_time - now_il()).total_seconds()
    task.remaining_seconds = max(int(remaining), 0)
    task.is_paused = True
    db.session.commit()

    return jsonify({"id": task.id, "remaining_seconds": task.remaining_seconds})

# זמן שנותר
@app.route("/remaining/<int:task_id>")
def remaining(task_id):
    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    if task.is_paused:
        remaining = task.remaining_seconds
    elif task.end_time:
        remaining = (task.end_time - now_il()).total_seconds()
    else:
        remaining = task.duration

    return jsonify({
        "id": task.id,
        "name": task.name,
        "remaining_seconds": max(int(remaining), 0),
        "end_time": task.end_time.isoformat() if task.end_time else None
    })

# עריכה
@app.route("/edit/<int:task_id>", methods=["POST"])
def edit(task_id):
    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    data = request.json
    if "name" in data:
        task.name = data["name"]
    if "duration" in data:
        task.duration = int(data["duration"])
        task.end_time = now_il() + timedelta(seconds=task.duration)
    db.session.commit()
    return jsonify({"message": "Task updated"})

# מחיקה
@app.route("/delete/<int:task_id>", methods=["DELETE"])
def delete(task_id):
    task = Task.query.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    db.session.delete(task)
    db.session.commit()
    return jsonify({"message": "Task deleted"})

# שעת סיום של כל המשימות
@app.route("/end_all")
def end_all():
    tasks = Task.query.order_by(Task.id).all()
    if not tasks:
        return jsonify({"error": "No tasks"}), 404
    last = tasks[-1]
    return jsonify({
        "end_time_all": last.end_time.isoformat() if last.end_time else None
    })

if __name__ == "__main__":
    app.run(debug=True)
