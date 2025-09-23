from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tasks.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# מודל משימה
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    duration = db.Column(db.Integer, default=0)  # בשניות
    status = db.Column(db.String(20), default="ממתינה")  # ממתינה / פעילה / מושהית / הסתיימה
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)

    def remaining_time(self):
        """כמה זמן נשאר למשימה"""
        if self.status == "פעילה" and self.end_time:
            remaining = (self.end_time - datetime.utcnow()).total_seconds()
            return max(int(remaining), 0)
        return self.duration

# יצירת טבלה
with app.app_context():
    db.create_all()

# פונקציה בטוחה להמרה ל-int
def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

@app.route("/")
def index():
    tasks = Task.query.all()
    edit_id = request.args.get("edit")
    edit_task = Task.query.get(edit_id) if edit_id else None
    return render_template("index.html", tasks=tasks, edit_task=edit_task)

@app.route("/add", methods=["POST"])
def add_task():
    name = request.form.get("name")
    hours = safe_int(request.form.get("hours"))
    minutes = safe_int(request.form.get("minutes"))
    seconds = safe_int(request.form.get("seconds"))
    duration = hours * 3600 + minutes * 60 + seconds
    if duration <= 0:
        duration = 0

    task = Task(name=name, duration=duration)
    db.session.add(task)
    db.session.commit()
    return redirect(url_for("index"))

@app.route("/start/<int:task_id>", methods=["POST"])
def start_task(task_id):
    task = Task.query.get(task_id)
    if task:
        task.status = "פעילה"
        task.start_time = datetime.utcnow()
        if task.duration > 0:
            task.end_time = task.start_time + timedelta(seconds=task.duration)
        db.session.commit()
    return redirect(url_for("index"))

@app.route("/stop/<int:task_id>", methods=["POST"])
def stop_task(task_id):
    task = Task.query.get(task_id)
    if task and task.status == "פעילה":
        remaining = task.remaining_time()
        task.duration = remaining
        task.status = "מושהית"
        task.end_time = None
        db.session.commit()
    return redirect(url_for("index"))

@app.route("/done/<int:task_id>", methods=["POST"])
def done_task(task_id):
    task = Task.query.get(task_id)
    if task:
        task.status = "הסתיימה"
        task.duration = 0
        task.end_time = datetime.utcnow()
        db.session.commit()
    return redirect(url_for("index"))

@app.route("/reset/<int:task_id>", methods=["POST"])
def reset_task(task_id):
    task = Task.query.get(task_id)
    if task:
        task.status = "ממתינה"
        task.start_time = None
        task.end_time = None
        db.session.commit()
    return redirect(url_for("index"))

@app.route("/edit/<int:task_id>")
def edit_task(task_id):
    return redirect(url_for("index", edit=task_id))

@app.route("/update/<int:task_id>", methods=["POST"])
def update_task(task_id):
    task = Task.query.get(task_id)
    if task:
        task.name = request.form.get("name")
        hours = safe_int(request.form.get("hours"))
        minutes = safe_int(request.form.get("minutes"))
        seconds = safe_int(request.form.get("seconds"))
        task.duration = hours * 3600 + minutes * 60 + seconds
        db.session.commit()
    return redirect(url_for("index"))

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    task = Task.query.get(task_id)
    if task:
        db.session.delete(task)
        db.session.commit()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
