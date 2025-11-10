import os, json
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
Session = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))
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
    sec = max(0, int(sec or 0))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
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
        rem = self.remaining
        if self.status == "running" and self.end_time:
            if self.end_time.tzinfo is None:
                self.end_time = TZ.localize(self.end_time)
            rem = max(0, int((self.end_time - now()).total_seconds()))

        return dict(
            id=self.id,
            name=self.name,
            duration=self.duration,
            remaining=rem,
            status=self.status,
            end_time_str=self.end_time.astimezone(TZ).strftime("%H:%M:%S") if self.end_time else "-",
            position=self.position,
            is_work=self.is_work,
        )

Base.metadata.create_all(engine)
app = Flask(__name__)

# ====================================================
# פונקציות עזר
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
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)
                    # הפעל משימה הבאה אם קיימת
                    if i + 1 < len(tasks):
                        nxt = tasks[i + 1]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = now_ts + timedelta(seconds=nxt.remaining)
                            s.add(nxt)
                else:
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
    return jsonify(
        ok=True,
        tasks=payload,
        work_total_seconds=work_total_seconds(),
        work_total_hhmmss=hhmmss(work_total_seconds()),
        now=now().strftime("%H:%M:%S %d.%m.%Y")
    )

@app.route("/add", methods=["POST"])
def add():
    d = request.json or {}
    n = (d.get("name") or "משימה חדשה").strip()
    h, m, ssec = map(int, [d.get("hours", 0), d.get("minutes", 0), d.get("seconds", 0)])
    dur = h * 3600 + m * 60 + ssec
    with session_scope() as sss:
        pos = sss.query(Task).count()
        sss.add(Task(name=n, duration=dur, remaining=dur, status="pending", position=pos))
    return jsonify(ok=True)

@app.route("/update/<int:tid>", methods=["POST"])
def update_task(tid):
    d = request.json or {}
    n = d.get("name", "").strip()
    h, m, ssec = map(int, [d.get("hours", 0), d.get("minutes", 0), d.get("seconds", 0)])
    dur = h * 3600 + m * 60 + ssec
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.name = n or t.name
            t.duration = dur
            t.remaining = dur
            s.add(t)
    return jsonify(ok=True)

@app.route("/extend/<int:tid>", methods=["POST"])
def extend_task(tid):
    d = request.json or {}
    add_sec = d.get("hours", 0)*3600 + d.get("minutes", 0)*60 + d.get("seconds", 0)
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.duration += add_sec
            t.remaining += add_sec
            if t.status == "running" and t.end_time:
                t.end_time += timedelta(seconds=add_sec)
            s.add(t)
    return jsonify(ok=True)

@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        for t in tasks:
            if t.status == "running" and t.id != tid:
                t.status = "paused"
                s.add(t)
        t = s.get(Task, tid)
        if t:
            t.status = "running"
            t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)
    return jsonify(ok=True)

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
    return jsonify(ok=True)

@app.route("/done/<int:tid>", methods=["POST"])
def done(tid):
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

@app.route("/set_pending/<int:tid>", methods=["POST"])
def set_pending(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "pending"
            t.end_time = None
            s.add(t)
    return jsonify(ok=True)

# ✅ חדש: הפוך את כל המשימות ל-Pending
@app.route("/set_all_pending", methods=["POST"])
def set_all_pending():
    with session_scope() as s:
        tasks = s.query(Task).all()
        for t in tasks:
            t.status = "pending"
            t.end_time = None
            s.add(t)
    return jsonify(ok=True)

@app.route("/delete/<int:tid>", methods=["POST"])
def delete(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            s.delete(t)
    return jsonify(ok=True)

@app.route("/workflag/<int:tid>", methods=["POST"])
def workflag(tid):
    d = request.json or {}
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.is_work = bool(d.get("is_work", False))
            s.add(t)
    return jsonify(ok=True)

@app.route("/reorder", methods=["POST"])
def reorder():
    data = request.json or {}
    order = data.get("order", [])
    if not order:
        return jsonify(ok=False, error="no order data")
    with session_scope() as s:
        for item in order:
            t = s.get(Task, item.get("id"))
            if t:
                t.position = int(item.get("position", 0))
                s.add(t)
    return jsonify(ok=True)

@app.route("/export")
def export():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc()).all()
        data = [t.to_dict() for t in tasks]
    js = json.dumps({"tasks": data}, ensure_ascii=False, indent=2)
    r = make_response(js)
    r.headers["Content-Type"] = "application/json; charset=utf-8"
    r.headers["Content-Disposition"] = "attachment; filename=tasks_export.json"
    return r

@app.route("/import", methods=["POST"])
def import_tasks():
    data = request.json or {}
    items = data.get("tasks", [])
    with session_scope() as s:
        s.query(Task).delete()
        for i, t in enumerate(items):
            s.add(
                Task(
                    name=t.get("name", "משימה"),
                    duration=int(t.get("duration", 0)),
                    remaining=int(t.get("remaining", 0)),
                    status=t.get("status", "pending"),
                    position=i,
                    is_work=bool(t.get("is_work", False)),
                )
            )
    return jsonify({"ok": True})

# ====================================================
# הרצת שרת Flask
# ====================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)