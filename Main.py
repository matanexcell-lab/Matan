import os
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
import pytz

from flask import Flask, jsonify, request, render_template, make_response
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime,
    create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session

TZ = pytz.timezone("Asia/Jerusalem")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://matan_nb_user:Qzcukb3uonnqU3wgDxKyzkxeEaT83PJp@dpg-d40u1m7gi27c73d0oorg-a:5432/matan_nb"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
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
    except:
        s.rollback()
        raise
    finally:
        s.close()


def now():
    return datetime.now(TZ)


def hhmmss(sec):
    sec = max(0, int(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


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
        now_ts = now()
        rem = self.remaining

        if self.status == "running" and self.end_time:
            et = self.end_time
            if et.tzinfo is None:
                et = TZ.localize(et)
            rem = max(0, int((et - now_ts).total_seconds()))

        end_str = "-"
        if self.end_time:
            try:
                end_str = self.end_time.astimezone(TZ).strftime("%H:%M:%S")
            except:
                pass

        return {
            "id": self.id,
            "name": self.name,
            "duration": self.duration,
            "remaining": rem,
            "status": self.status,
            "position": self.position,
            "is_work": self.is_work,
            "end_time_str": end_str
        }


Base.metadata.create_all(engine)


def recompute_chain():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        now_ts = now()

        for i, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                et = t.end_time
                if et.tzinfo is None:
                    et = TZ.localize(et)

                rem = int((et - now_ts).total_seconds())

                if rem <= 0:
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)

                    for nxt in tasks[i+1:]:
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
        rows = s.query(Task).filter(Task.is_work == True).all()
        return sum(t.duration for t in rows)


app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/state")
def state():
    recompute_chain()
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        arr = [t.to_dict() for t in tasks]

    return jsonify({
        "ok": True,
        "tasks": arr,
        "work_total_seconds": work_total_seconds(),
        "work_total_hhmmss": hhmmss(work_total_seconds()),
        "now": now().strftime("%H:%M:%S")
    })


@app.route("/add", methods=["POST"])
def add():
    d = request.json or {}

    name = d.get("name", "משימה חדשה")
    h = int(d.get("hours", 0))
    m = int(d.get("minutes", 0))
    ssec = int(d.get("seconds", 0))
    pos = int(d.get("position", 1)) - 1

    dur = h * 3600 + m * 60 + ssec

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()

        if pos < 0: pos = 0
        if pos > len(tasks): pos = len(tasks)

        for t in tasks:
            if t.position >= pos:
                t.position += 1
                s.add(t)

        new = Task(
            name=name,
            duration=dur,
            remaining=dur,
            status="pending",
            position=pos
        )
        s.add(new)

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
        if t and t.end_time:
            rem = int((t.end_time - now()).total_seconds())
            t.remaining = max(0, rem)
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
            t.status = "paused"
            t.end_time = None
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


@app.route("/set_pending/<int:tid>", methods=["POST"])
def set_pending(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "pending"
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


@app.route("/skip/<int:tid>", methods=["POST"])
def skip(tid):
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()

        for i, t in enumerate(tasks):
            if t.id == tid:
                t.status = "done"
                t.remaining = 0
                t.end_time = None
                s.add(t)
                break

        now_ts = now()
        for nxt in tasks[i+1:]:
            if nxt.status == "pending":
                nxt.status = "running"
                nxt.end_time = now_ts + timedelta(seconds=nxt.remaining)
                s.add(nxt)
                break

    return jsonify({"ok": True})


@app.route("/update/<int:tid>", methods=["POST"])
def update_task(tid):
    d = request.json or {}
    name = d.get("name", "משימה")

    h = int(d.get("hours", 0))
    m = int(d.get("minutes", 0))
    ssec = int(d.get("seconds", 0))
    dur = h * 3600 + m * 60 + ssec

    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.name = name
            t.duration = dur
            if t.status in ["pending","paused","running"]:
                t.remaining = dur
            if t.status == "running":
                t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)
    return jsonify({"ok": True})


@app.route("/extend/<int:tid>", methods=["POST"])
def extend(tid):
    d = request.json or {}
    add = int(d.get("hours",0))*3600 + int(d.get("minutes",0))*60 + int(d.get("seconds",0))

    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.duration += add
            t.remaining += add
            if t.status == "running":
                t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)
    return jsonify({"ok": True})


@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    d = request.json or {}
    tid = int(d.get("task_id"))
    new_pos = int(d.get("new_position")) - 1

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()

        if new_pos < 0: new_pos = 0
        if new_pos > len(tasks): new_pos = len(tasks)

        moving = s.get(Task, tid)
        if not moving:
            return jsonify({"ok": False})

        old_pos = moving.position

        for t in tasks:
            if t.id == tid: continue
            if old_pos < new_pos and old_pos < t.position <= new_pos:
                t.position -= 1
            elif new_pos <= t.position < old_pos:
                t.position += 1
            s.add(t)

        moving.position = new_pos
        s.add(moving)

    return jsonify({"ok": True})


@app.route("/delete/<int:tid>", methods=["POST"])
def delete(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            s.delete(t)

        tasks = s.query(Task).order_by(Task.position.asc()).all()
        for i, t in enumerate(tasks):
            t.position = i
            s.add(t)

    return jsonify({"ok": True})


@app.route("/export")
def export():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        arr = [t.to_dict() for t in tasks]

    txt = json.dumps({"tasks": arr}, ensure_ascii=False, indent=2)
    resp = make_response(txt)
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Content-Disposition"] = "attachment; filename=tasks_export.json"
    return resp


@app.route("/import", methods=["POST"])
def import_tasks():
    d = request.json or {}
    arr = d.get("tasks", [])

    with session_scope() as s:
        s.query(Task).delete()

        for i, t in enumerate(arr):
            obj = Task(
                name=t.get("name", "משימה"),
                duration=int(t.get("duration", 0)),
                remaining=int(t.get("remaining", t.get("duration", 0))),
                status=t.get("status", "pending"),
                position=i,
                is_work=bool(t.get("is_work", False)),
                end_time=None
            )
            s.add(obj)

    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)