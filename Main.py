from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tasks.db"
db = SQLAlchemy(app)


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.Integer, nullable=False)  # בשניות
    remaining = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending / running / paused / finished
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def remaining_time(self):
        if self.status == "running" and self.end_time:
            remaining = (self.end_time - datetime.utcnow()).total_seconds()
            return max(int(remaining), 0)
        return self.remaining


with app.app_context():
    db.create_all()


@app.route("/")
def index():
    edit_id = request.args.get("edit", type=int)
    tasks = Task.query.order_by(Task.created_at.desc()).all()
    return render_template("index.html", tasks=tasks, edit_id=edit_id)


@app.route("/add", methods=["POST"])
def add_task():
    name = request.form["name"]
    h = int(request.form.get("hours", 0))
    m = int(request.form.get("minutes", 0))
    s = int(request.form.get("seconds", 0))
    duration = h * 3600 + m * 60 + s
    if duration <= 0:
        duration = 60
    task = Task(name=name, duration=duration, remaining=duration, status="pending")
    db.session.add(task)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/start/<int:task_id>")
def start_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.status == "pending":
        task.status = "running"
        task.start_time = datetime.utcnow()
        task.end_time = task.start_time + timedelta(seconds=task.remaining)
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/pause/<int:task_id>")
def pause_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.status == "running":
        remaining = task.remaining_time()
        task.remaining = remaining
        task.status = "paused"
        task.start_time = None
        task.end_time = None
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/resume/<int:task_id>")
def resume_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.status == "paused" and task.remaining > 0:
        task.status = "running"
        task.start_time = datetime.utcnow()
        task.end_time = task.start_time + timedelta(seconds=task.remaining)
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/finish/<int:task_id>")
def finish_task(task_id):
    task = Task.query.get_or_404(task_id)
    task.status = "finished"
    task.remaining = 0
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/delete/<int:task_id>")
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/update/<int:task_id>", methods=["POST"])
def update_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.status != "running":  # לא ניתן לערוך בזמן ריצה
        task.name = request.form["name"]
        h = int(request.form.get("hours", 0))
        m = int(request.form.get("minutes", 0))
        s = int(request.form.get("seconds", 0))
        duration = h * 3600 + m * 60 + s
        if duration <= 0:
            duration = 60
        task.duration = duration
        task.remaining = duration
        task.start_time = None
        task.end_time = None
        task.status = "pending"
        db.session.commit()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
