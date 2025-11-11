import os
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
import pytz

from flask import Flask, jsonify, render_template, request, make_response
from sqlalchemy import Column, DateTime, Integer, String, Boolean, create_engine, text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

# ====================================================
# הגדרות בסיסיות
# ====================================================
TZ = pytz.timezone("Asia/Jerusalem")

# ✅ שימוש ב-Postgres (נשמר גם כשהשרת נרדם)
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
    duration = Column(Integer, nullable=False)   # זמן מקורי בשניות
    remaining = Column(Integer, nullable=False)  # זמן נותר בשניות
    status = Column(String, nullable=False)      # pending|running|paused|done
    end_time = Column(DateTime(timezone=True))   # מתי המשימה מסתיימת בפועל (אם רצה)
    position = Column(Integer, nullable=False, default=0)
    is_work = Column(Boolean, nullable=False, default=False)

    def to_dict(self):
        rem = self.remaining
        now_ts = now()
        if self.status == "running" and self.end_time:
            # ביטחון שאובייקט עם tz
            if self.end_time.tzinfo is None:
                self.end_time = TZ.localize(self.end_time)
            rem = max(0, int((self.end_time - now_ts).total_seconds()))

        return {
            "id": self.id,
            "name": self.name,
            "duration": int(self.duration or 0),
            "remaining": int(rem),
            "remaining_hhmmss": hhmmss(rem),
            "status": self.status,
            "end_time_str": self.end_time.astimezone(TZ).strftime("%H:%M:%S") if self.end_time else "-",
            "position": self.position,
            "is_work": bool(self.is_work),
        }


Base.metadata.create_all(engine)

# הוספת עמודות חסרות — בטוח להרצה חוזרת
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN is_work BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.commit()
    except Exception:
        pass


# ====================================================
# Flask App
# ====================================================
app = Flask(__name__)


# ====================================================
# פונקציות עזר
# ====================================================
def recompute_chain():
    """
    מעדכן משימה רצה:
    - אם נגמר הזמן: מסמן כ-done ומפעיל את ה-Pending הבא.
    - אחרת: מעדכן remaining.
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
                    # המשימה הסתיימה
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)

                    # מחפש את ה-Pending הבא ומפעיל אותו
                    for j in range(idx + 1, len(tasks)):
                        nxt = tasks[j]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = now_ts + timedelta(seconds=int(nxt.remaining))
                            s.add(nxt)
                            break
                else:
                    # עדיין רצה – רק מעדכן remaining
                    t.remaining = rem
                    s.add(t)


def work_total_seconds() -> int:
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
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]

    total_work = work_total_seconds()
    return jsonify({
        "ok": True,
        "tasks": payload,
        "work_total_seconds": total_work,
        "work_total_hhmmss": hhmmss(total_work),
        "now": now().strftime("%H:%M:%S %d.%m.%Y")
    })


@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "משימה חדשה").strip()
    h = int(data.get("hours", 0) or 0)
    m = int(data.get("minutes", 0) or 0)
    ssec = int(data.get("seconds", 0) or 0)
    dur = max(0, h * 3600 + m * 60 + ssec)

    with session_scope() as s:
        pos = s.query(Task).count()
        s.add(Task(
            name=name,
            duration=dur,
            remaining=dur,
            status="pending",
            position=pos,
            is_work=False
        ))
    return jsonify({"ok": True})


@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    """
    Start:
    - מותר רק אם אין משימה רצה אחרת.
    - מותר ממצב pending/paused.
    - ממצב done – מתחיל מהתחלה (remaining=duration).
    """
    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404

        other_running = s.query(Task).filter(Task.status == "running", Task.id != tid).first()
        if other_running:
            return jsonify({"ok": False, "error": "another task is running"}), 400

        if t.status in ("pending", "paused"):
            # ממשיך מאיפה שנשאר
            t.status = "running"
            t.end_time = now() + timedelta(seconds=int(t.remaining))
            s.add(t)
        elif t.status == "done":
            # להתחיל מחדש
            t.remaining = int(t.duration or 0)
            t.status = "running"
            t.end_time = now() + timedelta(seconds=t.remaining)
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
            t.status = "paused"
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


@app.route("/reset/<int:tid>", methods=["POST"])
def reset(tid):
    """
    איפוס — מאפס רק את הזמן של המשימה:
    - remaining = duration
    - אם היא running – מחשב end_time מחדש
    - לא מפעיל משימות אחרות!
    """
    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404

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
    הופך משימה ל-Pending מכל מצב (חוץ מ-running – אבל גם אם הייתה running, פשוט נעצור).
    בלי להפעיל משימות אחרות.
    """
    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404

        # אם הייתה רצה – עוצרים ועוברים ל-pending
        if t.status == "running" and t.end_time:
            if t.end_time.tzinfo is None:
                t.end_time = TZ.localize(t.end_time)
            rem = int((t.end_time - now()).total_seconds())
            t.remaining = max(0, rem)

        t.status = "pending"
        t.end_time = None
        s.add(t)
    return jsonify({"ok": True})


