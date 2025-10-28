import os
import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytz
from flask import Flask, jsonify, render_template, request, send_file, make_response
from sqlalchemy import Column, DateTime, Integer, String, Boolean, create_engine, text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

# ===== Settings =====
TZ = pytz.timezone("Asia/Jerusalem")
DEFAULT_SQLITE_URL = "sqlite:///tasks.db"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)
# Render/Heroku style prefix fix
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

DATA_SNAPSHOT = "tasks.json"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)
Session = scoped_session(
    sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False
    )
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

# ===== Model =====
class Task(Base):
    __tablename__ = "tasks"
    id        = Column(Integer, primary_key=True, autoincrement=True)
    name      = Column(String, nullable=False)
    duration  = Column(Integer, nullable=False)   # seconds
    remaining = Column(Integer, nullable=False)   # seconds
    status    = Column(String, nullable=False)    # pending|running|paused|done
    end_time  = Column(DateTime(timezone=True))   # aware
    position  = Column(Integer, nullable=False, default=0)  # explicit order
    is_work   = Column(Boolean, nullable=False, default=False)

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
            "is_work": bool(self.is_work),
        }

Base.metadata.create_all(engine)

# add columns if missing (safe to run repeatedly)
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

# ===== Flask =====
app = Flask(__name__)

# ===== Time helpers =====
def now():
    return datetime.now(TZ)

def hhmmss(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

# ===== Persistence helpers (snapshot to file) =====
def snapshot_to_file():
    try:
        with session_scope() as s:
            tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
            payload = [t.to_dict() for t in tasks]
        with open(DATA_SNAPSHOT, "w", encoding="utf-8") as f:
            json.dump({"tasks": payload}, f, ensure_ascii=False, indent=2)
    except Exception:
        # לא מפיל את האפליקציה אם אין אפשרות לכתוב לקובץ
        pass

def load_from_snapshot_if_empty():
    with session_scope() as s:
        count = s.query(Task).count()
        if count > 0:
            return
    if os.path.exists(DATA_SNAPSHOT):
        try:
            with open(DATA_SNAPSHOT, "r", encoding="utf-8") as f:
                data = json.load(f)
            tasks = data.get("tasks", [])
            with session_scope() as s:
                for idx, t in enumerate(tasks):
                    task = Task(
                        name=t.get("name","משימה"),
                        duration=int(t.get("duration",0)),
                        remaining=int(t.get("duration",0)),  # ברירת מחדל: remaining=duration
                        status=t.get("status","pending") if t.get("status") in ("pending","paused","done") else "pending",
                        end_time=None,
                        position=int(t.get("position", idx)),
                        is_work=bool(t.get("is_work", False))
                    )
                    s.add(task)
        except Exception:
            pass

load_from_snapshot_if_empty()

# ===== Chain logic =====
def any_running(s):
    return s.query(Task).filter(Task.status == "running").first() is not None

def any_active(s):
    return s.query(Task).filter(Task.status.in_(["running", "paused"])).first() is not None

def recompute_chain_in_db():
    """סוגר רצות שנגמרו, מעדכן remaining, ומפעיל אוטומטית את הבאה בתור לפי position."""
    changed = False
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        now_ts = now()
        for idx, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                rem = int((t.end_time - now_ts).total_seconds())
                if rem <= 0:
                    # finish current
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)
                    changed = True
                    # autostart next pending
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
                        changed = True
    if changed:
        snapshot_to_file()

def overall_end_time_calc():
    """שעת סיום כוללת לפי שרשרת position."""
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        if not tasks:
            return None
        base = now()
        # latest running end as base
        for t in tasks:
            if t.status == "running" and t.end_time and t.end_time > base:
                base = t.end_time
        # add all pending/paused remaining
        for t in tasks:
            if t.status in ("pending", "paused"):
                base = base + timedelta(seconds=int(max(0, t.remaining)))
        return base

def work_total_seconds():
    """סכום זמן העבודה הכולל לפי משימות is_work=True — לפי duration שהוגדר מראש."""
    with session_scope() as s:
        items = s.query(Task).filter(Task.is_work == True).all()  # noqa: E712
        total = sum(int(x.duration or 0) for x in items)
        return total

# ===== Routes =====
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/state")
def state():
    # update chain first
    recompute_chain_in_db()
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]
    end_all = overall_end_time_calc()
    end_all_str = end_all.astimezone(TZ).strftime("%H:%M:%S %d.%m.%Y") if end_all else "-"
    work_total = work_total_seconds()
    return jsonify({
        "ok": True,
        "tasks": payload,
        "overall_end_time": end_all_str,
        "work_total_seconds": int(work_total),
        "work_total_hhmmss": hhmmss(work_total),
        "now": now().strftime("%H:%M:%S %d.%m.%Y")
    })

# ===== Task management =====
@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "").strip() or "משימה חדשה"
    h = int(data.get("hours") or 0)
    m = int(data.get("minutes") or 0)
    ssec = int(data.get("seconds") or 0)
    duration = int(data.get("duration") or (h*3600 + m*60 + ssec))
    duration = max(0, duration)
    with session_scope() as s:
        # place at end by position
        max_pos = (s.query(Task).count())
        t = Task(
            name=name,
            duration=duration,
            remaining=duration,
            status="pending",
            end_time=None,
            position=max_pos,
            is_work=False,
        )
        s.add(t)
    snapshot_to_file()
    return jsonify({"ok": True})

