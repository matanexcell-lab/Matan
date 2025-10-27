import os
import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytz
from flask import Flask, jsonify, render_template, request, Response
from sqlalchemy import Column, DateTime, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

# ===== הגדרות בסיס =====
TZ = pytz.timezone("Asia/Jerusalem")
DEFAULT_SQLITE_URL = "sqlite:///tasks.db"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)

# תיקון לכתובת PostgreSQL (אם מישהו ישתמש)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)
Session = scoped_session(
    sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
)
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

# ===== מודל טבלה =====
class Task(Base):
    __tablename__ = "tasks"
    id        = Column(Integer, primary_key=True, autoincrement=True)
    name      = Column(String, nullable=False)
    duration  = Column(Integer, nullable=False)   # שניות
    remaining = Column(Integer, nullable=False)   # שניות
    status    = Column(String, nullable=False)    # pending|running|paused|done
    end_time  = Column(DateTime(timezone=True))   # aware
    position  = Column(Integer, nullable=False, default=0)
    is_work   = Column(Integer, nullable=False, default=0)  # 0/1 האם זו משימת "עבודה"

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
            "is_work": int(self.is_work or 0),
        }

Base.metadata.create_all(engine)

# הוספת עמודות אם חסרות (idempotent)
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN is_work INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass

# ===== Flask =====
app = Flask(__name__)

# ===== פונקציות עזר =====
def now():
    return datetime.now(TZ)

def hhmmss(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def recompute_chain_in_db():
    """סוגר רצות שנגמרו ומפעיל את הבאה בתור לפי position."""
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
    """שעת סיום כוללת לפי סדר."""
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

# ===== דפים =====
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

# ===== פעולות =====
@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "").strip() or "משימה חדשה"
    h = int(data.get("hours") or 0)
    m = int(data.get("minutes") or 0)
    ssec = int(data.get("seconds") or 0)
    duration = max(0, h*3600 + m*60 + ssec)
    with session_scope() as s:
        max_pos = s.query(Task).count()
        t = Task(name=name, duration=duration, remaining=duration, status="pending", position=max_pos, is_work=0)
        s.add(t)
    return jsonify({"ok": True})

@app.route("/start/<int:task_id>", methods=["POST"])
def start(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t and t.status in ("pending", "paused") and not s.query(Task).filter(Task.status == "running").first():
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
            t.remaining = t.duration
            t.status = "running"
            t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)
    return jsonify({"ok": True})

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t:
            s.delete(t)
            tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
            for idx, x in enumerate(tasks):
                if x.position != idx:
                    x.position = idx
                    s.add(x)
    return jsonify({"ok": True})

@app.route("/update/<int:task_id>", methods=["POST"])
def update(task_id):
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
        if any(k in data for k in ("hours","minutes","seconds")):
            h = int(data.get("hours") or 0)
            m = int(data.get("minutes") or 0)
            ssec = int(data.get("seconds") or 0)
            duration = max(0, h*3600 + m*60 + ssec)
            t.duration = duration
            t.remaining = duration
            t.end_time = None
            if t.status == "done":
                t.status = "pending"
        s.add(t)
    return jsonify({"ok": True})

@app.route("/extend/<int:task_id>", methods=["POST"])
def extend(task_id):
    data = request.json or {}
    extra = int(data.get("hours",0))*3600 + int(data.get("minutes",0))*60 + int(data.get("seconds",0))
    if extra <= 0:
        return jsonify({"ok": False, "error": "extra must be > 0"}), 400
    with session_scope() as s:
        t = s.get(Task, task_id)
        if not t: return jsonify({"ok": False, "error": "not found"}), 404
        t.duration += extra
        if t.status == "running" and t.end_time:
            rem = max(0, int((t.end_time - now()).total_seconds()))
            t.remaining = rem + extra
            t.end_time += timedelta(seconds=extra)
        else:
            t.remaining += extra
        s.add(t)
    return jsonify({"ok": True})

@app.route("/skip/<int:task_id>", methods=["POST"])
def skip(task_id):
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
                    nxt = tasks[idx + 1]
                    if nxt.status == "pending":
                        nxt.status = "running"
                        nxt.end_time = now() + timedelta(seconds=int(nxt.remaining))
                        s.add(nxt)
    return jsonify({"ok": True})

@app.route("/set_pending/<int:task_id>", methods=["POST"])
def set_pending(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t and t.status in ("paused","done","pending"):
            if t.status == "done":
                t.remaining = t.duration
            t.status = "pending"
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})

@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    data = request.json or {}
    task_id = data.get("task_id")
    try:
        new_pos = int(data.get("new_position", 0))
    except Exception:
        return jsonify({"ok": False, "error": "invalid new_position"}), 400
    if not task_id:
        return jsonify({"ok": False, "error": "no task_id"}), 400
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        ids = [t.id for t in tasks]
        if task_id not in ids:
            return jsonify({"ok": False, "error": "task not found"}), 404
        old_idx = ids.index(task_id)
        new_idx = max(0, min(new_pos-1, len(ids)-1))
        ids.insert(new_idx, ids.pop(old_idx))
        for idx, tid in enumerate(ids):
            t = s.get(Task, tid)
            if t and t.position != idx:
                t.position = idx
                s.add(t)
    return jsonify({"ok": True})

# ===== סימון משימה כ"עבודה" =====
@app.route("/set_work/<int:task_id>", methods=["POST"])
def set_work(task_id):
    data = request.json or {}
    val = 1 if data.get("is_work") else 0
    with session_scope() as s:
        t = s.get(Task, task_id)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404
        t.is_work = val
        s.add(t)
    return jsonify({"ok": True})

# ===== יצוא/יבוא לקובץ JSON =====
@app.route("/export_json", methods=["GET"])
def export_json():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]
    data = json.dumps({"version": 1, "generated_at": now().isoformat(), "tasks": payload}, ensure_ascii=False)
    return Response(
        data,
        mimetype="application/json",
        headers={"Content-Disposition": 'attachment; filename="tasks_backup.json"'}
    )

@app.route("/import_json", methods=["POST"])
def import_json():
    data = request.get_json(silent=True) or {}
    items = data.get("tasks", [])
    if not isinstance(items, list):
        return jsonify({"ok": False, "error": "invalid payload"}), 400

    with session_scope() as s:
        # מוחקים הכל
        s.query(Task).delete()

        # מכניסים מחדש לפי הסדר
        for idx, it in enumerate(items):
            name      = (it.get("name") or "").strip() or "משימה"
            duration  = int(it.get("duration") or 0)
            remaining = int(it.get("remaining") or duration)
            status    = it.get("status") or "pending"
            is_work   = 1 if it.get("is_work") else 0

            end_time_str = it.get("end_time")
            end_dt = None
            if end_time_str:
                try:
                    end_dt = datetime.fromisoformat(end_time_str)
                    if end_dt.tzinfo is None:
                        end_dt = TZ.localize(end_dt)
                except Exception:
                    end_dt = None

            t = Task(
                name=name,
                duration=duration,
                remaining=remaining,
                status=status,
                end_time=end_dt,
                position=idx,
                is_work=is_work
            )
            s.add(t)

    return jsonify({"ok": True})

# ===== Run =====
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)