@app.route("/skip/<int:tid>", methods=["POST"])
def skip(tid):
    """
    דילוג — מסמן המשימה כ-done ומפעיל את ה-Pending הבא בתור (אם יש).
    """
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        now_ts = now()

        for idx, t in enumerate(tasks):
            if t.id == tid:
                t.status = "done"
                t.remaining = 0
                t.end_time = None
                s.add(t)

                # מחפש הבא במצב pending
                for j in range(idx + 1, len(tasks)):
                    nxt = tasks[j]
                    if nxt.status == "pending":
                        nxt.status = "running"
                        nxt.end_time = now_ts + timedelta(seconds=int(nxt.remaining))
                        s.add(nxt)
                        break
                break

    return jsonify({"ok": True})


@app.route("/done/<int:tid>", methods=["POST"])
def mark_done(tid):
    """
    לחצן Done:
    - רק מסמן את המשימה כ-done
    - לא מפעיל משימה הבאה
    """
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "done"
            t.remaining = 0
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


@app.route("/update/<int:tid>", methods=["POST"])
def update_task(tid):
    """
    עדכון משימה: שם + זמן.
    מותר לערוך כל מצב חוץ מ-running.
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

        if any(k in data for k in ("hours", "minutes", "seconds", "duration")):
            h = int(data.get("hours", 0) or 0)
            m = int(data.get("minutes", 0) or 0)
            ssec = int(data.get("seconds", 0) or 0)
            duration = int(data.get("duration") or (h * 3600 + m * 60 + ssec))
            duration = max(0, duration)
            t.duration = duration
            t.remaining = duration
            t.end_time = None

        s.add(t)
    return jsonify({"ok": True})


@app.route("/extend/<int:tid>", methods=["POST"])
def extend(tid):
    """
    הארכת משימה (שעות/דקות/שניות).
    - מוסיף ל-duration.
    - אם המשימה רצה — מאריך גם את end_time.
    """
    data = request.json or {}
    extra = int(data.get("hours", 0) or 0) * 3600 \
        + int(data.get("minutes", 0) or 0) * 60 \
        + int(data.get("seconds", 0) or 0)

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


@app.route("/delete/<int:tid>", methods=["POST"])
def delete_task(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            s.delete(t)
            # מיישר מחדש position
            tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
            for idx, x in enumerate(tasks):
                if x.position != idx:
                    x.position = idx
                    s.add(x)
    return jsonify({"ok": True})


@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    """
    שינוי מיקום של משימה אחת: task_id + new_position (1-based).
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

        if new_idx == old_idx:
            return jsonify({"ok": True})

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
            dur = int(t.get("duration", 0) or 0)
            s.add(Task(
                name=t.get("name", "משימה"),
                duration=dur,
                remaining=dur,
                status=t.get("status", "pending"),
                position=i,
                is_work=bool(t.get("is_work", False))
            ))
    return jsonify({"ok": True})


# ====================================================
# ריצה מקומית
# ====================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)