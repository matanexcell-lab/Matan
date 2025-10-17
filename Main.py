# Main.py
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from datetime import datetime
import pytz
import os

app = Flask(__name__)

# ---- DB URI (עדיף מה-ENV), כולל תיקון postgres:// -> postgresql:// ----
db_url = os.getenv("DATABASE_URL", "postgresql://meitar_user:rnw5jOCjnkfts5RBd6ZCYsIle4VkxjvL@dpg-d3aqv7ruibrs73evq640-a/meitar")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# ---- Models ----
class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=True)
    duration = db.Column(db.Integer, nullable=True)        # דקות
    remaining = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default="pending")
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    position = db.Column(db.Integer, default=0)


# ---- יצירת טבלה באתחול (חזק ובטוח ל-Render) ----
def bootstrap_db():
    with app.app_context():
        try:
            insp = inspect(db.engine)
            tables = insp.get_table_names()
            if "tasks" not in tables:
                # ניסיון ראשון: ORM
                db.create_all()
                insp2 = inspect(db.engine)
                if "tasks" in insp2.get_table_names():
                    print("✅ 'tasks' created via SQLAlchemy create_all()")
                    return
                # פולבק: SQL גולמי
                with db.engine.begin() as conn:
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS tasks (
                            id SERIAL PRIMARY KEY,
                            name VARCHAR(100),
                            duration INTEGER,
                            remaining INTEGER,
                            status VARCHAR(20) DEFAULT 'pending',
                            start_time TIMESTAMP WITH TIME ZONE NULL,
                            end_time   TIMESTAMP WITH TIME ZONE NULL,
                            position INTEGER DEFAULT 0
                        );
                    """))
                print("✅ 'tasks' created via raw SQL fallback")
            else:
                print("ℹ️ 'tasks' table already exists")
        except Exception as e:
            print("⚠️ bootstrap_db error:", e)

bootstrap_db()


# ---- Utils ----
def israel_time():
    return datetime.now(pytz.timezone("Asia/Jerusalem"))


# ---- Routes ----
@app.route("/")
def index():
    # אין צורך לקרוא שוב – כבר ביצענו באתחול
    tasks = Task.query.order_by(Task.position.asc()).all()
    return render_template("index.html", tasks=tasks)


@app.route("/add", methods=["POST"])
def add_task():
    data = request.json or {}
    name = data.get("name", "").strip()
    duration = int(data.get("duration") or 0)
    if not name or duration <= 0:
        return jsonify({"error": "שם ומשך (בדקות) חובה"}), 400

    new_task = Task(
        name=name,
        duration=duration,
        remaining=duration,
        status="pending",
        position=Task.query.count(),
    )
    db.session.add(new_task)
    db.session.commit()
    return jsonify({"message": "Task added!"})


@app.route("/update", methods=["POST"])
def update_task():
    data = request.json or {}
    task = Task.query.get(data.get("id"))
    if not task:
        return jsonify({"error": "Task not found"}), 404

    # עדכון שדות קיימים בלבד
    for key in ["name", "duration", "remaining", "status", "start_time", "end_time", "position"]:
        if key in data:
            setattr(task, key, data[key])
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
    data = request.json or {}
    order = data.get("order", [])
    for i, task_id in enumerate(order):
        task = Task.query.get(task_id)
        if task:
            task.position = i
    db.session.commit()
    return jsonify({"message": "Order updated"})


@app.route("/state")
def state():
    tasks = Task.query.order_by(Task.position.asc()).all()
    out = []
    now = israel_time()
    for t in tasks:
        if t.start_time and t.end_time:
            remaining = max(0, (t.end_time - now).total_seconds() / 60)
        else:
            remaining = t.remaining if t.remaining is not None else t.duration
        out.append({
            "id": t.id,
            "name": t.name,
            "duration": t.duration,
            "remaining": round(remaining or 0, 2),
            "status": t.status,
            "start_time": t.start_time.isoformat() if t.start_time else None,
            "end_time": t.end_time.isoformat() if t.end_time else None,
            "position": t.position
        })
    return jsonify(out)


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
