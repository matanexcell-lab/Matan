from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tasks.db'
db = SQLAlchemy(app)

# מודל של משימה
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    hours = db.Column(db.Integer, default=0)
    minutes = db.Column(db.Integer, default=0)
    seconds = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="pending")  # pending / running / paused / finished
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# פונקציה למציאת המשימה הבאה
def get_next_task(current_id):
    return Task.query.filter(Task.id > current_id, Task.status == "pending").order_by(Task.id.asc()).first()

# דף ראשי
@app.route('/')
def index():
    tasks = Task.query.order_by(Task.id.asc()).all()
    total_end_time = None

    if tasks:
        current_time = datetime.now(ZoneInfo("Asia/Jerusalem"))
        total_duration = timedelta()

        for task in tasks:
            if task.status in ["pending", "paused"]:
                total_duration += timedelta(hours=task.hours, minutes=task.minutes, seconds=task.seconds)
            elif task.status == "running" and task.end_time:
                total_duration += (task.end_time - current_time)

        total_end_time = (current_time + total_duration).strftime("%H:%M:%S")

    return render_template("index.html", tasks=tasks, total_end_time=total_end_time)

# הוספת משימה
@app.route('/add', methods=['POST'])
def add_task():
    name = request.form['name']
    hours = int(request.form.get('hours', 0) or 0)
    minutes = int(request.form.get('minutes', 0) or 0)
    seconds = int(request.form.get('seconds', 0) or 0)

    new_task = Task(name=name, hours=hours, minutes=minutes, seconds=seconds)
    db.session.add(new_task)
    db.session.commit()
    return redirect(url_for('index'))

# התחלת משימה
@app.route('/start/<int:task_id>')
def start_task(task_id):
    task = Task.query.get_or_404(task_id)

    # בדיקה שאין משימה אחרת פעילה
    running_task = Task.query.filter_by(status="running").first()
    if running_task and running_task.id != task_id:
        return redirect(url_for('index'))

    if task.status in ["pending", "paused"]:
        task.status = "running"
        now = datetime.now(ZoneInfo("Asia/Jerusalem"))
        task.start_time = now
        task.end_time = now + timedelta(hours=task.hours, minutes=task.minutes, seconds=task.seconds)
        db.session.commit()
    return redirect(url_for('index'))

# השהיית משימה
@app.route('/pause/<int:task_id>')
def pause_task(task_id):
    task = Task.query.get_or_404(task_id)
    if task.status == "running":
        task.status = "paused"
        now = datetime.now(ZoneInfo("Asia/Jerusalem"))
        if task.end_time:
            remaining = task.end_time - now
            if remaining.total_seconds() > 0:
                task.hours = remaining.seconds // 3600
                task.minutes = (remaining.seconds % 3600) // 60
                task.seconds = remaining.seconds % 60
        db.session.commit()
    return redirect(url_for('index'))

# סיום משימה
@app.route('/finish/<int:task_id>')
def finish_task(task_id):
    task = Task.query.get_or_404(task_id)
    task.status = "finished"
    db.session.commit()

    # התחלת המשימה הבאה ברצף
    next_task = get_next_task(task_id)
    if next_task:
        next_task.status = "running"
        now = datetime.now(ZoneInfo("Asia/Jerusalem"))
        next_task.start_time = now
        next_task.end_time = now + timedelta(hours=next_task.hours, minutes=next_task.minutes, seconds=next_task.seconds)
        db.session.commit()

    return redirect(url_for('index'))

# מחיקת משימה
@app.route('/delete/<int:task_id>')
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for('index'))

# עריכת משימה
@app.route('/edit/<int:task_id>', methods=['POST'])
def edit_task(task_id):
    task = Task.query.get_or_404(task_id)
    task.name = request.form['name']
    task.hours = int(request.form.get('hours', 0) or 0)
    task.minutes = int(request.form.get('minutes', 0) or 0)
    task.seconds = int(request.form.get('seconds', 0) or 0)
    db.session.commit()
    return redirect(url_for('index'))

# API – זמן נותר (בשביל JS בזמן אמת)
@app.route('/remaining/<int:task_id>')
def remaining_time(task_id):
    task = Task.query.get_or_404(task_id)
    now = datetime.now(ZoneInfo("Asia/Jerusalem"))

    if task.status == "running" and task.end_time:
        remaining = task.end_time - now
        if remaining.total_seconds() <= 0:
            task.status = "finished"
            db.session.commit()

            # להתחיל את המשימה הבאה
            next_task = get_next_task(task.id)
            if next_task:
                next_task.status = "running"
                next_task.start_time = now
                next_task.end_time = now + timedelta(hours=next_task.hours, minutes=next_task.minutes, seconds=next_task.seconds)
                db.session.commit()

            return jsonify({"time": "00:00:00", "status": "finished"})
        return jsonify({"time": str(remaining).split(".")[0], "status": task.status})

    return jsonify({"time": f"{task.hours:02d}:{task.minutes:02d}:{task.seconds:02d}", "status": task.status})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
