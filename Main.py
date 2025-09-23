from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request, send_from_directory, abort
from flask_sqlalchemy import SQLAlchemy
import os

app = Flask(__name__, static_folder="static", template_folder="templates")

# --- DB setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "tasks.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

IL_TZ = ZoneInfo("Asia/Jerusalem")

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, default="משימה")
    duration = db.Column(db.Integer, nullable=False, default=0)   # seconds
    remaining = db.Column(db.Integer, nullable=False, default=0)  # seconds
    status = db.Column(db.String(20), nullable=False, default="waiting")  # waiting|running|paused|done
    order_index = db.Column(db.Integer, nullable=False, default=0)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)     # UTC
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)    # UTC
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: now_utc())

    def as_dict_runtime(self, ref_utc: datetime):
        """Snapshot including זמן נותר מחושב עכשיו."""
        rem = self.remaining
        if self.status == "running" and self.started_at:
            elapsed = int((ref_utc - self.started_at).total_seconds())
            rem = max(self.remaining - elapsed, 0)

        # סיום צפוי לכל משימה, אם ממשיכים מידית לפי הסדר
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "order_index": self.order_index,
            "duration": self.duration,
            "remaining_now": rem,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at_local": fmt_local(self.finished_at) if self.finished_at else None,
        }

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def fmt_local(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.astimezone(IL_TZ).strftime("%H:%M:%S  %d.%m.%Y")

with app.app_context():
    db.create_all()

# ---------- Helpers ----------

def _safe_int(v, default=0):
    try:
        if v is None or v == "":
            return default
        return int(v)
    except Exception:
        return default

def _top_runnable_id():
    """ה-id של המשימה הראשונה מלמעלה שאינה 'done'."""
    t = Task.query.filter(Task.status != "done").order_by(Task.order_index.asc()).first()
    return t.id if t else None

def _any_running():
    return Task.query.filter_by(status="running").first() is not None

def _start_task(task: Task):
    task.status = "running"
    task.started_at = now_utc()
    db.session.commit()

def _finish_task(task: Task):
    """סיים משימה: סטטוס נשאר 'done', ה-remaining מתאפס חזרה למשך המקורי (כבקשתך)."""
    task.status = "done"
    task.finished_at = now_utc()
    # מחזיר את השעון לערך המקורי, אך לא מתחיל אוטומטית מחדש
    task.remaining = task.duration
    task.started_at = None
    db.session.commit()

def _normalize_chain():
    """
    דואג לרצף אוטומטי:
    - אם יש running שסיים בזמן – מסיים אותו.
    - אם אין running – מתחיל אוטומטית את המשימה הראשונה במצב 'waiting'.
      (אם המשימה הראשונה 'paused', לא נתחיל אותה אוטומטית; תידרש לחיצה ידנית).
    """
    utc = now_utc()
    running = Task.query.filter_by(status="running").order_by(Task.order_index.asc()).first()
    if running:
        elapsed = int((utc - running.started_at).total_seconds()) if running.started_at else 0
        if elapsed >= running.remaining:
            _finish_task(running)

    if not _any_running():
        first_waiting = Task.query.filter_by(status="waiting").order_by(Task.order_index.asc()).first()
        if first_waiting:
            _start_task(first_waiting)

def _recalc_predicted_finish_for_chain(ref_utc: datetime):
    """
    מחשב לכל משימה שעת סיום צפויה (כאילו ממשיכים מכאן ברצף לפי הסדר).
    מחזיר dict {task_id: local_end_str} וגם את 'total_finish_local'.
    """
    tasks = Task.query.order_by(Task.order_index.asc()).all()
    timeline = ref_utc
    end_map = {}
    for t in tasks:
        # זמן שנותר "כאילו עכשיו"
        if t.status == "running" and t.started_at:
            rem = max(t.remaining - int((ref_utc - t.started_at).total_seconds()), 0)
        elif t.status == "done":
            # אם יש זמן סיום אמיתי – נציג אותו; לא מקדמים ציר זמן.
            end_map[t.id] = fmt_local(t.finished_at) if t.finished_at else None
            continue
        else:
            rem = t.remaining

        finish = timeline + timedelta(seconds=rem)
        end_map[t.id] = fmt_local(finish)
        timeline = finish

    total_finish_local = fmt_local(timeline) if tasks else None
    return end_map, total_finish_local

# ---------- Routes ----------

@app.route("/")
def root():
    return send_from_directory(app.template_folder, "index.html")

@app.route("/state", methods=["GET"])
def state():
    # מרענן רצף אוטומטי אם צריך
    _normalize_chain()

    utc = now_utc()
    tasks = Task.query.order_by(Task.order_index.asc()).all()
    data = [t.as_dict_runtime(utc) for t in tasks]

    # הוספת שעת סיום צפויה לכל משימה ולכולן יחד
    end_map, total_finish_local = _recalc_predicted_finish_for_chain(utc)
    for d in data:
        d["predicted_finish_local"] = end_map.get(d["id"])

    return jsonify({
        "now_utc": utc.isoformat(),
        "tasks": data,
        "total_finish_local": total_finish_local
    })

@app.route("/tasks", methods=["POST"])
def add_task():
    """
    תומך גם ב-JSON וגם ב-form.
    מצפה: name, hours, minutes, seconds.
    """
    payload = request.get_json(silent=True) or request.form

    name = (payload.get("name") or "משימה").strip()
    h = _safe_int(payload.get("hours"), 0)
    m = _safe_int(payload.get("minutes"), 0)
    s = _safe_int(payload.get("seconds"), 0)

    duration = max(h * 3600 + m * 60 + s, 0)
    # לא זורקים שגיאה אם 0; פשוט נאפשר משימה 0 שניות (תסתיים מיידית)
    next_order = (db.session.query(db.func.max(Task.order_index)).scalar() or 0) + 1

    t = Task(
        name=name or "משימה",
        duration=duration,
        remaining=duration,
        status="waiting",
        order_index=next_order
    )
    db.session.add(t)
    db.session.commit()
    return jsonify({"ok": True, "task_id": t.id}), 201

@app.route("/tasks/<int:tid>", methods=["PUT"])
def edit_task(tid):
    t = Task.query.get_or_404(tid)

    if t.status == "running":
        return abort(400, description="אי אפשר לערוך משימה בזמן ריצה")

    payload = request.get_json(silent=True) or request.form
    if "name" in payload:
        nm = (payload.get("name") or "").strip()
        if nm:
            t.name = nm

    # שעות/דקות/שניות – אם נמסרו, מעדכנים זמן ומאפסים remaining בהתאם
    supplied_any_time = any(k in payload for k in ("hours", "minutes", "seconds"))
    if supplied_any_time:
        h = _safe_int(payload.get("hours"), 0)
        m = _safe_int(payload.get("minutes"), 0)
        s = _safe_int(payload.get("seconds"), 0)
        duration = max(h * 3600 + m * 60 + s, 0)
        t.duration = duration
        t.remaining = duration
        # לא משנים סטטוס 'done' אם היה Done – נשאר Done (לפי בקשתך)
        t.started_at = None

    db.session.commit()
    return jsonify({"ok": True})

@app.route("/tasks/<int:tid>", methods=["DELETE"])
def delete_task(tid):
    t = Task.query.get_or_404(tid)
    db.session.delete(t)
    db.session.commit()
    # סידור אינדקסים מחדש
    tasks = Task.query.order_by(Task.order_index.asc()).all()
    for i, tk in enumerate(tasks, start=1):
        tk.order_index = i
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/tasks/<int:tid>/start", methods=["POST"])
def start(tid):
    t = Task.query.get_or_404(tid)
    top_id = _top_runnable_id()
    if _any_running() and t.status != "paused":
        return abort(400, description="כבר רצה משימה אחרת")
    if t.id != top_id and t.status != "paused":
        return abort(400, description="אפשר להתחיל רק את המשימה העליונה או משימה שמושהתה")

    # אם הייתה paused – לא משנים remaining; אם waiting – remaining=duration כבר מוגדר
    t.status = "running"
    t.started_at = now_utc()
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/tasks/<int:tid>/pause", methods=["POST"])
def pause(tid):
    t = Task.query.get_or_404(tid)
    if t.status != "running":
        return abort(400, description="המשימה אינה רצה")
    utc = now_utc()
    elapsed = int((utc - t.started_at).total_seconds()) if t.started_at else 0
    t.remaining = max(t.remaining - elapsed, 0)
    t.status = "paused"
    t.started_at = None
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/tasks/<int:tid>/reset", methods=["POST"])
def reset(tid):
    t = Task.query.get_or_404(tid)
    # מאפס לזמן המקורי ומחזיר למצב waiting (לא מתחיל עד לחיצה על 'התחל')
    t.remaining = t.duration
    t.started_at = None
    t.finished_at = None if t.status != "done" else t.finished_at
    t.status = "waiting" if t.status != "done" else "done"
    db.session.commit()
    return jsonify({"ok": True})

# קבצי סטטיק/טמפלט
@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory(app.static_folder, path)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
