from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tasks.db'
db = SQLAlchemy(app)


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.Integer, nullable=False)  # בשניות
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="pending")  # pending / running / paused / finished

    def remaining_time(self):
        if self.status == "running" and self.start_time:
            elapsed = (datetime.utcnow() - self.start_time).total_seconds()
            return max(0, self.duration - int(elapsed))
        elif self.status == "paused" and self.end_time:
            return max(0, self.duration - int((self.end_time - self.start_time).total_seconds()))
        elif self.status == "finished":
            return 0
        return self.duration


# יצירת הטבלאות
with app.app_context():
    db.create_all()


@app.route("/")
def index():
    tasks = Task.query.order_by(Task.created_at.asc()).all()
    return render_template("index.html", tasks=tasks, datetime=datetime)


@app.route("/add", methods=["POST"])
def add_task():
    name = request.form["name"]
    hours = int(request.form.get("hours", 0) or 0)
    minutes = int(request.form.get("minutes", 0) or 0)
    seconds = int(request.form.get("seconds", 0) or 0)
    duration = hours * 3600 + minutes * 60 + seconds
    new_task = Task(name=name, duration=duration)
    db.session.add(new_task)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/start/<int:task_id>")
def start_task(task_id):
    task = Task.query.get(task_id)
    if task and task.status == "pending":
        task.start_time = datetime.utcnow()
        task.end_time = task.start_time + timedelta(seconds=task.duration)
        task.status = "running"
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/pause/<int:task_id>")
def pause_task(task_id):
    task = Task.query.get(task_id)
    if task and task.status == "running":
        task.end_time = datetime.utcnow()
        task.status = "paused"
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/resume/<int:task_id>")
def resume_task(task_id):
    task = Task.query.get(task_id)
    if task and task.status == "paused":
        paused_duration = (task.end_time - task.start_time).total_seconds()
        task.duration -= int(paused_duration)
        task.start_time = datetime.utcnow()
        task.end_time = task.start_time + timedelta(seconds=task.duration)
        task.status = "running"
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/finish/<int:task_id>")
def finish_task(task_id):
    task = Task.query.get(task_id)
    if task:
        task.status = "finished"
        task.end_time = datetime.utcnow()
        db.session.commit()
    # אחרי סיום – להתחיל את המשימה הבאה
    return redirect(url_for("start_next"))


@app.route("/start_next")
def start_next():
    task = Task.query.filter(Task.status == "pending").order_by(Task.created_at.asc()).first()
    if task:
        task.start_time = datetime.utcnow()
        task.end_time = task.start_time + timedelta(seconds=task.duration)
        task.status = "running"
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/edit/<int:task_id>", methods=["POST"])
def edit_task(task_id):
    task = Task.query.get(task_id)
    if task and task.status in ["pending", "paused"]:
        task.name = request.form["name"]
        hours = int(request.form.get("hours", 0) or 0)
        minutes = int(request.form.get("minutes", 0) or 0)
        seconds = int(request.form.get("seconds", 0) or 0)
        task.duration = hours * 3600 + minutes * 60 + seconds
        db.session.commit()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
