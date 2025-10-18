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

# הוספת עמודת position אם חסרה
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

# ===== Flask + SocketIO =====
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# ===== Helpers =====
def now(): return datetime.now(TZ)
def hhmmss(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def any_running(s): return s.query(Task).filter(Task.status == "running").first() is not None
def any_active(s): return s.query(Task).filter(Task.status.in_(["running","paused"])).first() is not None

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
        if not tasks: return None
        base = now()
        for t in tasks:
            if t.status == "running" and t.end_time and t.end_time > base:
                base = t.end_time
        for t in tasks:
            if t.status in ("pending","paused"):
                base = base + timedelta(seconds=int(max(0, t.remaining)))
        return base

# ===== Broadcast helper =====
def broadcast_update():
    recompute_chain_in_db()
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]
    end_all = overall_end_time_calc()
    end_all_str = end_all.astimezone(TZ).strftime("%H:%M:%S %d.%m.%Y") if end_all else "-"
    socketio.emit("update", {
        "tasks": payload,
        "overall_end_time": end_all_str,
        "now": now().strftime("%H:%M:%S %d.%m.%Y")
    })

# ===== Routes =====
@app.route("/")
def index(): return render_template("index.html")

@app.route("/add", methods=["POST"])
def add():
    d = request.json or {}
    name = (d.get("name") or "משימה חדשה").strip()
    h, m, s = int(d.get("hours",0)), int(d.get("minutes",0)), int(d.get("seconds",0))
    duration = h*3600 + m*60 + s
    with session_scope() as sdb:
        pos = sdb.query(Task).count()
        sdb.add(Task(name=name, duration=duration, remaining=duration, status="pending", position=pos))
    broadcast_update();  return jsonify(ok=True)

@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t and t.status in ("pending","paused") and not any_running(s):
            t.end_time = now() + timedelta(seconds=t.remaining)
            t.status = "running"; s.add(t)
        elif t and t.status=="done" and not any_active(s):
            t.remaining=t.duration; t.end_time=now()+timedelta(seconds=t.remaining)
            t.status="running"; s.add(t)
    broadcast_update();  return jsonify(ok=True)

@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    with session_scope() as s:
        t=s.get(Task,tid)
        if t and t.status=="running" and t.end_time:
            rem=int((t.end_time-now()).total_seconds())
            t.remaining=max(0,rem); t.status="paused"; t.end_time=None; s.add(t)
    broadcast_update();  return jsonify(ok=True)

@app.route("/reset/<int:tid>", methods=["POST"])
def reset(tid):
    with session_scope() as s:
        t=s.get(Task,tid)
        if t: t.remaining=t.duration; t.status="running"; t.end_time=now()+timedelta(seconds=t.duration); s.add(t)
    broadcast_update();  return jsonify(ok=True)

@app.route("/delete/<int:tid>", methods=["POST"])
def delete(tid):
    with session_scope() as s:
        t=s.get(Task,tid)
        if t: s.delete(t)
        tasks=s.query(Task).order_by(Task.position.asc(),Task.id.asc()).all()
        for i,x in enumerate(tasks): x.position=i; s.add(x)
    broadcast_update();  return jsonify(ok=True)

@app.route("/update/<int:tid>", methods=["POST"])
def update(tid):
    d=request.json or {}
    with session_scope() as s:
        t=s.get(Task,tid)
        if not t: return jsonify(ok=False,error="not found"),404
        if t.status not in ("pending","paused","done"):
            return jsonify(ok=False,error="cannot edit running"),400
        if "name" in d and d["name"].strip(): t.name=d["name"].strip()
        if any(k in d for k in("hours","minutes","seconds","duration")):
            h,m,sx=int(d.get("hours",0)),int(d.get("minutes",0)),int(d.get("seconds",0))
            duration=h*3600+m*60+sx
            t.duration=duration; t.remaining=duration; t.end_time=None
            if t.status=="done": t.status="pending"
        s.add(t)
    broadcast_update();  return jsonify(ok=True)

@app.route("/extend/<int:tid>", methods=["POST"])
def extend(tid):
    d=request.json or {}; extra=int(d.get("hours",0))*3600+int(d.get("minutes",0))*60+int(d.get("seconds",0))
    with session_scope() as s:
        t=s.get(Task,tid)
        if not t: return jsonify(ok=False,error="not found"),404
        t.duration+=extra; t.remaining+=extra
        if t.status=="running" and t.end_time: t.end_time+=timedelta(seconds=extra)
        s.add(t)
    broadcast_update();  return jsonify(ok=True)

@app.route("/skip/<int:tid>", methods=["POST"])
def skip(tid):
    with session_scope() as s:
        tasks=s.query(Task).order_by(Task.position.asc(),Task.id.asc()).all()
        ids=[t.id for t in tasks]; t=s.get(Task,tid)
        if t and t.status=="running":
            t.status="done"; t.remaining=0; t.end_time=None; s.add(t)
            if tid in ids:
                i=ids.index(tid)
                if i+1<len(tasks):
                    nxt=tasks[i+1]
                    if nxt.status=="pending":
                        nxt.status="running"; nxt.end_time=now()+timedelta(seconds=nxt.remaining); s.add(nxt)
    broadcast_update();  return jsonify(ok=True)

@app.route("/set_pending/<int:tid>", methods=["POST"])
def set_pending(tid):
    with session_scope() as s:
        t=s.get(Task,tid)
        if t and t.status in ("paused","done","pending"):
            if t.status=="done": t.remaining=t.duration
            t.status="pending"; t.end_time=None; s.add(t)
    broadcast_update();  return jsonify(ok=True)

@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    d=request.json or {}; tid=d.get("task_id"); newp=int(d.get("new_position",0))
    if not tid: return jsonify(ok=False,error="no task_id"),400
    with session_scope() as s:
        tasks=s.query(Task).order_by(Task.position.asc(),Task.id.asc()).all()
        ids=[t.id for t in tasks]
        if tid not in ids: return jsonify(ok=False,error="not found"),404
        old=ids.index(tid); newi=max(0,min(newp-1,len(ids)-1))
        ids.insert(newi,ids.pop(old))
        for i,tid2 in enumerate(ids):
            t=s.get(Task,tid2)
            if t: t.position=i; s.add(t)
    broadcast_update();  return jsonify(ok=True)

# ===== Run =====
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
