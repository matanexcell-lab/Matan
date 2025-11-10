import os
import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytz
from flask import Flask, jsonify, render_template, request, make_response
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Boolean,
    create_engine,
    text,
)
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
Session = scoped_session(
    sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
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


def now():
    return datetime.now(TZ)


def hhmmss(total_seconds: int) -> str:
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
    duration = Column(Integer, nullable=False)  # משך מקורי בשניות
    remaining = Column(Integer, nullable=False)  # שניות שנותרו
    status = Column(String, nullable=False)  # pending / running / paused / done
    end_time = Column(DateTime(timezone=True))
    position = Column(Integer, nullable=False, default=0)
    is_work = Column(Boolean, nullable=False, default=False)

    def to_dict(self):
        rem = self.remaining
        now_ts = now()

        if self.status == "running" and self.end_time:
            # לוודא timezone-aware
            if self.end_time.tzinfo is None:
                self.end_time = TZ.localize(self.end_time)
            rem = max(0, int((self.end_time - now_ts).total_seconds()))

        return {
            "id": self.id,
            "name": self.name,
            "duration": int(self.duration or 0),
            "remaining": int(rem or 0),
            "remaining_hhmmss": hhmmss(rem),
            "status": self.status,
            "end_time_str": (
                self.end_time.astimezone(TZ).strftime("%H:%M:%S")
                if self.end_time
                else "-"
            ),
            "position": int(self.position or 0),
            "is_work": bool(self.is_work),
        }


# יצירת טבלה במידה ולא קיימת
Base.metadata.create_all(engine)

# הוספת עמודות אם חסרות (לא מזיק אם קיימות)
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN is_work BOOLEAN DEFAULT FALSE"))
        conn.commit()
    except Exception:
        pass

# ==============================
# Flask App
# ==============================
app = Flask(__name__)


# ==============================
# פונקציות עזר לשרשרת
# ==============================
def recompute_chain():
    """
    מעדכן משימה רצה:
    - אם נגמר הזמן -> done ומפעיל הבאה בתור (pending) לפי position.
    - אם עדיין רצה -> מעדכן remaining מהשרת.
    """
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        now_ts = now()
        for idx, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                if t.end_time.tzinfo is None:
                    t.end_time = TZ.localize(t.end_time)
                rem = int((t.end_time - now_ts).total_seconds())
                if rem <= 0:
                    # המשימה הזו הסתיימה
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)
                    # הפעל הבאה בתור
                    if idx + 1 < len(tasks):
                        nxt = tasks[idx + 1]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = now_ts + timedelta(
                                seconds=int(nxt.remaining or 0)
                            )
                            s.add(nxt)
                else:
                    # עדיין רצה – מעדכן remaining
                    t.remaining = rem
                    s.add(t)


def work_total_seconds():
    with session_scope() as s:
        items = s.query(Task).filter(Task.is_work == True).all()  # noqa: E712
        return sum(int(x.duration or 0) for x in items)


# ==============================
# ROUTES
# ==============================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/state")
def state():
    # קודם מחשב שרשרת
    recompute_chain()
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]

    wsec = work_total_seconds()
    return jsonify(
        {
            "ok": True,
            "tasks": payload,
            "work_total_seconds": int(wsec),
            "work_total_hhmmss": hhmmss(wsec),
            "now": now().strftime("%H:%M:%S %d.%m.%Y"),
        }
    )


@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "משימה חדשה").strip()
    h = int(data.get("hours") or 0)
    m = int(data.get("minutes") or 0)
    ssec = int(data.get("seconds") or 0)
    dur = max(0, h * 3600 + m * 60 + ssec)

    with session_scope() as s:
        pos = s.query(Task).count()
        t = Task(
            name=name,
            duration=dur,
            remaining=dur,
            status="pending",
            position=pos,
            is_work=False,
        )
        s.add(t)

    return jsonify({"ok": True})


@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    with session_scope() as s:
        # לא להריץ אם כבר יש משימה רצה?
        running = s.query(Task).filter(Task.status == "running").first()
        t = s.get(Task, tid)
        if t and not running:
            t.status = "running"
            t.end_time = now() + timedelta(seconds=int(t.remaining or 0))
            s.add(t)
    return jsonify({"ok": True})


@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t and t.status == "running" and t.end_time:
            if t.end_time.tzinfo is None:
                t.end_time = TZ.localize(t.end_time)
            rem = int((t.end_time - now()).total_seconds())
            t.remaining = max(0, rem)
            t.end_time = None
            t.status = "paused"
            s.add(t)
    return jsonify({"ok": True})


@app.route("/reset/<int:tid>", methods=["POST"])
def reset(tid):
    """
    איפוס משימה:
    - מאפס remaining ל-duration.
    - אם המשימה רצה – מעדכן גם end_time.
    - לא משנה סטטוס (מלבד הקייס של running).
    """
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.remaining = int(t.duration or 0)
            if t.status == "running":
                t.end_time = now() + timedelta(seconds=t.remaining)
            else:
                t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


