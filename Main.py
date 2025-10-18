# main.py
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
import pytz

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO
from sqlalchemy import Column, DateTime, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

# ===== Settings =====
TZ = pytz.timezone("Asia/Jerusalem")
DEFAULT_SQLITE_URL = "sqlite:///tasks.db"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
Session = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False))
Base = declarative_base()

# Flask + SocketIO
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev")
socketio = SocketIO(app, cors_allowed_origins="*")  # וובסוקט בזמן אמת

@contextmanager
def session_scope():
    s = Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

# ===== Model =====
class Task(Base):
    __tablename__ = "tasks"
    id        = Column(Integer, primary_key=True, autoincrement=True)
    name      = Column(String, nullable=False)
    duration  = Column(Integer, nullable=False)   # seconds
    remaining = Column(Integer, nullable=False)   # seconds
    status    = Column(String, nullable=False)    # pending|running|paused|done
    end_time  = Column(DateTime(timezone=True))   # aware
    position  = Column(Integer, nullable=False, default=0)  # סדר מוצג

    def to_dict(self):
        rem = self.remaining
        if self.status == "running" and self.end_time:
            now_ts = now()
            rem = max(0, int((self.end_time - now_ts).total_seconds()))
        return {
            "id": self.id,
            "name": self.name,
            "duration": int(self.duration),
            "remaining": int(rem),
            "remaining_hhmmss": hhmmss(rem),
            "status": self.status,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "end_time_str": self.end_time.astimezone(TZ).strftime("%H:%M:%S") if self.end_time else "-",
            "position": self.position,
        }

Base.metadata.create_all(engine)

# אם חסר עמודת position — נוסיף (למקרה של DB קיים)
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

# ===== Time helpers =====
def now():
    return datetime.now(TZ)

def hhmmss(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ===== Chain logic =====
def any_running(s):
    return s.query(Task).filter(Task.status == "running").first() is not None

def any_active(s):
    return s.query(Task).filter(Task.status.in_(["running", "paused"])).first() is not None

def recompute_chain_in_db():
    """
    סוגר משימות שרצות שנגמרו, מפעיל את הבאה, ומעדכן remaining.
    מחזיר רשימת משימות ששונו כדי שנוכל לשדר לכולם רק דלתא.
    """
    changed_ids = []
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        now_ts = now()

        for idx, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                rem = int((t.end_time - now_ts).total_seconds())
                if rem <= 0:
                    # סיום משימה
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    changed_ids.append(t.id)

                    # הפעלת הבאה בתור
                    if idx + 1 < len(tasks):
                        nxt = tasks[idx + 1]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = now_ts + timedelta(seconds=int(nxt.remaining))
                            changed_ids.append(nxt.id)
                else:
                    # עדכון remaining בריצה
                    if t.remaining != rem:
                        t.remaining = rem
                        changed_ids.append(t.id)
    return changed_ids

def overall_end_time_calc():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        if not tasks:
            return None
        base = now()
        for t in tasks:
            if t.status == "running" and t.end_time and t.end_time > base:
                base = t.end_time
        for t in tasks:
            if t.status in ("pending", "paused"):
                base = base + timedelta(seconds=int(max(0, t.remaining)))
        return base

def broadcast_overall_and_now():
    end_all = overall_end_time_calc()
    end_all_str = end_all.astimezone(TZ).strftime("%H:%M:%S %d.%m.%Y") if end_all else "-"
    socketio.emit("overall_update", {
        "overall_end_time": end_all_str,
        "now": now().strftime("%H:%M:%S %d.%m.%Y")
    })

def broadcast_task_by_id(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t:
            socketio.emit("task_update", {"task": t.to_dict()})

def broadcast_full_snapshot():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]
    end_all = overall_end_time_calc()
    end_all_str = end_all.astimezone(TZ).strftime("%H:%M:%S %d.%m.%Y") if end_all else "-"
    socketio.emit("snapshot", {
        "tasks": payload,
        "overall_end_time": end_all_str,
        "now": now().strftime("%H:%M:%S %d.%m.%Y")
    })

# ===== Background tick (כל שנייה) =====
@socketio.on("connect")
def on_connect():
    # שולח סנאפשוט מלא לחיבור חדש
    broadcast_full_snapshot()

def tick():
    # קריאה מחזורית: לעדכן ריצות, לשדר שינויים
    changed = recompute_chain_in_db()
    if changed:
        for tid in set(changed):
            broadcast_task_by_id(tid)
        broadcast_overall_and_now()

# מריצים "טיק" כל שנייה
@socketio.on("client_tick")  # קריאה מהדפדפן פעם בשנייה כדי לאפשר טיימר גם ב-free hosting
def handle_client_tick(_msg=None):
    tick()

# ===== Routes =====
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/state")
def state():
    # נקודת גיבוי/דיבוג (לא חובה בשוטף)
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]
    end_all = overall_end_time_calc()
    end_all_str = end_all.astimezone(TZ).strftime("%H:%M:%S %d.%m.%Y") if end_all else "-"
    return jsonify({
        "ok": True,
        "tasks": payload,
        "overall_end_time": end_all_str,
        "now": now().strftime("%H:%M:%S %d.%m.%Y")
    })

