import os
import json
from datetime import datetime, timedelta
from contextlib import contextmanager

import pytz
from flask import Flask, jsonify, render_template, request, make_response
from sqlalchemy import Column, DateTime, Integer, String, Boolean, create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

# ====================================================
# הגדרות בסיסיות
# ====================================================
TZ = pytz.timezone("Asia/Jerusalem")

# חיבור למסד הנתונים (Render Postgres)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://matan_nb_user:Qzcukb3uonnqU3wgDxKyzkxeEaT83PJp@dpg-d40u1m7gi27c73d0oorg-a:5432/matan_nb"
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


def hhmmss(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds or 0))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ====================================================
# מודל המשימה
# ====================================================
class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    duration = Column(Integer, nullable=False)
    remaining = Column(Integer, nullable=False)
    status = Column(String, nullable=False)          # running / paused / pending / done
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

# ====================================================
# אפליקציית Flask
# ====================================================
app = Flask(__name__)


# ====================================================
# פונקציית רקע – עדכון שרשרת המשימות
# ====================================================
def recompute_chain():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        now_ts = now()

        for i, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                if t.end_time.tzinfo is None:
                    t.end_time = TZ.localize(t.end_time)

                rem = int((t.end_time - now_ts).total_seconds())

                if rem <= 0:
                    # המשימה הסתיימה – מסמנים done ומפעילים הבאה
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)

                    if i + 1 < len(tasks):
                        nxt = tasks[i + 1]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = now_ts + timedelta(seconds=nxt.remaining)
                            s.add(nxt)
                else:
                    # מעדכנים כמה זמן נשאר
                    t.remaining = rem
                    s.add(t)


def work_total_seconds():
    with session_scope() as s:
        items = s.query(Task).filter(Task.is_work == True).all()
        return sum(int(x.duration or 0) for x in items)


# ====================================================
# ROUTES
# ====================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/state")
def state():
    recompute_chain()
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        payload = [t.to_dict() for t in tasks]

    return jsonify({
        "ok": True,
        "tasks": payload,
        "work_total_hhmmss": hhmmss(work_total_seconds()),
        "now": now().strftime("%H:%M:%S %d.%m.%Y"),
    })


# ---------- יצירת משימה ----------
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
        s.add(Task(name=name, duration=dur, remaining=dur,
                   status="pending", position=pos))
    return jsonify(ok=True)


# ---------- עדכון מלא של משימה ----------
@app.route("/update/<int:tid>", methods=["POST"])
def update_task(tid):
    data = request.json or {}
    name = (data.get("name") or "").strip()
    h = int(data.get("hours", 0))
    m = int(data.get("minutes", 0))
    ssec = int(data.get("seconds", 0))
    dur = h * 3600 + m * 60 + ssec

    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            if name:
                t.name = name
            t.duration = dur
            t.remaining = dur
            t.end_time = None
            if t.status == "running":
                t.status = "paused"
            s.add(t)
    return jsonify(ok=True)


# ---------- הארכת משימה ----------
@app.route("/extend/<int:tid>", methods=["POST"])
def extend(tid):
    data = request.json or {}
    h = int(data.get("hours", 0))
    m = int(data.get("minutes", 0))
    ssec = int(data.get("seconds", 0))
    add_sec = h * 3600 + m * 60 + ssec

    with session_scope() as s:
        t = s.get(Task, tid)
        if t and add_sec > 0:
            t.duration += add_sec
            t.remaining += add_sec
            if t.status == "running" and t.end_time:
                t.end_time = t.end_time + timedelta(seconds=add_sec)
            s.add(t)
    return jsonify(ok=True)


# ---------- התחל ----------
@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "running"
            t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)
    return jsonify(ok=True)


# ---------- השהה ----------
@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t and t.end_time:
            if t.end_time.tzinfo is None:
                t.end_time = TZ.localize(t.end_time)
            rem = int((t.end_time - now()).total_seconds())
            t.remaining = max(0, rem)
            t.status = "paused"
            t.end_time = None
            s.add(t)
    return jsonify(ok=True)


# ---------- DONE ----------
@app.route("/done/<int:tid>", methods=["POST"])
def mark_done(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "done"
            t.remaining = 0
            t.end_time = None
            s.add(t)
    return jsonify(ok=True)


# ---------- החזרה ל־Pending ----------
@app.route("/set_pending/<int:tid>", methods=["POST"])
def set_pending(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "pending"
            t.end_time = None
            s.add(t)
    return jsonify(ok=True)


# ---------- Skip: מסמן Done ומדלג למשימה הבאה Pending ----------
@app.route("/skip/<int:tid>", methods=["POST"])
def skip(tid):
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        now_ts = now()
        for i, t in enumerate(tasks):
            if t.id == tid:
                t.status = "done"
                t.remaining = 0
                t.end_time = None
                s.add(t)

                if i + 1 < len(tasks):
                    nxt = tasks[i + 1]
                    if nxt.status == "pending":
                        nxt.status = "running"
                        nxt.end_time = now_ts + timedelta(seconds=nxt.remaining)
                        s.add(nxt)
                break
    return jsonify(ok=True)


# ---------- מחיקה ----------
@app.route("/delete/<int:tid>", methods=["POST"])
def delete(tid):
    with session_scope() as s:
        s.query(Task).filter(Task.id == tid).delete()
    return jsonify(ok=True)


# ---------- שינוי סדר של משימה אחת ----------
@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    data = request.json or {}
    tid = int(data.get("task_id"))
    new_pos = max(0, int(data.get("new_position", 1)) - 1)

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        ids = [t.id for t in tasks]

        if tid not in ids:
            return jsonify(ok=False, error="task not found")

        ids.remove(tid)
        new_pos = min(new_pos, len(ids))
        ids.insert(new_pos, tid)

        # מעדכנים positions לפי הסדר החדש
        id_to_task = {t.id: t for t in tasks}
        for idx, task_id in enumerate(ids):
            task = id_to_task[task_id]
            task.position = idx
            s.add(task)

    return jsonify(ok=True)


# ---------- סימון כעבודה / לא עבודה ----------
@app.route("/workflag/<int:tid>", methods=["POST"])
def workflag(tid):
    data = request.json or {}
    val = bool(data.get("is_work", False))
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.is_work = val
            s.add(t)
    return jsonify(ok=True)


# ---------- Export ----------
@app.route("/export")
def export():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        payload = [t.to_dict() for t in tasks]

    raw = json.dumps({"tasks": payload}, ensure_ascii=False, indent=2)
    resp = make_response(raw)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=tasks_export.json"
    return resp


# ---------- Import ----------
@app.route("/import", methods=["POST"])
def import_tasks():
    data = request.json or {}
    items = data.get("tasks", [])

    with session_scope() as s:
        s.query(Task).delete()
        for i, t in enumerate(items):
            duration = int(t.get("duration", 0))
            s.add(Task(
                name=t.get("name", "משימה"),
                duration=duration,
                remaining=duration,
                status=t.get("status", "pending"),
                position=i,
                is_work=bool(t.get("is_work", False)),
            ))
    return jsonify(ok=True)


# ====================================================
# ריצה מקומית
# ====================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)