@app.route("/set_pending/<int:tid>", methods=["POST"])
def set_pending(tid):
    """
    הפיכת משימה לכלי עבודה:
    - אם היא running – עוצרים, מעדכנים remaining, סטטוס = pending.
    - אם paused/done/pending – סטטוס = pending (done מאפס remaining=duration).
    """
    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False}), 404

        if t.status == "running" and t.end_time:
            if t.end_time.tzinfo is None:
                t.end_time = TZ.localize(t.end_time)
            rem = int((t.end_time - now()).total_seconds())
            t.remaining = max(0, rem)
            t.end_time = None
            t.status = "pending"
        elif t.status in ("paused", "pending", "done"):
            if t.status == "done":
                # מחזיר remaining למשך מלא
                t.remaining = int(t.duration or 0)
            t.status = "pending"
            t.end_time = None

        s.add(t)
    return jsonify({"ok": True})


@app.route("/skip/<int:tid>", methods=["POST"])
def skip(tid):
    """
    דלג:
    - אם המשימה רצה – מסמן אותה כ-done ומפעיל את הבאה בתור ב-pending.
    - אם לא רצה – פשוט מסמן done.
    """
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        ids = [t.id for t in tasks]
        now_ts = now()

        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False}), 404

        was_running = t.status == "running"
        t.status = "done"
        t.remaining = 0
        t.end_time = None
        s.add(t)

        if was_running and tid in ids:
            idx = ids.index(tid)
            if idx + 1 < len(tasks):
                nxt = tasks[idx + 1]
                if nxt.status == "pending":
                    nxt.status = "running"
                    nxt.end_time = now_ts + timedelta(seconds=int(nxt.remaining or 0))
                    s.add(nxt)

    return jsonify({"ok": True})


@app.route("/done/<int:tid>", methods=["POST"])
def mark_done(tid):
    """
    לחצן ✅ Done:
    - מסמן משימה כ-done (מכל סטטוס).
    - מפעיל את הבאה בתור במידה והיא pending.
    """
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        ids = [t.id for t in tasks]
        now_ts = now()

        if tid not in ids:
            return jsonify({"ok": False}), 404

        idx = ids.index(tid)
        t = tasks[idx]
        t.status = "done"
        t.remaining = 0
        t.end_time = None
        s.add(t)

        # מפעיל את הבאה בתור אם Pending
        if idx + 1 < len(tasks):
            nxt = tasks[idx + 1]
            if nxt.status == "pending":
                nxt.status = "running"
                nxt.end_time = now_ts + timedelta(seconds=int(nxt.remaining or 0))
                s.add(nxt)

    return jsonify({"ok": True})


@app.route("/delete/<int:tid>", methods=["POST"])
def delete(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            s.delete(t)
            # מסדר שוב positions
            tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
            for idx, item in enumerate(tasks):
                if item.position != idx:
                    item.position = idx
                    s.add(item)
    return jsonify({"ok": True})


@app.route("/update/<int:tid>", methods=["POST"])
def update(tid):
    """
    עדכון שם/זמן משימה (כשלא רצה).
    """
    data = request.json or {}
    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404

        if t.status == "running":
            return jsonify({"ok": False, "error": "cannot edit running task"}), 400

        if "name" in data:
            nm = (data.get("name") or "").strip()
            if nm:
                t.name = nm

        if any(k in data for k in ("hours", "minutes", "seconds")):
            h = int(data.get("hours") or 0)
            m = int(data.get("minutes") or 0)
            ssec = int(data.get("seconds") or 0)
            dur = max(0, h * 3600 + m * 60 + ssec)
            t.duration = dur
            t.remaining = dur
            t.end_time = None
            if t.status == "done":
                t.status = "pending"

        s.add(t)
    return jsonify({"ok": True})


@app.route("/extend/<int:tid>", methods=["POST"])
def extend(tid):
    """
    הארכת משימה:
    - מוסיף זמן ל-duration ול-remaining.
    - אם המשימה רצה – מעדכן גם end_time.
    """
    data = request.json or {}
    extra = (
        int(data.get("hours", 0)) * 3600
        + int(data.get("minutes", 0)) * 60
        + int(data.get("seconds", 0))
    )
    if extra <= 0:
        return jsonify({"ok": False, "error": "extra must be > 0"}), 400

    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404

        t.duration = int(t.duration or 0) + extra

        if t.status == "running" and t.end_time:
            if t.end_time.tzinfo is None:
                t.end_time = TZ.localize(t.end_time)
            rem = max(0, int((t.end_time - now()).total_seconds()))
            t.remaining = rem + extra
            t.end_time = t.end_time + timedelta(seconds=extra)
        else:
            t.remaining = int(t.remaining or 0) + extra

        s.add(t)

    return jsonify({"ok": True})


@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    """
    שינוי סדר משימה בודדת:
    קלט: task_id, new_position (1-based).
    """
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
        new_idx = max(0, min(new_pos - 1, len(ids) - 1))

        ids.insert(new_idx, ids.pop(old_idx))

        for idx, tid in enumerate(ids):
            t = s.get(Task, tid)
            if t and t.position != idx:
                t.position = idx
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


@app.route("/all_pending", methods=["POST"])
def all_pending():
    """
    הופך את כל המשימות ל-Pending (ומאפס end_time, לא נוגע ב-duration/remaining).
    """
    with session_scope() as s:
        tasks = s.query(Task).all()
        for t in tasks:
            if t.status == "done":
                t.remaining = int(t.duration or 0)
            t.status = "pending"
            t.end_time = None
            s.add(t)
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
            s.add(
                Task(
                    name=t.get("name", "משימה"),
                    duration=int(t.get("duration", 0)),
                    remaining=int(t.get("duration", 0)),
                    status=t.get("status", "pending"),
                    position=i,
                    is_work=bool(t.get("is_work", False)),
                )
            )
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)