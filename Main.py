from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)

# חיבור למסד נתונים SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tasks.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# מודל טבלה של משימות
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending, running, paused, done
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<Task {self.id} - {self.name}>"

# עמוד ראשי
@app.route("/")
def index():
    tasks = Task.query.order_by(Task.created_at.desc()).all()
    return render_template("index.html", tasks=tasks)

# יצירת משימה חדשה
@app.route("/add", methods=["POST"])
def add():
    task_name = request.form.get("task_name")
    if task_name:
        new_task = Task(name=task_name)
        db.session.add(new_task)
        db.session.commit()
    return redirect(url_for("index"))

# התחלת משימה
@app.route("/start/<int:task_id>")
def start(task_id):
    task = Task.query.get_or_404(task_id)
    task.status = "running"
    task.start_time = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("index"))

# השהיית משימה
@app.route("/pause/<int:task_id>")
def pause(task_id):
    task = Task.query.get_or_404(task_id)
    task.status = "paused"
    db.session.commit()
    return redirect(url_for("index"))

# סיום משימה
@app.route("/done/<int:task_id>")
def done(task_id):
    task = Task.query.get_or_404(task_id)
    task.status = "done"
    task.end_time = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("index"))

# מחיקת משימה
@app.route("/delete/<int:task_id>")
def delete(task_id):
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for("index"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000)
