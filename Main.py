import os
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
import pytz

from flask import Flask, jsonify, render_template, request, make_response
from sqlalchemy import Column, DateTime, Integer, String, Boolean, create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

# ====================================================
# הגדרות בסיסיות
# ====================================================
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


def hhmmss(total_seconds):
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
    status = Column(String, nullable=False)
    end_time = Column(DateTime(timezone=True))
    position = Column(Integer, nullable=False, default=0)
    is_work = Column(Boolean, nullable=False, default=False)

    def to_dict(self):
        """המרת משימה למילון עבור ה-frontend"""
        rem = self.remaining
        now_ts = now()

        if self.status == "running" and self.end_time:
            if self.end_time.tzinfo is None:
                self.end_time = TZ.localize(self.end_time)
            rem = max(0, int((self.end_time - now_ts).total_seconds()))

        end_str = "-"
        if self.end_time:
            try:
                end_str = self.end_time.astimezone(TZ).strftime("%H:%M:%S")
            except:
                end_str = "-"

        return {
            "id": self.id,
            "name": self.name,
            "duration": self.duration,
            "remaining": rem,
            "status": self.status,
            "end_time_str": end_str,
            "position": self.position,
            "is_work": self.is_work
        }


Base.metadata.create_all(engine)


# ====================================================
# ניהול שרשרת משימות
# ====================================================
def recompute_chain():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        now_ts = now()
        running_found = False

        for i, t in enumerate(tasks):
            if t.status == "running":
                running_found = True

                if t.end_time:
                    if t.end_time.tzinfo is None:
                        t.end_time = TZ.localize(t.end_time)

                    rem = (t.end_time - now_ts).total_seconds()

                    if rem <= 0:
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
                        t.remaining = int(rem)
                        s.add(t)

        if not running_found:
            pass


def work_total_seconds():
    with session_scope() as s:
        items = s.query(Task).filter(Task.is_work == True).all()
        return sum(int(x.duration or 0) for x in items)


# ====================================================
# Flask App
# ====================================================
app = Flask(__name__)


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
        "work_total_seconds": work_total_seconds(),
        "work_total_hhmmss": hhmmss(work_total_seconds()),
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

    pos = int(data.get("position", 99999))

    with session_scope() as s:
        count = s.query(Task).count()
        pos = max(0, min(pos, count))

        tasks = s.query(Task).order_by(Task.position.asc()).all()
        for t in tasks:
            if t.position >= pos:
                t.position += 1
                s.add(t)

        s.add(Task(
            name=name,
            duration=dur,
            remaining=dur,
            status="pending",
            position=pos
        ))

    return jsonify({"ok": True})


@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t and t.status in ("pending", "paused"):
            t.status = "running"
            t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)
    return jsonify({"ok": True})


@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t and t.status == "running":
            rem = max(0, int((t.end_time - now()).total_seconds()))
            t.remaining = rem
            t.status = "paused"
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


@app.route("/reset/<int:tid>", methods=["POST"])
def reset(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.remaining = t.duration
            t.end_time = None
            if t.status == "running":
                t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)
    return jsonify({"ok": True})


@app.route("/done/<int:tid>", methods=["POST"])
def done(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "done"
            t.remaining = 0
            t.end_time = None
            s.add(t)
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

    return jsonify({"ok": True})


@app.route("/update/<int:tid>", methods=["POST"])
def update(tid):
    data = request.json or {}

    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False})

        name = data.get("name")
        if name:
            t.name = name

        h = int(data.get("hours", 0))
        m = int(data.get("minutes", 0))
        ssec = int(data.get("seconds", 0))
        dur = h * 3600 + m * 60 + ssec

        t.duration = dur
        t.remaining = dur
        t.end_time = None
        s.add(t)

    return jsonify({"ok": True})


@app.route("/extend/<int:tid>", methods=["POST"])
def extend(tid):
    data = request.json or {}
    extra = int(data.get("hours", 0)) * 3600 + int(data.get("minutes", 0)) * 60 + int(data.get("seconds", 0))

    if extra <= 0:
        return jsonify({"ok": False})

    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False})

        t.duration += extra
        t.remaining += extra
        if t.status == "running":
            t.end_time = now() + timedelta(seconds=t.remaining)
        s.add(t)

    return jsonify({"ok": True})


@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    data = request.json or {}
    tid = data.get("task_id")
    new_pos = int(data.get("new_position", 0)) - 1

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        ids = [t.id for t in tasks]

        if tid not in ids:
            return jsonify({"ok": False})

        old = ids.index(tid)
        ids.insert(new_pos, ids.pop(old))

        for i, idd in enumerate(ids):
            t = s.get(Task, idd)
            t.position = i
            s.add(t)

    return jsonify({"ok": True})


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

    return jsonify({"ok": True})


# ====================================================
# ריצה
# ====================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)