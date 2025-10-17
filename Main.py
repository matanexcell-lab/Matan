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

# ===== מודל =====
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
            "position": self.position
        }

Base.metadata.create_all(engine)

# אם חסרה עמודת position
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

# ===== עזר =====
def now():
    return datetime.now(TZ)

def hhmmss(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    h, m, s = total_seconds // 3600, (total_seconds % 3600) // 60, total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def any_running(s):
    return s.query(Task).filter(Task.status == "running").first() is not None

def any_active(s):
    return s.query(Task).filter(Task.status.in_(["running", "paused"])).first() is not None

def recompute_chain_in_db():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        now_ts = now()
        for idx, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                rem = int((t.end_time - now_ts).total_seconds())
                if rem <= 0:
                    t.status, t.remaining, t.end_time = "done", 0, None
                    s.add(t)
                    if idx + 1 < len(tasks):
                        nxt = tasks[idx + 1]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = now_ts + timedelta(seconds=int(nxt.remaining))
                            s.add(nxt)
                else:
                    t.remaining = rem
                    s.add(t)

def overall_end_time_calc():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        if not tasks: return None
        base = now()
        for t in tasks:
            if t.status == "running" and t.end_time and t.end_time > base:
                base = t.end_time
        for t in tasks:
            if t.status in ("pending", "paused"):
                base += timedelta(seconds=int(max(0, t.remaining)))
        return base

# ===== Flask =====
app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/state")
def state():
    recompute_chain_in_db()
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        payload = [t.to_dict() for t in tasks]
    end_all = overall_end_time_calc()
    end_all_str = end_all.astimezone(TZ).strftime("%H:%M:%S %d.%m.%Y") if end_all else "-"
    return jsonify({"ok": True, "tasks": payload, "overall_end_time": end_all_str, "now": now().strftime("%H:%M:%S %d.%m.%Y")})

# ===== פעולות =====
@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "משימה חדשה").strip()
    h, m, ssec = int(data.get("hours",0)), int(data.get("minutes",0)), int(data.get("seconds",0))
    duration = h*3600 + m*60 + ssec
    with session_scope() as s:
        pos = s.query(Task).count()
        t = Task(name=name, duration=duration, remaining=duration, status="pending", position=pos)
        s.add(t)
    return jsonify({"ok": True})

@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if not t: return jsonify({"ok": False}), 404
        if t.status in ("pending","paused") and not any_running(s):
            t.status, t.end_time = "running", now()+timedelta(seconds=int(t.remaining))
        elif t.status=="done" and not any_active(s):
            t.remaining, t.status, t.end_time = t.duration,"running",now()+timedelta(seconds=int(t.duration))
        s.add(t)
    return jsonify({"ok": True})

@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t and t.status=="running" and t.end_time:
            t.remaining=max(0,int((t.end_time-now()).total_seconds()))
            t.status,t.end_time="paused",None
            s.add(t)
    return jsonify({"ok": True})

@app.route("/reset/<int:tid>", methods=["POST"])
def reset(tid):
    with session_scope() as s:
        t=s.get(Task,tid)
        if t:
            t.remaining,t.status,t.end_time=t.duration,"running",now()+timedelta(seconds=int(t.duration))
            s.add(t)
    return jsonify({"ok":True})

@app.route("/delete/<int:tid>",methods=["POST"])
def delete(tid):
    with session_scope() as s:
        t=s.get(Task,tid)
        if t:s.delete(t)
    return jsonify({"ok":True})

@app.route("/update/<int:tid>",methods=["POST"])
def update(tid):
    data=request.json or {}
    with session_scope() as s:
        t=s.get(Task,tid)
        if not t:return jsonify({"ok":False}),404
        if "name" in data:t.name=data["name"].strip() or t.name
        if any(k in data for k in ("hours","minutes","seconds","duration")):
            h=int(data.get("hours",0));m=int(data.get("minutes",0));sec=int(data.get("seconds",0))
            dur=h*3600+m*60+sec;t.duration=t.remaining=dur;t.end_time=None
        s.add(t)
    return jsonify({"ok":True})

@app.route("/extend/<int:tid>",methods=["POST"])
def extend(tid):
    data=request.json or {};extra=int(data.get("hours",0))*3600+int(data.get("minutes",0))*60+int(data.get("seconds",0))
    with session_scope() as s:
        t=s.get(Task,tid)
        if not t:return jsonify({"ok":False}),404
        t.duration+=extra;t.remaining+=extra
        if t.status=="running"and t.end_time:t.end_time+=timedelta(seconds=extra)
        s.add(t)
    return jsonify({"ok":True})

# ===== שינוי סדר משימה בודדת =====
@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    data = request.json or {}
    task_id, new_position = data.get("task_id"), int(data.get("new_position", 0))
    if not task_id: return jsonify({"ok": False, "error": "no task_id"}), 400

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        ids = [t.id for t in tasks]
        if task_id not in ids: return jsonify({"ok": False, "error": "not found"}), 404

        old_i, new_i = ids.index(task_id), max(0, min(new_position-1, len(ids)-1))
        ids.insert(new_i, ids.pop(old_i))
        for i, tid in enumerate(ids):
            t = s.get(Task, tid)
            if t: t.position = i; s.add(t)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