@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "").strip() or "משימה"
    h = int(data.get("hours") or 0)
    m = int(data.get("minutes") or 0)
    ssec = int(data.get("seconds") or 0)
    duration = int(data.get("duration") or (h*3600 + m*60 + ssec))
    duration = max(0, duration)
    with session_scope() as s:
        max_pos = s.query(Task).count()
        t = Task(name=name, duration=duration, remaining=duration, status="pending", end_time=None, position=max_pos)
        s.add(t)
        s.flush()
        new_id = t.id
    # שידור מיידי
    broadcast_task_by_id(new_id)
    broadcast_overall_and_now()
    return jsonify({"ok": True, "id": new_id})

@app.route("/start/<int:task_id>", methods=["POST"])
def start(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404
        if t.status in ("pending", "paused") and not any_running(s):
            t.end_time = now() + timedelta(seconds=int(t.remaining))
            t.status = "running"
        elif t.status == "done" and not any_active(s):
            t.remaining = int(t.duration)
            t.end_time = now() + timedelta(seconds=int(t.remaining))
            t.status = "running"
        s.add(t)
        tid = t.id
    broadcast_task_by_id(tid)
    broadcast_overall_and_now()
    return jsonify({"ok": True})

@app.route("/pause/<int:task_id>", methods=["POST"])
def pause(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t and t.status == "running" and t.end_time:
            rem = int((t.end_time - now()).total_seconds())
            t.remaining = max(0, rem)
            t.status = "paused"
            t.end_time = None
            s.add(t)
            tid = t.id
        else:
            tid = None
    if tid:
        broadcast_task_by_id(tid)
        broadcast_overall_and_now()
    return jsonify({"ok": True})

@app.route("/reset/<int:task_id>", methods=["POST"])
def reset(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t:
            t.remaining = int(t.duration)
            t.status = "running"
            t.end_time = now() + timedelta(seconds=int(t.remaining))
            s.add(t)
            tid = t.id
        else:
            tid = None
    if tid:
        broadcast_task_by_id(tid)
        broadcast_overall_and_now()
    return jsonify({"ok": True})

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t:
            s.delete(t)
    # אחרי מחיקה: הכי פשוט לשדר סנאפשוט מלא (כדי לעדכן מיקומים)
    broadcast_full_snapshot()
    return jsonify({"ok": True})

@app.route("/update/<int:task_id>", methods=["POST"])
def update(task_id):
    """עריכת שם/זמן כאשר המשימה לא רצה."""
    data = request.json or {}
    with session_scope() as s:
        t = s.get(Task, task_id)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404
        if t.status not in ("pending", "paused", "done"):
            return jsonify({"ok": False, "error": "cannot edit running task"}), 400

        if "name" in data:
            nm = (data.get("name") or "").strip()
            if nm:
                t.name = nm

        if any(k in data for k in ("hours", "minutes", "seconds", "duration")):
            h = int(data.get("hours") or 0)
            m = int(data.get("minutes") or 0)
            ssec = int(data.get("seconds") or 0)
            duration = int(data.get("duration") or (h*3600 + m*60 + ssec))
            duration = max(0, duration)
            t.duration = duration
            t.remaining = duration
            if t.status == "done":
                t.status = "pending"
            t.end_time = None
        s.add(t)
        tid = t.id
    broadcast_task_by_id(tid)
    broadcast_overall_and_now()
    return jsonify({"ok": True})

@app.route("/extend/<int:task_id>", methods=["POST"])
def extend(task_id):
    """הארכת משימה בזמן חופשי (שעות/דקות/שניות)."""
    data = request.json or {}
    extra = 0
    if any(k in data for k in ("seconds", "minutes", "hours")):
        extra = int(data.get("hours", 0))*3600 + int(data.get("minutes", 0))*60 + int(data.get("seconds", 0))
    else:
        extra = int(data.get("extra_seconds") or 0)
    if extra <= 0:
        return jsonify({"ok": False, "error": "extra must be > 0"}), 400

    with session_scope() as s:
        t = s.get(Task, task_id)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404

        t.duration = int(t.duration) + extra
        if t.status == "running" and t.end_time:
            rem = max(0, int((t.end_time - now()).total_seconds()))
            t.remaining = rem + extra
            t.end_time = t.end_time + timedelta(seconds=extra)
        else:
            t.remaining = int(t.remaining) + extra
        s.add(t)
        tid = t.id
    broadcast_task_by_id(tid)
    broadcast_overall_and_now()
    return jsonify({"ok": True})

@app.route("/skip/<int:task_id>", methods=["POST"])
def skip(task_id):
    """דלג לבאה: מסמן רצה כ-done ומפעיל את הבאה מיידית."""
    next_id = None
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        ids = [t.id for t in tasks]
        t = s.get(Task, task_id)
        if t and t.status == "running":
            t.status = "done"
            t.remaining = 0
            t.end_time = None
            s.add(t)
            if task_id in ids:
                idx = ids.index(task_id)
                if idx + 1 < len(tasks):
                    nxt = tasks[idx+1]
                    if nxt.status == "pending":
                        nxt.status = "running"
                        nxt.end_time = now() + timedelta(seconds=int(nxt.remaining))
                        s.add(nxt)
                        next_id = nxt.id
            this_id = t.id
        else:
            this_id = None
    if this_id:
        broadcast_task_by_id(this_id)
    if next_id:
        broadcast_task_by_id(next_id)
    broadcast_overall_and_now()
    return jsonify({"ok": True})

@app.route("/set_pending/<int:task_id>", methods=["POST"])
def set_pending(task_id):
    """הפיכת משימה ל-pending (כולל DONE חוזר לפנדינג עם remaining=duration)."""
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t and t.status in ("paused", "done", "pending"):
            if t.status == "done":
                t.remaining = int(t.duration)
            t.status = "pending"
            t.end_time = None
            s.add(t)
            tid = t.id
        else:
            tid = None
    if tid:
        broadcast_task_by_id(tid)
        broadcast_overall_and_now()
    return jsonify({"ok": True})

# שינוי מיקום של משימה בודדת (״העבר למיקום״)
@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    data = request.json or {}
    task_id = data.get("task_id")
    new_position = int(data.get("new_position", 0))

    if not task_id:
        return jsonify({"ok": False, "error": "no task_id provided"}), 400

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        ids = [t.id for t in tasks]

        if task_id not in ids:
            return jsonify({"ok": False, "error": "task not found"}), 404

        old_index = ids.index(task_id)
        new_index = max(0, min(new_position - 1, len(ids) - 1))
        ids.insert(new_index, ids.pop(old_index))

        for idx, tid in enumerate(ids):
            t = s.get(Task, tid)
            if t:
                t.position = idx
                s.add(t)

    # שידור סנאפשוט מלא (כי סדר השתנה לכולם)
    broadcast_full_snapshot()
    return jsonify({"ok": True})

if __name__ == "__main__":
    # לפיתוח מקומי:
    # socketio.run(app, host="0.0.0.0", port=5000, debug=True)
    # ברנדר/פרודקשן תריץ עם gunicorn eventlet:
    # web: gunicorn -k eventlet -w 1 main:app
    socketio.run(app, host="0.0.0.0", port=5000)
