import os
import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytz
from flask import Flask, jsonify, render_template, request, make_response
from sqlalchemy import Column, DateTime, Integer, String, Boolean, create_engine, text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

# ===== Settings =====
TZ = pytz.timezone("Asia/Jerusalem")
DEFAULT_SQLITE_URL = "sqlite:///tasks.db"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DATA_SNAPSHOT = "tasks.json"

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

def now():
    return datetime.now(TZ)  # תמיד עם timezone-aware

def hhmmss(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ===== Model =====
class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    duration = Column(Integer, nullable=False)
    remaining = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
    end_time = Column(DateTime(timezone=True))  # timezone-aware
    position = Column(Integer, nullable=False, default=0)
    is_work = Column(Boolean, nullable=False, default=False)

    def to_dict(self):
        rem = self.remaining
        now_ts = now()
        if self.status == "running" and self.end_time:
            # גם אם end_time היה נאיבי, נוודא שיהיה tz-aware
            if self.end_time.tzinfo is None:
                self.end_time = TZ.localize(self.end_time)
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
            "is_work": bool(self.is_work),
        }

Base.metadata.create_all(engine)

# תיקון אם end_time נאיבי
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN is_work BOOLEAN DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

# ===== Flask App =====
app = Flask(__name__)

def snapshot_to_file():
    try:
        with session_scope() as s:
            tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
            payload = [t.to_dict() for t in tasks]
        with open(DATA_SNAPSHOT, "w", encoding="utf-8") as f:
            json.dump({"tasks": payload}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def load_from_snapshot_if_empty():
    with session_scope() as s:
        if s.query(Task).count() > 0:
            return
    if os.path.exists(DATA_SNAPSHOT):
        with open(DATA_SNAPSHOT, "r", encoding="utf-8") as f:
            data = json.load(f)
        with session_scope() as s:
            for idx, t in enumerate(data.get("tasks", [])):
                s.add(Task(
                    name=t.get("name", "משימה"),
                    duration=int(t.get("duration", 0)),
                    remaining=int(t.get("duration", 0)),
                    status=t.get("status", "pending"),
                    position=int(t.get("position", idx)),
                    is_work=bool(t.get("is_work", False))
                ))

load_from_snapshot_if_empty()

def recompute_chain():
    changed = False
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        now_ts = now()
        for i, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                # ודא שגם כאן end_time tz-aware
                if t.end_time.tzinfo is None:
                    t.end_time = TZ.localize(t.end_time)
                rem = int((t.end_time - now_ts).total_seconds())
                if rem <= 0:
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)
                    if i + 1 < len(tasks):
                        nxt = tasks[i + 1]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = now_ts + timedelta(seconds=int(nxt.remaining))
                            s.add(nxt)
                else:
                    t.remaining = rem
                    s.add(t)
                    changed = True
    if changed:
        snapshot_to_file()

def work_total_seconds():
    with session_scope() as s:
        items = s.query(Task).filter(Task.is_work == True).all()  # noqa
        return sum(int(x.duration or 0) for x in items)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/state")
def state():
    recompute_chain()
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]
    work_total = work_total_seconds()
    return jsonify({
        "ok": True,
        "tasks": payload,
        "work_total_seconds": int(work_total),
        "work_total_hhmmss": hhmmss(work_total),
        "now": now().strftime("%H:%M:%S %d.%m.%Y")
    })

@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "משימה חדשה").strip()
    h = int(data.get("hours", 0))
    m = int(data.get("minutes", 0))
    ssec = int(data.get("seconds", 0))
    dur = h * 3600 + m * 60 + ssec
    with session_scope() as s:
        pos = s.query(Task).count()
        s.add(Task(name=name, duration=dur, remaining=dur, status="pending", position=pos))
    snapshot_to_file()
    return jsonify({"ok": True})

@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "running"
            t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)
    snapshot_to_file()
    return jsonify({"ok": True})

@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t and t.end_time:
            if t.end_time.tzinfo is None:
                t.end_time = TZ.localize(t.end_time)
            rem = int((t.end_time - now()).total_seconds())
            t.remaining = max(0, rem)
            t.end_time = None
            t.status = "paused"
            s.add(t)
    snapshot_to_file()
    return jsonify({"ok": True})

@app.route("/workflag/<int:tid>", methods=["POST"])
def workflag(tid):
    data = request.json or {}
    val = bool(data.get("is_work", False))
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.is_work = val
            s.add(t)
    snapshot_to_file()
    return jsonify({"ok": True})

@app.route("/export")
def export():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]
    raw = json.dumps({"tasks": payload}, ensure_ascii=False, indent=2)
    resp = make_response(raw)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=tasks_export.json"
    return resp

@app.route("/import", methods=["POST"])
def import_tasks():
    data = request.json or {}
    items = data.get("tasks", [])
    with session_scope() as s:
        s.query(Task).delete()
        for i, t in enumerate(items):
            s.add(Task(
                name=t.get("name", "משימה"),
                duration=int(t.get("duration", 0)),
                remaining=int(t.get("duration", 0)),
                status=t.get("status", "pending"),
                position=i,
                is_work=bool(t.get("is_work", False))
            ))
    snapshot_to_file()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)