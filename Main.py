import os
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
import pytz

from flask import Flask, jsonify, render_template, request, make_response
from sqlalchemy import Column, DateTime, Integer, String, Boolean, create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

# ========================
#   הגדרות
# ========================

TZ = pytz.timezone("Asia/Jerusalem")

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

def hhmmss(sec):
    sec = max(0, int(sec or 0))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ========================
#   מודל מסד נתונים
# ========================

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    duration = Column(Integer, nullable=False)
    remaining = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="pending")
    position = Column(Integer, nullable=False, default=0)
    end_time = Column(DateTime(timezone=True))
    is_work = Column(Boolean, nullable=False, default=False)

    # --- לשימוש בצד לקוח ---
    def to_dict(self):
        rem = self.remaining

        if self.status == "running" and self.end_time:
            et = self.end_time
            if et.tzinfo is None:
                et = TZ.localize(et)

            rem = max(0, int((et - now()).total_seconds()))

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

# ========================
#   פונקציות שרשרת
# ========================

def auto_advance():
    """אם משימה רצה ונגמר הזמן — עוברים לפנדינג הבא."""
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()

        now_ts = now()

        for i, t in enumerate(tasks):
            if t.status == "running":
                if t.end_time:
                    if t.end_time.tzinfo is None:
                        t.end_time = TZ.localize(t.end_time)

                    rem = int((t.end_time - now_ts).total_seconds())

                    if rem <= 0:
                        # הסתיימה מעצמה → DONE → מפעילים הבאה
                        t.remaining = 0
                        t.status = "done"
                        t.end_time = None
                        s.add(t)

                        # משימה הבאה
                        if i + 1 < len(tasks):
                            nxt = tasks[i + 1]
                            if nxt.status == "pending":
                                nxt.status = "running"
                                nxt.end_time = now_ts + timedelta(seconds=nxt.remaining)
                                s.add(nxt)
                    else:
                        t.remaining = rem
                        s.add(t)


def work_time_total():
    """סה״כ זמן עבודה (משימות שסומנו כעבודה)."""
    with session_scope() as s:
        ts = s.query(Task).filter(Task.is_work == True).all()
        return sum(int(t.duration) for t in ts)


# ========================
#   Flask
# ========================

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/state")
def state():
    auto_advance()

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        out = [t.to_dict() for t in tasks]

    return jsonify({
        "ok": True,
        "tasks": out,
        "work_total_hhmmss": hhmmss(work_time_total()),
        "now": now().strftime("%H:%M:%S"),
    })


# ========================
#   פעולות CRUD
# ========================

@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "משימה חדשה").strip()

    h = int(data.get("hours", 0))
    m = int(data.get("minutes", 0))
    ssec = int(data.get("seconds", 0))
    dur = h * 3600 + m * 60 + ssec

    insert_pos = int(data.get("insert_position", -1))

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()

        if insert_pos < 1 or insert_pos > len(tasks) + 1:
            insert_pos = len(tasks) + 1

        # דוחפים את כל מה שאחרי למטה
        for t in tasks:
            if t.position >= insert_pos - 1:
                t.position += 1
                s.add(t)

        new_task = Task(
            name=name,
            duration=dur,
            remaining=dur,
            status="pending",
            position=insert_pos - 1
        )
        s.add(new_task)

    return jsonify({"ok": True})


@app.route("/update/<int:tid>", methods=["POST"])
def update(tid):
    data = request.json or {}
    name = data.get("name", "")
    h = int(data.get("hours", 0))
    m = int(data.get("minutes", 0))
    ssec = int(data.get("seconds", 0))
    new_dur = h * 3600 + m * 60 + ssec

    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.name = name
            t.duration = new_dur
            t.remaining = new_dur
            t.end_time = None
            s.add(t)

    return jsonify({"ok": True})


@app.route("/extend/<int:tid>", methods=["POST"])
def extend(tid):
    data = request.json or {}
    add_h = int(data.get("hours", 0))
    add_m = int(data.get("minutes", 0))
    add_s = int(data.get("seconds", 0))
    add_total = add_h * 3600 + add_m * 60 + add_s

    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.duration += add_total
            t.remaining += add_total

            if t.status == "running":
                t.end_time = now() + timedelta(seconds=t.remaining)

            s.add(t)

    return jsonify({"ok": True})


@app.route("/reset/<int:tid>", methods=["POST"])
def reset_task(tid):
    """מאפס זמן בלבד — לא מפעיל הבאה."""
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.remaining = t.duration
            t.end_time = None
            t.status = "pending"
            s.add(t)
    return jsonify({"ok": True})


@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "running"
            t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)
    return jsonify({"ok": True})


@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t.status == "running":
            if t.end_time:
                rem = max(0, int((t.end_time - now()).total_seconds()))
                t.remaining = rem

            t.end_time = None
            t.status = "paused"
            s.add(t)
    return jsonify({"ok": True})


@app.route("/set_pending/<int:tid>", methods=["POST"])
def set_pending(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "pending"
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


@app.route("/done/<int:tid>", methods=["POST"])
def done(tid):
    """DONE → לא מפעיל הבאה"""
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "done"
            t.remaining = 0
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


@app.route("/delete/<int:tid>", methods=["POST"])
def delete_task(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            s.delete(t)
    return jsonify({"ok": True})


@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    data = request.json or {}
    tid = int(data.get("task_id"))
    new_pos = int(data.get("new_position"))

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()

        if new_pos < 1:
            new_pos = 1
        if new_pos > len(tasks):
            new_pos = len(tasks)

        # הוצא משימה
        moving = None
        for t in tasks:
            if t.id == tid:
                moving = t
        if not moving:
            return jsonify({"ok": False})

        tasks.remove(moving)

        # הכנס מחדש
        tasks.insert(new_pos - 1, moving)

        # כתוב מיקומים חדשים
        for i, t in enumerate(tasks):
            t.position = i
            s.add(t)

    return jsonify({"ok": True})


# ========================
#   ייבוא / ייצוא
# ========================

@app.route("/export")
def export():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        arr = [t.to_dict() for t in tasks]

    raw = json.dumps({"tasks": arr}, ensure_ascii=False, indent=2)

    resp = make_response(raw)
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Content-Disposition"] = "attachment; filename=tasks_export.json"
    return resp


@app.route("/import", methods=["POST"])
def import_tasks():
    data = request.json or {}
    items = data.get("tasks", [])

    with session_scope() as s:
        s.query(Task).delete()

        for i, t in enumerate(items):
            tt = Task(
                name=t["name"],
                duration=int(t["duration"]),
                remaining=int(t["duration"]),
                status="pending",
                position=i,
                is_work=bool(t.get("is_work", False))
            )
            s.add(tt)

    return jsonify({"ok": True})


# ========================
#   הפעלה
# ========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)