from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tasks.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), default="ממתינה")  # ממתינה / פעילה / מושהית / הסתיימה
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)


with app.app_context():
    db.create_all()


def recalc_schedule():
    """חשב מחדש את זמני ההתחלה/סיום לכל המשימות שעדיין לא הסתיימו"""
    tasks = Task.query.filter(Task.status.in_(["ממתינה", "פעילה", "מושהית"])) \
                      .order_by(Task.created_at.asc()).all()

    active_task = Task.query.filter_by(status="פעילה").first()
    if active_task and active_task.end_time:
        current_time = active_task.end_time
    else:
        current_time = datetime.utcnow()

    for task in tasks:
        if task.status in ["ממתינה", "מושהית"]:
            if task.duration_minutes:
                task.start_time = current_time
                task.end_time = current_time + timedelta(minutes=task.duration_minutes)
                current_time = task.end_time
    db.session.commit()


@app.route("/")
def index():
    tasks = Task.query.order_by(Task.created_at.asc()).all()
    return render_template("index.html", tasks=tasks)


@app.route("/add", methods=["POST"])
def add_task():
    name = request.form.get("name")
    duration = request.form.get("duration")

    if name:
        task = Task(
            name=name,
            duration_minutes=int(duration) if duration else None,
            status="ממתינה"
        )
        db.session.add(task)
        db.session.commit()
        recalc_schedule()

    return redirect(url_for("index"))


@app.route("/start/<int:task_id>")
def start_task(task_id):
    task = Task.query.get(task_id)
    if task and task.status in ["ממתינה", "מושהית"]:
        task.status = "פעילה"
        task.start_time = datetime.utcnow()
        if task.duration_minutes:
            task.end_time = task.start_time + timedelta(minutes=task.duration_minutes)
        db.session.commit()
        recalc_schedule()
    return redirect(url_for("index"))


@app.route("/pause/<int:task_id>")
def pause_task(task_id):
    task = Task.query.get(task_id)
    if task and task.status == "פעילה":
        task.status = "מושהית"
        if task.start_time:
            elapsed = (datetime.utcnow() - task.start_time).seconds // 60
            if task.duration_minutes:
                task.duration_minutes = max(0, task.duration_minutes - elapsed)
        db.session.commit()
        recalc_schedule()
    return redirect(url_for("index"))


@app.route("/resume/<int:task_id>")
def resume_task(task_id):
    task = Task.query.get(task_id)
    if task and task.status == "מושהית":
        task.status = "פעילה"
        task.start_time = datetime.utcnow()
        if task.duration_minutes:
            task.end_time = task.start_time + timedelta(minutes=task.duration_minutes)
        db.session.commit()
        recalc_schedule()
    return redirect(url_for("index"))


@app.route("/finish/<int:task_id>")
def finish_task(task_id):
    task = Task.query.get(task_id)
    if task:
        task.status = "הסתיימה"
        task.end_time = datetime.utcnow()
        db.session.commit()
        recalc_schedule()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
