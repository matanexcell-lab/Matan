from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tasks.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# === פונקציות עזר ===
def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def format_time(seconds):
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02}:{m:02}:{s:02}"


# === מודל המשימה ===
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    duration = db.Column(db.Integer, default=0)  # בשניות
    status = db.Column(db.String(50), default="ממתינה")
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)

    def remaining_seconds(self):
        if self.status == "פעילה" and self.end_time:
            remaining = (self.end_time - datetime.utcnow()).total_seconds()
            return max(0, int(remaining))
        return self.duration

    def remaining_time(self):
        return format_time(int(self.remaining_seconds()))


with app.app_context():
    db.create_all()


# === פונקציה שבודקת אם משימה הסתיימה ומפעילה את הבאה ===
def check_and_update_tasks():
    active_task = Task.query.filter_by(status="פעילה").first()
    if active_task and active_task.end_time and datetime.utcnow() >= active_task.end_time:
        # המשימה הסתיימה
        active_task.status = "הסתיימה"
        active_task.duration = 0
        active_task.start_time = None
        active_task.end_time = None
        db.session.commit()

        # הפעלה אוטומטית של המשימה הבאה
        next_task = Task.query.filter_by(status="ממתינה").order_by(Task.id).first()
        if next_task:
            next_task.status = "פעילה"
            next_task.start_time = datetime.utcnow()
            next_task.end_time = next_task.start_time + timedelta(seconds=next_task.duration)
            db.session.commit()


# === ראוטים ===
@app.route("/")
def index():
    check_and_update_tasks()
    tasks = Task.query.order_by(Task.id).all()
    edit_id = request.args.get("edit")
    edit_task = Task.query.get(edit_id) if edit_id else None

    # חישוב מתי כל המשימות צפויות להסתיים
    total_end_time = None
    if tasks:
        current_time = datetime.utcnow()
        for t in tasks:
            if t.status == "פעילה" and t.end_time:
                current_time = t.end_time
            elif t.status == "ממתינה" and t.duration > 0:
                current_time += timedelta(seconds=t.duration)
        if current_time:
            total_end_time = current_time.strftime("%H:%M:%S")

    return render_template("index.html", tasks=tasks, edit_task=edit_task, total_end_time=total_end_time)


@app.route("/add", methods=["POST"])
def add_task():
    name = request.form.get("name")
    hours = safe_int(request.form.get("hours"))
    minutes = safe_int(request.form.get("minutes"))
    seconds = safe_int(request.form.get("seconds"))
    total_seconds = hours * 3600 + minutes * 60 + seconds

    if name and total_seconds > 0:
        task = Task(name=name, duration=total_seconds, status="ממתינה")
        db.session.add(task)
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/start/<int:task_id>", methods=["POST"])
def start_task(task_id):
    task = Task.query.get(task_id)
    if task:
        task.status = "פעילה"
        task.start_time = datetime.utcnow()
        task.end_time = task.start_time + timedelta(seconds=task.duration)
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/stop/<int:task_id>", methods=["POST"])
def stop_task(task_id):
    task = Task.query.get(task_id)
    if task and task.status == "פעילה":
        remaining = task.remaining_seconds()
        task.duration = int(remaining)
        task.status = "מושהית"
        task.start_time = None
        task.end_time = None
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    task = Task.query.get(task_id)
    if task:
        db.session.delete(task)
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/update/<int:task_id>", methods=["POST"])
def update_task(task_id):
    task = Task.query.get(task_id)
    if task:
        name = request.form.get("name")
        hours = safe_int(request.form.get("hours"))
        minutes = safe_int(request.form.get("minutes"))
        seconds = safe_int(request.form.get("seconds"))
        total_seconds = hours * 3600 + minutes * 60 + seconds

        if name:
            task.name = name
        if total_seconds > 0:
            task.duration = total_seconds
        task.status = "ממתינה"
        task.start_time = None
        task.end_time = None
        db.session.commit()
    return redirect(url_for("index"))
