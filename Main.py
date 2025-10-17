from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# ---- Database ----
app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://meitar_user:rnw5jOCjnkfts5RBd6ZCYsIle4VkxjvL@dpg-d3aqv7ruibrs73evq640-a/meitar"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# ---- Models ----
class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    duration = db.Column(db.Integer)  # minutes
    remaining = db.Column(db.Integer)
    status = db.Column(db.String(20), default="pending")
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    position = db.Column(db.Integer, default=0)

# ---- Ensure table exists ----
def ensure_table_exists():
    try:
        insp = inspect(db.engine)
        tables = insp.get_table_names(schema="public") if "public" in insp.get_schema_names() else insp.get_table_names()
        if "tasks" not in tables:
            print("📦 Creating 'tasks' table...")
            with db.engine.begin() as conn:
                db.metadata.create_all(bind=conn)
            print("✅ 'tasks' table created successfully!")
    except Exception as e:
        print("⚠️ ensure_table_exists error:", e)


# ---- Utils ----
def israel_time():
    return datetime.now(pytz.timezone("Asia/Jerusalem"))


# ---- Routes ----
@app.route("/")
def index():
    ensure_table_exists()
    tasks = Task.query.order_by(Task.position.asc()).all()
    return render_template("index.html", tasks=tasks)


@app.route("/add", methods=["POST"])
def add_task():
    data = request.json
    new_task = Task(
        name=data["name"],
        duration=data["duration"],
        remaining=data["duration"],
        status="pending",
        position=Task.query.count(),
    )
    db.session.add(new_task)
    db.session.commit()
    return jsonify({"message": "Task added!"})


@app.route("/update", methods=["POST"])
def update_task():
    data = request.json
    task = Task.query.get(data["id"])
    if not task:
        return jsonify({"error": "Task not found"}), 404

    for key, value in data.items():
        if hasattr(task, key):
            setattr(task, key, value)
    db.session.commit()
    return jsonify({"message": "Task updated!"})


@app.route("/delete/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    task = Task.query.get(task_id)
    if task:
        db.session.delete(task)
        db.session.commit()
    return jsonify({"message": "Task deleted!"})


@app.route("/reorder", methods=["POST"])
def reorder_tasks():
    data = request.json  # list of task IDs in new order
    for i, task_id in enumerate(data["order"]):
        task = Task.query.get(task_id)
        if task:
            task.position = i
    db.session.commit()
    return jsonify({"message": "Order updated"})


@app.route("/state")
def state():
    ensure_table_exists()
    tasks = Task.query.order_by(Task.position.asc()).all()
    out = []
    now = israel_time()
    for t in tasks:
        if t.start_time and t.end_time:
            remaining = max(0, (t.end_time - now).total_seconds() / 60)
        else:
            remaining = t.remaining
        out.append({
            "id": t.id,
            "name": t.name,
            "duration": t.duration,
            "remaining": round(remaining, 2),
            "status": t.status,
            "start_time": t.start_time.isoformat() if t.start_time else None,
            "end_time": t.end_time.isoformat() if t.end_time else None,
            "position": t.position
        })
    return jsonify(out)


if __name__ == "__main__":
    ensure_table_exists()
    app.run(host="0.0.0.0", port=10000)
