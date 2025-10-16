import os
from contextlib import contextmanager
from datetime import datetime, timedelta
import pytz
from flask import Flask, jsonify, render_template, request
from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

# === הגדרות בסיסיות ===
TZ = pytz.timezone("Asia/Jerusalem")
DEFAULT_SQLITE_URL = "sqlite:///tasks.db"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
Session = scoped_session(sessionmaker(bind=engine, expire_on_commit=False))
Base = declarative_base()

@contextmanager
def session_scope():
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# === טבלת משימות ===
class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    duration = Column(Integer, nullable=False)
    remaining = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending | running | paused | done
    start_time = Column(DateTime(timezone=True))
    end_time = Column(DateTime(timezone=True))
    position = Column(Integer, nullable=False, default=0)

    def to_dict(self):
        rem = self.remaining
        if self.status == "running" and self.end_time:
            rem = max(0, int((self.end_time - now()).total_seconds()))
        return {
            "id": self.id,
            "name": self.name,
            "duration": self.duration,
            "remaining": rem,
            "remaining_hhmmss": hhmmss(rem),
            "status": self.status,
            "end_time_str": self.end_time.astimezone(TZ).strftime("%H:%M:%S") if self.end_time else "-",
            "position": self.position,
        }

Base.metadata.create_all(engine)

# === פונקציות עזר ===
def now():
    return datetime.now(TZ)

def hhmmss(sec):
    sec = max(0, int(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# === עדכון רצף משימות ===
def recompute_chain():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        t_now = now()
        for i, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                remaining = int((t.end_time - t_now).total_seconds())
                if remaining <= 0:
                    t.status = "done"
                    t.remaining = 0
                    t.start_time = None
                    t.end_time = None
                    s.add(t)
                    if i + 1 < len(tasks):
                        nxt = tasks[i + 1]
                        if nxt.status == "pending":
                            nxt.start_time = t_now
                            nxt.end_time = t_now + timedelta(seconds=nxt.remaining)
                            nxt.status = "running"
                            s.add(nxt)
                else:
                    t.remaining = remaining
                    s.add(t)

# === חישוב שעת סיום כוללת ===
def calc_overall_end():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        if not tasks:
            return None
        base = now()
        for t in tasks:
            if t.status == "running" and t.end_time:
                base = t.end_time
            elif t.status in ("paused", "pending"):
                base += timedelta(seconds=t.remaining)
        return base

# === Flask ===
app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/state")
def state():
    recompute_chain()
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        end_all = calc_overall_end()
        end_str = end_all.strftime("%H:%M:%S %d.%m.%Y") if end_all else "-"
        return jsonify({
            "tasks": [t.to_dict() for t in tasks],
            "overall_end_time": end_str,
            "now": now().strftime("%H:%M:%S %d.%m.%Y")
        })

@app.route("/add", methods=["POST"])
def add_task():
    data = request.json or {}
    name = (data.get("name") or "משימה חדשה").strip()
    hours = int(data.get("hours") or 0)
    minutes = int(data.get("minutes") or 0)
    seconds = int(data.get("seconds") or 0)
    total = max(1, hours * 3600 + minutes * 60 + seconds)

    with session_scope() as s:
        last_pos = s.query(Task.position).order_by(Task.position.desc()).first()
        position = (last_pos[0] + 1) if last_pos else 1
        t = Task(name=name, duration=total, remaining=total, status="pending", position=position)
        s.add(t)
    return jsonify({"ok": True})

@app.route("/start/<int:task_id>", methods=["POST"])
def start_task(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t and t.status in ("pending", "paused"):
            t.start_time = now()
            t.end_time = t.start_time + timedelta(seconds=t.remaining)
            t.status = "running"
            s.add(t)
    return jsonify({"ok": True})

@app.route("/pause/<int:task_id>", methods=["POST"])
def pause_task(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t and t.status == "running":
            t.remaining = max(0, int((t.end_time - now()).total_seconds()))
            t.status = "paused"
            t.start_time = None
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete_task(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t:
            s.delete(t)
    return jsonify({"ok": True})

@app.route("/update/<int:task_id>", methods=["POST"])
def update_task(task_id):
    data = request.json or {}
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t and t.status != "running":
            if "name" in data:
                t.name = (data["name"] or t.name).strip()
            if any(k in data for k in ("hours", "minutes", "seconds")):
                h = int(data.get("hours", 0))
                m = int(data.get("minutes", 0))
                sec = int(data.get("seconds", 0))
                total = max(1, h * 3600 + m * 60 + sec)
                t.duration = total
                t.remaining = total
                t.start_time = None
                t.end_time = None
            s.add(t)
    return jsonify({"ok": True})

@app.route("/extend/<int:task_id>", methods=["POST"])
def extend_task(task_id):
    data = request.json or {}
    extra = int(data.get("hours", 0)) * 3600 + int(data.get("minutes", 0)) * 60 + int(data.get("seconds", 0))
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t:
            t.duration += extra
            t.remaining += extra
            if t.end_time:
                t.end_time += timedelta(seconds=extra)
            s.add(t)
    return jsonify({"ok": True})

@app.route("/reorder", methods=["POST"])
def reorder():
    order = request.json.get("order", [])
    with session_scope() as s:
        for idx, tid in enumerate(order, start=1):
            t = s.get(Task, tid)
            if t:
                t.position = idx
                s.add(t)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
