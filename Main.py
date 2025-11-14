import os
from contextlib import contextmanager
from datetime import datetime, timedelta
import pytz
from flask import Flask, jsonify, render_template, request, make_response
from sqlalchemy import Column, DateTime, Integer, String, Boolean, create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker
import json

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
            et = self.end_time.astimezone(TZ)
            rem = max(0, int((et - now()).total_seconds()))

        return {
            "id": self.id,
            "name": self.name,
            "duration": self.duration,
            "remaining": rem,
            "status": self.status,
            "position": self.position,
            "is_work": self.is_work
        }

Base.metadata.create_all(engine)

app = Flask(__name__)

def recompute_chain():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position).all()
        tnow = now()

        for i, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                rem = int((t.end_time - tnow).total_seconds())
                if rem <= 0:
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)

                    for j in range(i+1, len(tasks)):
                        if tasks[j].status == "pending":
                            nxt = tasks[j]
                            nxt.status = "running"
                            nxt.end_time = tnow + timedelta(seconds=nxt.remaining)
                            s.add(nxt)
                            break
                else:
                    t.remaining = rem
                    s.add(t)

def work_total_seconds():
    with session_scope() as s:
        ts = s.query(Task).filter(Task.is_work == True).all()
        return sum(t.duration for t in ts)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/state")
def state():
    recompute_chain()
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position).all()
        data = [t.to_dict() for t in tasks]
    return jsonify({
        "ok": True,
        "tasks": data,
        "now": now().strftime("%H:%M:%S"),
        "work_total_hhmmss": hhmmss(work_total_seconds())
    })

@app.route("/add", methods=["POST"])
def add():
    d = request.json
    name = d.get("name", "משימה חדשה")
    h = int(d.get("hours", 0))
    m = int(d.get("minutes", 0))
    ssec = int(d.get("seconds", 0))
    dur = h*3600 + m*60 + ssec
    pos = int(d.get("position", 999999))

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position).all()

        if pos < 0: pos = 0
        if pos > len(tasks): pos = len(tasks)

        for i, t in enumerate(tasks):
            if i >= pos:
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
            t.end_time = None
            t.status = "paused"
            s.add(t)
    return jsonify({"ok": True})

@app.route("/reset/<int:tid>", methods=["POST"])
def reset(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.remaining = t.duration
            t.end_time = None
            t.status = "pending"
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
    return done(tid)

@app.route("/update/<int:tid>", methods=["POST"])
def update(tid):
    d = request.json
    nm = d.get("name", "")
    h = int(d.get("hours", 0))
    m = int(d.get("minutes", 0))
    ssec = int(d.get("seconds", 0))
    dur = h*3600 + m*60 + ssec

    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.name = nm
            diff = dur - t.duration
            t.duration = dur
            t.remaining += diff
            if t.remaining < 0: t.remaining = 0
            s.add(t)
    return jsonify({"ok": True})

@app.route("/extend/<int:tid>", methods=["POST"])
def extend(tid):
    d = request.json
    add = int(d.get("hours", 0))*3600 + int(d.get("minutes", 0))*60 + int(d.get("seconds", 0))
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.duration += add
            t.remaining += add
            s.add(t)
    return jsonify({"ok": True})

@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    d = request.json
    tid = int(d["task_id"])
    new_pos = int(d["new_position"])

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position).all()

        if new_pos < 1: new_pos=1
        if new_pos > len(tasks): new_pos = len(tasks)

        idx = [t.id for t in tasks].index(tid)
        item = tasks.pop(idx)
        tasks.insert(new_pos-1, item)

        for i, t in enumerate(tasks):
            t.position = i
            s.add(t)

    return jsonify({"ok": True})

@app.route("/workflag/<int:tid>", methods=["POST"])
def workflag(tid):
    d = request.json
    val = bool(d.get("is_work", False))
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.is_work = val
            s.add(t)
    return jsonify({"ok": True})

@app.route("/export")
def export():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position).all()
        raw = json.dumps({"tasks":[t.to_dict() for t in tasks]}, ensure_ascii=False, indent=2)
    resp = make_response(raw)
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Content-Disposition"] = "attachment; filename=tasks.json"
    return resp

@app.route("/import", methods=["POST"])
def import_json():
    d = request.json
    items = d.get("tasks", [])
    with session_scope() as s:
        s.query(Task).delete()
        for i, t in enumerate(items):
            s.add(Task(
                name=t["name"],
                duration=t["duration"],
                remaining=t["duration"],
                status=t.get("status","pending"),
                position=i,
                is_work=bool(t.get("is_work", False))
            ))
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run()