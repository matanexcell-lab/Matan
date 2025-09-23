from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)

# הגדרות מסד נתונים (SQLite בקובץ tasks.db)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tasks.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# מודל של משימה
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(50), default="ממתינה")  # ממתינה / פעילה / מושהית / הסתיימה
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)

# יצירת טבלאות במסד הנתונים
with app.app_context():
    db.create_all()

# דף הבית – רשימת משימות
@app.route("/")
def index():
    tasks = Task.query.order_by(Task.created_at.desc()).all()
    return render_template("index.html", tasks=tasks)

# הוספת משימה חדשה
@app.route("/add", methods=["POST"])
def add_task():
    name = request.form.get("name")
    if name:
        task = Task(name=name)
        db.session.add(task)
        db.session.commit()
    return redirect(url_for("index"))

# התחלת משימה
@app.route("/start/<int:task_id>")
def start_task(task_id):
    task = Task.query.get_or_404(task_id)
    task.status = "פעילה"
    task.start_time = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("index"))

# השהיית משימה
@app.route("/pause/<int:task_id>")
def pause_task(task_id):
    task = Task.query.get_or_404(task_id)
    task.status = "מושהית"
    db.session.commit()
    return redirect(url_for("index"))

# סיום משימה
@app.route("/finish/<int:task_id>")
def finish_task(task_id):
    task = Task.query.get_or_404(task_id)
    task.status = "הסתיימה"
    task.end_time = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("index"))

# עריכת שם משימה
@app.route("/edit/<int:task_id>", methods=["POST"])
def edit_task(task_id):
    task = Task.query.get_or_404(task_id)
    new_name = request.form.get("name")
    if new_name:
        task.name = new_name
        db.session.commit()
    return redirect(url_for("index"))

# מחיקת משימה
@app.route("/delete/<int:task_id>")
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