@app.route("/start/<int:task_id>", methods=["POST"])
def start(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404
        # only if nothing else is running
        if t.status in ("pending", "paused") and not any_running(s):
            t.end_time = now() + timedelta(seconds=int(t.remaining))
            t.status = "running"
            s.add(t)
        # allow re-run done only if no active (running/paused)
        elif t.status == "done" and not any_active(s):
            t.remaining = int(t.duration)
            t.end_time = now() + timedelta(seconds=int(t.remaining))
            t.status = "running"
            s.add(t)
    snapshot_to_file()
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
    snapshot_to_file()
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
    snapshot_to_file()
    return jsonify({"ok": True})

@app.route("/delete/<int:task_id>", methods=["POST"])
def delete(task_id):
    with session_scope() as s:
        t = s.get(Task, task_id)
        if t:
            s.delete(t)
            # normalize positions after delete
            tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
            for idx, x in enumerate(tasks):
                if x.position != idx:
                    x.position = idx
                    s.add(x)
    snapshot_to_file()
    return jsonify({"ok": True})

@app.route("/update/<int:task_id>", methods=["POST"])
def update(task_id):
    """עריכת שם/זמן (לא בזמן ריצה)."""
    data = request.json or {}
    with session_scope() as s:
        t = s.get(Task, task_id)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404

        # עריכה מותרת ב-pending/paused/done, לא בזמן running
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
            t.end_time = None
            if t.status == "done":
                t.status = "pending"

        s.add(t)
    snapshot_to_file()
    return jsonify({"ok": True})

@app.route("/extend/<int:task_id>", methods=["POST"])
def extend(task_id):
    """הארכת משימה בזמן חופשי (שעות/דקות/שניות)."""
    data = request.json or {}
    extra = int(data.get("hours", 0))*3600 + int(data.get("minutes", 0))*60 + int(data.get("seconds", 0))
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
    snapshot_to_file()
    return jsonify({"ok": True})

@app.route("/skip/<int:task_id>", methods=["POST"])
def skip(task_id):
    """דלג לבאה: מסמן רצה כ-done ומפעיל את הבאה בתור לפי position."""
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
    snapshot_to_file()
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
    snapshot_to_file()
    return jsonify({"ok": True})

# ===== Work flag =====
@app.route("/workflag/<int:task_id>", methods=["POST"])
def workflag(task_id):
    data = request.json or {}
    val = bool(data.get("is_work", False))
    with session_scope() as s:
        t = s.get(Task, task_id)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404
        t.is_work = val
        s.add(t)
    snapshot_to_file()
    return jsonify({"ok": True})

# ===== Reorder (single) =====
@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    """
    מקבל: task_id, new_position (1-based).
    מזיז משימה אחת למיקום החדש ומעדכן את כל ה-position בהתאם.
    """
    data = request.json or {}
    task_id = data.get("task_id")
    try:
        new_position = int(data.get("new_position", 0))
    except Exception:
        return jsonify({"ok": False, "error": "invalid new_position"}), 400

    if not task_id:
        return jsonify({"ok": False, "error": "no task_id provided"}), 400

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        ids = [t.id for t in tasks]

        if task_id not in ids:
            return jsonify({"ok": False, "error": "task not found"}), 404

        old_index = ids.index(task_id)
        # clamp new index
        new_index = max(0, min(new_position - 1, len(ids) - 1))

        # no-op
        if new_index == old_index:
            return jsonify({"ok": True})  # nothing to change

        # reorder
        ids.insert(new_index, ids.pop(old_index))

        # rewrite sequential positions
        for idx, tid in enumerate(ids):
            t = s.get(Task, tid)
            if t and t.position != idx:
                t.position = idx
                s.add(t)
    snapshot_to_file()
    return jsonify({"ok": True})

# ===== Export/Import JSON =====
@app.route("/export")
def export_tasks():
    # תמיד נייצר קובץ זמני עדכני מה־DB
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
    """
    מצפה ל-JSON גוף: { "tasks": [ {id?, name, duration, remaining?, status, position, is_work}, ... ] }
    - מאפס את הטבלה ומכניס מחדש לפי position.
    - remaining יוגדר כברירת מחדל = duration, והסטטוס יהיה pending/paused/done (לא running).
    """
    data = request.json or {}
    items = data.get("tasks", [])
    with session_scope() as s:
        # מחיקת הכל
        s.query(Task).delete()
        # הכנסת משימות
        for idx, t in enumerate(items):
            duration = int(t.get("duration", 0))
            status = t.get("status", "pending")
            if status not in ("pending", "paused", "done"):
                status = "pending"
            obj = Task(
                name = (t.get("name") or "משימה").strip() or "משימה",
                duration = duration,
                remaining = duration,  # ברירת מחדל
                status = status,
                end_time = None,
                position = int(t.get("position", idx)),
                is_work = bool(t.get("is_work", False))
            )
            s.add(obj)
    snapshot_to_file()
    return jsonify({"ok": True})

# ===== Run =====
if __name__ == "__main__":
    # for local dev
    app.run(host="0.0.0.0", port=5000)