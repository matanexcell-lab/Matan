import os
from contextlib import contextmanager
from datetime import datetime, timedelta
import pytz
from flask import Flask, jsonify, render_template, request
from sqlalchemy import Column, DateTime, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

# ===== הגדרות כלליות =====
TZ = pytz.timezone("Asia/Jerusalem")
DEFAULT_SQLITE_URL = "sqlite:///tasks.db"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)
Session = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False))
Base = declarative_base()

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

# ===== מודל המשימה =====
class Task(Base):
    __tablename__ = "tasks"
    id        = Column(Integer, primary_key=True, autoincrement=True)
    name      = Column(String, nullable=False)
    duration  = Column(Integer, nullable=False)
    remaining = Column(Integer, nullable=False)
    status    = Column(String, nullable=False)
    end_time  = Column(DateTime(timezone=True))
    position  = Column(Integer, nullable=False, default=0)  # סדר המשימה

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
        }

Base.metadata.create_all(engine)

# אם חסרה עמודת position — נוסיף אותה
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass  # כבר קיימת


# ===== פונקציות עזר =====
def now():
    return datetime.now(TZ)

def hhmmss(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def any_running(s):
    return s.query(Task).filter(Task.status == "running").first() is not None

def any_active(s):
    return s.query(Task).filter(Task.status.in_(["running", "paused"])).first() is not None

def recompute_chain_in_db():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        now_ts = now()
        for idx, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                rem = int((t.end_time - now_ts).total_seconds())
                if rem <= 0:
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)
                    if idx + 1 < len(tasks):
                        nxt = tasks[idx + 1]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = now_ts + timedelta(seconds=int(nxt.remaining))
                            s.add(nxt)
                else:
                    if t.remaining != rem:
                        t.remaining = rem
                        s.add(t)

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


# ===== אפליקציית Flask =====
app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/state")
def state():
    recompute_chain_in_db()
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


# ===== ראוטים לניהול משימות =====

@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "").strip() or "משימה חדשה"
    h = int(data.get("hours") or 0)
    m = int(data.get("minutes") or 0)
    ssec = int(data.get("seconds") or 0)
    duration = int(data.get("duration") or (h*3600 + m*60 + ssec))
    with session_scope() as s:
        max_pos = s.query(Task).count()
        t = Task(name=name, duration=duration, remaining=duration, status="pending", end_time=None, position=max_pos)
        s.add(t)
    return jsonify({"ok": True})

@app.route("/start/<int:task_id>", methods=["POST"])
def start(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t and t.status in ("pending", "paused") and not any_running(s):
            t.end_time = now() + timedelta(seconds=int(t.remaining))
            t.status = "running"
            s.add(t)
        elif t and t.status == "done" and not any_active(s):
            t.remaining = int(t.duration)
            t.end_time = now() + timedelta(seconds=int(t.remaining))
            t.status = "running"
            s.add(t)
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
    return jsonify({"ok": True})

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t: s.delete(t)
    return jsonify({"ok": True})

@app.route("/update/<int:task_id>", methods=["POST"])
def update(task_id):
    data = request.json or {}
    with session_scope() as s:
        t = s.get(Task, task_id)
        if not t: return jsonify({"ok": False, "error": "not found"}), 404
        if "name" in data:
            nm = (data.get("name") or "").strip()
            if nm: t.name = nm
        if any(k in data for k in ("hours","minutes","seconds","duration")):
            h = int(data.get("hours") or 0)
            m = int(data.get("minutes") or 0)
            ssec = int(data.get("seconds") or 0)
            duration = int(data.get("duration") or (h*3600 + m*60 + ssec))
            t.duration = duration
            t.remaining = duration
            t.end_time = None
        s.add(t)
    return jsonify({"ok": True})

@app.route("/extend/<int:task_id>", methods=["POST"])
def extend(task_id):
    data = request.json or {}
    extra = int(data.get("hours",0))*3600 + int(data.get("minutes",0))*60 + int(data.get("seconds",0))
    with session_scope() as s:
        t = s.get(Task, task_id)
        if not t: return jsonify({"ok": False}), 404
        t.duration += extra
        t.remaining += extra
        if t.status == "running" and t.end_time:
            t.end_time = t.end_time + timedelta(seconds=extra)
        s.add(t)
    return jsonify({"ok": True})

@app.route("/reorder", methods=["POST"])
def reorder():
    """עדכון סדר המשימות לפי רשימת מזהים"""
    data = request.json or {}
    order = data.get("order", [])
    if not order:
        return jsonify({"ok": False, "error": "no order provided"}), 400
    with session_scope() as s:
        for idx, task_id in enumerate(order):
            t = s.get(Task, task_id)
            if t:
                t.position = idx
                s.add(t)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
