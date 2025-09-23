from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tasks.db"
db = SQLAlchemy(app)

# ===== מודל טבלה =====
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    duration = db.Column(db.Integer, nullable=False)  # משך בשניות
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="מושהה")

    def remaining(self):
        if self.status == "רץ" and self.end_time:
            delta = self.end_time - datetime.utcnow()
            return max(int(delta.total_seconds()), 0)
        return self.duration

    def formatted_remaining(self):
        total = self.remaining()
        h, m = divmod(total, 3600)
        m, s = divmod(m, 60)
        return f"{h:02}:{m:02}:{s:02}"

with app.app_context():
    db.create_all()

# ===== דף ראשי =====
@app.route("/")
def index():
    # סדר מלמעלה למטה (כל חדשה מתווספת למטה)
    tasks = Task.query.order_by(Task.id.asc()).all()
    return render_template("index.html", tasks=tasks)

# ===== הוספת משימה =====
@app.route("/add", methods=["POST"])
def add_task():
    name = request.form["name"]
    hours = int(request.form.get("hours", 0))
    minutes = int(request.form.get("minutes", 0))
    seconds = int(request.form.get("seconds", 0))
    duration = hours * 3600 + minutes * 60 + seconds
    if duration <= 0:
        duration = 1
    new_task = Task(name=name, duration=duration, status="מושהה")
    db.session.add(new_task)
    db.session.commit()
    return redirect(url_for("index"))

# ===== התחלת משימה =====
@app.route("/start/<int:task_id>")
def start_task(task_id):
    task = Task.query.get(task_id)
    if task:
        task.start_time = datetime.utcnow()
        task.end_time = task.start_time + timedelta(seconds=task.duration)
        task.status = "רץ"
        db.session.commit()
    return redirect(url_for("index"))

# ===== עצירת משימה =====
@app.route("/pause/<int:task_id>")
def pause_task(task_id):
    task = Task.query.get(task_id)
    if task and task.status == "רץ":
        task.duration = task.remaining()
        task.status = "מושהה"
        task.start_time = None
        task.end_time = None
        db.session.commit()
    return redirect(url_for("index"))

# ===== איפוס משימה =====
@app.route("/reset/<int:task_id>")
def reset_task(task_id):
    task = Task.query.get(task_id)
    if task:
        task.status = "מושהה"
        task.start_time = None
        task.end_time = None
        db.session.commit()
    return redirect(url_for("index"))

# ===== סיום משימה =====
@app.route("/finish/<int:task_id>")
def finish_task(task_id):
    task = Task.query.get(task_id)
    if task:
        task.status = "הסתיים"
        task.end_time = datetime.utcnow()
        db.session.commit()

        # התחלה אוטומטית של המשימה הבאה
        next_task = Task.query.filter(Task.id > task.id, Task.status == "מושהה") \
                              .order_by(Task.id.asc()).first()
        if next_task:
            next_task.start_time = datetime.utcnow()
            next_task.end_time = next_task.start_time + timedelta(seconds=next_task.duration)
            next_task.status = "רץ"
            db.session.commit()
    return redirect(url_for("index"))

# ===== עריכת משימה =====
@app.route("/edit/<int:task_id>", methods=["POST"])
def edit_task(task_id):
    task = Task.query.get(task_id)
    if task:
        task.name = request.form["name"]
        hours = int(request.form.get("hours", 0))
        minutes = int(request.form.get("minutes", 0))
        seconds = int(request.form.get("seconds", 0))
        task.duration = hours * 3600 + minutes * 60 + seconds
        task.start_time = None
        task.end_time = None
        task.status = "מושהה"
        db.session.commit()
    return redirect(url_for("index"))

# ===== מחיקת משימה =====
@app.route("/delete/<int:task_id>")
def delete_task(task_id):
    task = Task.query.get(task_id)
    if task:
        db.session.delete(task)
        db.session.commit()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
