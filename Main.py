from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tasks.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.Integer, nullable=False)  # משך זמן התחלתי בשניות
    remaining = db.Column(db.Integer, nullable=False)  # זמן שנותר
    status = db.Column(db.String(20), default="ממתינה")  # ממתינה, פעילה, הסתיימה
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)


with app.app_context():
    db.create_all()


def safe_int(value):
    """המרה בטוחה למספר שלם (אם ריק או לא חוקי → 0)"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def update_task_status(task):
    """בודקת אם משימה הסתיימה ומעדכנת בהתאם"""
    now = datetime.utcnow()

    if task.status == "פעילה" and task.end_time and now >= task.end_time:
        # המשימה הסתיימה
        task.status = "הסתיימה"
        task.remaining = task.duration  # החזרת הזמן לזמן המקורי
        db.session.commit()

        # הפעלת המשימה הבאה אם קיימת
        next_task = Task.query.filter_by(status="ממתינה").order_by(Task.created_at.asc()).first()
        if next_task:
            next_task.status = "פעילה"
            next_task.start_time = now
            next_task.end_time = now + timedelta(seconds=next_task.remaining)
            db.session.commit()


@app.route("/")
def index():
    tasks = Task.query.order_by(Task.created_at.desc()).all()

    # עדכון סטטוס לפני הצגה
    for task in tasks:
        update_task_status(task)

    return render_template("index.html", tasks=tasks)


@app.route("/add", methods=["POST"])
def add_task():
    name = request.form["name"]
    hours = safe_int(request.form.get("hours"))
    minutes = safe_int(request.form.get("minutes"))
    seconds = safe_int(request.form.get("seconds"))
    duration = hours * 3600 + minutes * 60 + seconds

    if duration <= 0:
        duration = 1  # ברירת מחדל כדי לא להיתקע

    new_task = Task(
        name=name,
        duration=duration,
        remaining=duration,
        status="ממתינה"
    )
    db.session.add(new_task)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/start/<int:task_id>")
def start_task(task_id):
    task = Task.query.get_or_404(task_id)

    if task.status == "ממתינה":
        task.status = "פעילה"
        task.start_time = datetime.utcnow()
        task.end_time = task.start_time + timedelta(seconds=task.remaining)
        db.session.commit()

    return redirect(url_for("index"))


@app.route("/pause/<int:task_id>")
def pause_task(task_id):
    task = Task.query.get_or_404(task_id)

    if task.status == "פעילה":
        now = datetime.utcnow()
        task.remaining = max(0, int((task.end_time - now).total_seconds()))
        task.status = "ממתינה"
        task.start_time = None
        task.end_time = None
        db.session.commit()

    return redirect(url_for("index"))


@app.route("/reset/<int:task_id>")
def reset_task(task_id):
    task = Task.query.get_or_404(task_id)

    task.remaining = task.duration
    task.status = "ממתינה"
    task.start_time = None
    task.end_time = None
    db.session.commit()

    return redirect(url_for("index"))


@app.route("/finish/<int:task_id>")
def finish_task(task_id):
    task = Task.query.get_or_404(task_id)
    task.status = "הסתיימה"
    task.remaining = task.duration
    task.start_time = None
    task.end_time = None
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/delete/<int:task_id>")
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/edit/<int:task_id>", methods=["POST"])
def edit_task(task_id):
    task = Task.query.get_or_404(task_id)
    task.name = request.form["name"]
    hours = safe_int(request.form.get("hours"))
    minutes = safe_int(request.form.get("minutes"))
    seconds = safe_int(request.form.get("seconds"))
    task.duration = hours * 3600 + minutes * 60 + seconds
    task.remaining = task.duration
    task.status = "ממתינה"
    task.start_time = None
    task.end_time = None
    db.session.commit()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
