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
    position  = Column(Integer, nullable=False, default=0)

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

# מוסיף עמודת position אם חסרה
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

# ===== פונקציות עזר =====
def now():
    return datetime.now(TZ)

def hhmmss(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ===== אפליקציית Flask =====
app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/state")
def state():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]
    return jsonify({"ok": True, "tasks": payload})

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

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t: s.delete(t)
    return jsonify({"ok": True})

@app.route("/reorder", methods=["POST"])
def reorder():
    """מעביר משימה בודדת למיקום חדש ומחשב מחדש את הסדר"""
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

    return jsonify({"ok": True})
    
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
