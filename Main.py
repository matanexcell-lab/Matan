import os
import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytz
from flask import Flask, jsonify, render_template, request, make_response
from sqlalchemy import Column, DateTime, Integer, String, Boolean, create_engine, text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

# ==============================
# הגדרות בסיס
# ==============================
TZ = pytz.timezone("Asia/Jerusalem")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://matan_nb_user:Qzcukb3uonnqU3wgDxKyzkxeEaT83PJp@dpg-d40u1m7gi27c73d0oorg-a:5432/matan_nb",
)

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
    return datetime.now(TZ)


def hhmmss(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ==============================
# מודל טבלה
# ==============================
class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    duration = Column(Integer, nullable=False)
    remaining = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
    end_time = Column(DateTime(timezone=True))
    position = Column(Integer, nullable=False, default=0)
    is_work = Column(Boolean, nullable=False, default=False)

    def to_dict(self):
        rem = self.remaining
        if self.status == "running" and self.end_time:
            if self.end_time.tzinfo is None:
                self.end_time = TZ.localize(self.end_time)
            rem = max(0, int((self.end_time - now()).total_seconds()))
        return {
            "id": self.id,
            "name": self.name,
            "duration": self.duration,
            "remaining": rem,
            "status": self.status,
            "position": self.position,
            "is_work": self.is_work,
        }


Base.metadata.create_all(engine)

# ==============================
# פונקציות עזר
# ==============================
def recompute_chain():
    """מעדכן משימות רצות ומפעיל את הבאה בתור אם נגמר הזמן."""
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        now_ts = now()

        for i, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                if t.end_time.tzinfo is None:
                    t.end_time = TZ.localize(t.end_time)
                rem = int((t.end_time - now_ts).total_seconds())

                if rem <= 0:
                    # המשימה הסתיימה
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)

                    # הפעל משימה הבאה אם קיימת Pending
                    for nxt in tasks[i + 1:]:
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = now_ts + timedelta(seconds=nxt.remaining)
                            s.add(nxt)
                            break
                else:
                    t.remaining = rem
                    s.add(t)


def work_total_seconds():
    with session_scope() as s:
        items = s.query(Task).filter(Task.is_work == True).all()  # noqa: E712
        return sum(int(x.duration or 0) for x in items)


# ==============================
# Flask App
# ==============================
app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/state")
def state():
    recompute_chain()
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        data = [t.to_dict() for t in tasks]
    return jsonify({"ok": True, "tasks": data, "work_total_hhmmss": hhmmss(work_total_seconds())})


@app.route("/add", methods=["POST"])
def add():
    d = request.json
    name = d.get("name", "משימה חדשה")
    h, m, ssec = d.get("hours", 0), d.get("minutes", 0), d.get("seconds", 0)
    dur = h * 3600 + m * 60 + ssec
    with session_scope() as s:
        pos = s.query(Task).count()
        s.add(Task(name=name, duration=dur, remaining=dur, status="pending", position=pos))
    return jsonify({"ok": True})


@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    with session_scope() as s:
        running = s.query(Task).filter(Task.status == "running").first()
        t = s.get(Task, tid)
        if t and not running:
            t.status = "running"
            t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)
    return jsonify({"ok": True})


@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t and t.status == "running" and t.end_time:
            rem = int((t.end_time - now()).total_seconds())
            t.remaining = max(0, rem)
            t.status = "paused"
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


@app.route("/reset/<int:tid>", methods=["POST"])
def reset(tid):
    """מאפס רק את הזמן של המשימה — לא משנה סטטוס ולא מדלג."""
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.remaining = int(t.duration or 0)
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


@app.route("/done/<int:tid>", methods=["POST"])
def done(tid):
    """מסיים משימה ומדליק את הבאה ב-pending"""
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        now_ts = now()
        for i, t in enumerate(tasks):
            if t.id == tid:
                t.status = "done"
                t.remaining = 0
                t.end_time = None
                s.add(t)

                # מפעיל את הבאה בתור אם Pending
                for nxt in tasks[i + 1:]:
                    if nxt.status == "pending":
                        nxt.status = "running"
                        nxt.end_time = now_ts + timedelta(seconds=nxt.remaining)
                        s.add(nxt)
                        break
                break
    return jsonify({"ok": True})


@app.route("/extend/<int:tid>", methods=["POST"])
def extend(tid):
    d = request.json
    extra = d.get("hours", 0) * 3600 + d.get("minutes", 0) * 60 + d.get("seconds", 0)
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.duration += extra
            t.remaining += extra
            if t.status == "running" and t.end_time:
                t.end_time += timedelta(seconds=extra)
            s.add(t)
    return jsonify({"ok": True})


@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    d = request.json
    task_id = d.get("task_id")
    new_pos = int(d.get("new_position", 0))
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        ids = [t.id for t in tasks]
        if task_id not in ids:
            return jsonify({"ok": False})
        ids.insert(new_pos - 1, ids.pop(ids.index(task_id)))
        for i, tid in enumerate(ids):
            t = s.get(Task, tid)
            if t:
                t.position = i
                s.add(t)
    return jsonify({"ok": True})


@app.route("/all_pending", methods=["POST"])
def all_pending():
    with session_scope() as s:
        for t in s.query(Task).all():
            if t.status == "done":
                t.remaining = t.duration
            t.status = "pending"
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


@app.route("/import", methods=["POST"])
def import_tasks():
    d = request.json
    with session_scope() as s:
        s.query(Task).delete()
        for i, x in enumerate(d.get("tasks", [])):
            s.add(Task(
                name=x["name"],
                duration=x["duration"],
                remaining=x["duration"],
                status="pending",
                position=i,
                is_work=x.get("is_work", False)
            ))
    return jsonify({"ok": True})


@app.route("/export")
def export():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        data = [t.to_dict() for t in tasks]
    raw = json.dumps({"tasks": data}, ensure_ascii=False, indent=2)
    resp = make_response(raw)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)