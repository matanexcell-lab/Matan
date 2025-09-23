from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import case
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tasks.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

IL_TZ = ZoneInfo("Asia/Jerusalem")

def now_il() -> datetime:
    return datetime.now(IL_TZ)

# סטטוסים: pending / active / paused / done
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    duration = db.Column(db.Integer, nullable=False)            # משך התחלתי (שניות)
    remaining_seconds = db.Column(db.Integer, nullable=False)    # בזמן pause/לפני התחלה
    status = db.Column(db.String(16), default="pending")
    start_time = db.Column(db.DateTime, nullable=True)           # כשהמשימה פעילה
    end_time = db.Column(db.DateTime, nullable=True)             # כשהמשימה פעילה

with app.app_context():
    db.create_all()

# ---------- פונקציות עזר ----------

def secs(n): return max(int(n), 0)

def complete_and_chain():
    """
    מריץ "קידום שרשרת": אם יש משימה פעילה שסיימה – מסמן done
    ומיד מפעיל את הבאה בתור (אם קיימת). נקרא בכל בקשה.
    מטפל גם במקרה שהאתר היה סגור זמן רב (לולאה עד שאין מה לקדם).
    """
    while True:
        active = Task.query.filter_by(status="active").order_by(Task.id).first()
        if not active:
            break
        if active.end_time and now_il() >= active.end_time:
            # סיימה
            active.status = "done"
            active.start_time = None
            active.end_time = None
            active.remaining_seconds = 0
            db.session.commit()
            # מפעילים את הבאה (אם יש)
            nxt = Task.query.filter(Task.status.in_(["pending"])).order_by(Task.id).first()
            if nxt:
                nxt.status = "active"
                nxt.start_time = now_il()
                nxt.end_time = nxt.start_time + timedelta(seconds=nxt.remaining_seconds)
                db.session.commit()
                # ממשיכים לולאה (ייתכן שגם היא כבר הסתיימה אם עבר הרבה זמן)
                continue
        break

def compute_schedule():
    """
    מחשב לכל משימה: זמן נותר ושעת סיום צפויה (predict),
    וגם "זמן סיום לכלל המשימות".
    """
    tasks = Task.query.order_by(Task.id).all()
    now = now_il()

    # אם יש פעילה – המצב זורם ממנה; אחרת מתחילים מנקודת הזמן עכשיו
    pointer = now
    active = Task.query.filter_by(status="active").order_by(Task.id).first()
    if active and active.end_time:
        pointer = max(active.end_time, now)

    result = []
    for t in tasks:
        if t.status == "active" and t.end_time:
            rem = secs((t.end_time - now).total_seconds())
            pred = t.end_time
            pointer = pred
        elif t.status == "paused":
            rem = secs(t.remaining_seconds)
            pred = pointer + timedelta(seconds=rem)
            pointer = pred
        elif t.status == "pending":
            rem = secs(t.duration)
            pred = pointer + timedelta(seconds=rem)
            pointer = pred
        else:  # done
            rem = 0
            pred = None

        result.append({
            "id": t.id,
            "name": t.name,
            "status": t.status,
            "duration": t.duration,
            "remaining": rem,
            "start_time": t.start_time.isoformat() if t.start_time else None,
            "end_time": t.end_time.isoformat() if t.end_time else None,
            "predicted_end": pred.isoformat() if pred else None
        })

    all_end = result and result[-1]["predicted_end"] or None
    return {"now": now.isoformat(), "tasks": result, "all_end": all_end}

# ---------- ראוטים ----------

@app.route("/")
def index():
    # רינדור ראשוני – הממשק נטען, הנתונים מגיעים ב-/state
    return render_template("index.html")

@app.route("/state")
def state():
    complete_and_chain()
    return jsonify(compute_schedule())

@app.route("/add", methods=["POST"])
def add_task():
    complete_and_chain()
    name = (request.form.get("name") or "").strip() or "משימה"
    h = int(request.form.get("hours") or 0)
    m = int(request.form.get("minutes") or 0)
    s = int(request.form.get("seconds") or 0)
    duration = h * 3600 + m * 60 + s
    if duration <= 0:
        return jsonify({"error": "משך חייב להיות גדול מ-0"}), 400

    t = Task(name=name, duration=duration, remaining_seconds=duration, status="pending")
    db.session.add(t)
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/start/<int:task_id>", methods=["POST"])
def start_task(task_id):
    complete_and_chain()
    active = Task.query.filter_by(status="active").first()
    if active:
        return jsonify({"error": "כבר יש משימה פעילה"}), 400

    # מותר להתחיל רק את הראשונה בתור שהיא pending או paused
    first_waiting = Task.query.filter(Task.status.in_(["paused", "pending"]))\
                              .order_by(Task.id).first()
    if not first_waiting or first_waiting.id != task_id:
        return jsonify({"error": "ניתן להתחיל רק את המשימה הראשונה בתור"}), 400

    t = first_waiting
    t.status = "active"
    t.start_time = now_il()
    # אם היתה בהשהיה – משתמשים בזמן שנותר, אחרת בזמן התחלתי
    rem = t.remaining_seconds if t.remaining_seconds > 0 else t.duration
    t.end_time = t.start_time + timedelta(seconds=rem)
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/pause/<int:task_id>", methods=["POST"])
def pause_task(task_id):
    complete_and_chain()
    t = Task.query.get(task_id)
    if not t or t.status != "active" or not t.end_time:
        return jsonify({"error": "אין משימה פעילה עם מזהה זה"}), 400
    t.remaining_seconds = secs((t.end_time - now_il()).total_seconds())
    t.status = "paused"
    t.start_time = None
    t.end_time = None
    db.session.commit()
    return jsonify({"ok": True, "remaining": t.remaining_seconds})

@app.route("/reset/<int:task_id>", methods=["POST"])
def reset_task(task_id):
    complete_and_chain()
    t = Task.query.get(task_id)
    if not t:
        return jsonify({"error": "Task not found"}), 404
    if t.status == "active":
        return jsonify({"error": "עצור/השהה לפני איפוס"}), 400
    t.status = "pending"
    t.start_time = None
    t.end_time = None
    t.remaining_seconds = t.duration
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/edit/<int:task_id>", methods=["POST"])
def edit_task(task_id):
    complete_and_chain()
    t = Task.query.get(task_id)
    if not t:
        return jsonify({"error": "Task not found"}), 404
    if t.status == "active":
        return jsonify({"error": "אי אפשר לערוך משימה פעילה"}), 400

    data = request.json or {}
    if "name" in data:
        t.name = (data["name"] or "").strip() or t.name
    if {"hours","minutes","seconds"} <= set(data.keys()):
        h = int(data.get("hours") or 0)
        m = int(data.get("minutes") or 0)
        s = int(data.get("seconds") or 0)
        dur = h*3600 + m*60 + s
        if dur > 0:
            t.duration = dur
            t.remaining_seconds = dur
            t.start_time = None
            t.end_time = None
            if t.status == "done":
                t.status = "pending"

    db.session.commit()
    return jsonify({"ok": True})

@app.route("/delete/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    complete_and_chain()
    t = Task.query.get(task_id)
    if not t:
        return jsonify({"error": "Task not found"}), 404
    if t.status == "active":
        return jsonify({"error": "עצור/השהה לפני מחיקה"}), 400
    db.session.delete(t)
    db.session.commit()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True)
