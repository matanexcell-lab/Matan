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

# === טבלת המשימות ===
class Task(Base):
    __tablename__ = "tasks"
    id        = Column(Integer, primary_key=True, autoincrement=True)
    name      = Column(String, nullable=False)
    duration  = Column(Integer, nullable=False)
    remaining = Column(Integer, nullable=False)
    status    = Column(String, nullable=False)  # pending | running | paused | done
    end_time  = Column(DateTime(timezone=True))
    position  = Column(Integer, nullable=False, default=0)

    def to_dict(self):
        rem = self.remaining
        if self.status == "running" and self.end_time:
            now_ts = now()
            rem = max(0, int((self.end_time - now_ts).total_seconds()))
        return {
            "id": self.id,
            "name": self.name,
            "duration": self.duration,
            "remaining": rem,
            "remaining_hhmmss": hhmmss(rem),
            "status": self.status,
            "end_time_str": self.end_time.astimezone(TZ).strftime("%H:%M:%S") if self.end_time else "-",
            "position": self.position
        }

Base.metadata.create_all(engine)

# === עזרי זמן ===
def now():
    return datetime.now(TZ)

def hhmmss(sec):
    sec = max(0, int(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# === לוגיקה ===
def recompute_chain():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        t_now = now()
        for i, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                rem = int((t.end_time - t_now).total_seconds())
                if rem <= 0:
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)
                    if i + 1 < len(tasks):
                        nxt = tasks[i + 1]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = t_now + timedelta(seconds=nxt.remaining)
                            s.add(nxt)
                else:
                    t.remaining = rem
                    s.add(t)

def calc_overall_end():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        if not tasks:
            return None
        t_now = now()
        base = t_now
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
        overall = calc_overall_end()
        return jsonify({
            "tasks": [t.to_dict() for t in tasks],
            "overall_end_time": overall.astimezone(TZ).strftime("%H:%M:%S %d.%m.%Y") if overall else "-",
            "now": now().strftime("%H:%M:%S %d.%m.%Y")
        })

@app.route("/add", methods=["POST"])
def add():
    d = request.json
    name = d.get("name", "משימה חדשה")
    duration = int(d.get("hours", 0))*3600 + int(d.get("minutes", 0))*60 + int(d.get("seconds", 0))
    duration = max(duration, 1)
    with session_scope() as s:
        max_pos = s.query(Task.position).order_by(Task.position.desc()).first()
        new_pos = (max_pos[0] + 1) if max_pos else 1
        t = Task(name=name, duration=duration, remaining=duration, status="pending", position=new_pos)
        s.add(t)
    return jsonify({"ok": True})

@app.route("/reorder", methods=["POST"])
def reorder():
    data = request.json
    order = data.get("order", [])
    with session_scope() as s:
        for idx, tid in enumerate(order, start=1):
            t = s.get(Task, tid)
            if t:
                t.position = idx
                s.add(t)
    return jsonify({"ok": True})

@app.route("/start/<int:id>", methods=["POST"])
def start(id):
    with session_scope() as s:
        t = s.get(Task, id)
        if t and t.status in ("pending", "paused"):
            t.status = "running"
            t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)
    return jsonify({"ok": True})

@app.route("/pause/<int:id>", methods=["POST"])
def pause(id):
    with session_scope() as s:
        t = s.get(Task, id)
        if t and t.status == "running":
            t.remaining = max(0, int((t.end_time - now()).total_seconds()))
            t.status = "paused"
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})

@app.route("/delete/<int:id>", methods=["POST"])
def delete(id):
    with session_scope() as s:
        t = s.get(Task, id)
        if t: s.delete(t)
    return jsonify({"ok": True})

@app.route("/update/<int:id>", methods=["POST"])
def update(id):
    d = request.json
    with session_scope() as s:
        t = s.get(Task, id)
        if not t: return jsonify({"ok": False, "error": "not found"}), 404
        t.name = d.get("name", t.name)
        h, m, sec = int(d.get("hours", 0)), int(d.get("minutes", 0)), int(d.get("seconds", 0))
        dur = h*3600 + m*60 + sec
        if dur > 0:
            t.duration = dur
            t.remaining = dur
        s.add(t)
    return jsonify({"ok": True})

@app.route("/extend/<int:id>", methods=["POST"])
def extend(id):
    d = request.json
    extra = int(d.get("hours", 0))*3600 + int(d.get("minutes", 0))*60 + int(d.get("seconds", 0))
    with session_scope() as s:
        t = s.get(Task, id)
        if t:
            t.duration += extra
            t.remaining += extra
            if t.end_time:
                t.end_time += timedelta(seconds=extra)
            s.add(t)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
