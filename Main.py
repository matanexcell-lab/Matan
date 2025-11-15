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

# ✅ מסד הנתונים שלך (Render PostgreSQL)
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
    duration = Column(Integer, nullable=False)       # משך מקורי בשניות
    remaining = Column(Integer, nullable=False)      # כמה נשאר בשניות
    status = Column(String, nullable=False)          # pending/running/paused/done
    end_time = Column(DateTime(timezone=True))       # מתי תסתיים (כשב־running)
    position = Column(Integer, nullable=False, default=0)
    is_work = Column(Boolean, nullable=False, default=False)

    def to_dict(self):
        rem = self.remaining
        now_ts = now()

        # חישוב remaining בזמן אמת אם המשימה רצה
        if self.status == "running" and self.end_time:
            if self.end_time.tzinfo is None:
                self.end_time = TZ.localize(self.end_time)
            rem = max(0, int((self.end_time - now_ts).total_seconds()))

        return {
            "id": self.id,
            "name": self.name,
            "duration": int(self.duration),
            "remaining": int(rem),
            "remaining_hhmmss": hhmmss(rem),
            "status": self.status,
            "end_time_str": self.end_time.astimezone(TZ).strftime("%H:%M:%S") if self.end_time else "-",
            "position": self.position,
            "is_work": self.is_work,
        }


Base.metadata.create_all(engine)

# ====================================================
# אפליקציית Flask
# ====================================================
app = Flask(__name__)


# ====================================================
# פונקציות עזר
# ====================================================
def recompute_chain():
    """
    מעדכן משימות שרצות לפי הזמן הנוכחי.
    אם משימה הסתיימה → מסמנת כ-done ומפעילה את ה-pending הבא.
    """
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
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

                    # מחפש את ה-pending הבא ומפעיל אותו
                    for j in range(i + 1, len(tasks)):
                        nxt = tasks[j]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = now_ts + timedelta(seconds=nxt.remaining)
                            s.add(nxt)
                            break
                else:
                    # עדיין רצה
                    t.remaining = rem
                    s.add(t)


def work_total_seconds():
    """
    סכום כל משך (duration) המשימות המסומנות כעבודה.
    """
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
    """
    מחזיר מצב מלא ל־JS:
    - כל המשימות
    - זמן עבודה כולל
    - השעה הנוכחית
    """
    recompute_chain()
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]

    ws = work_total_seconds()
    return jsonify({
        "ok": True,
        "tasks": payload,
        "work_total_seconds": ws,
        "work_total_hhmmss": hhmmss(ws),
        "now": now().strftime("%H:%M:%S %d.%m.%Y")
    })


@app.route("/add", methods=["POST"])
def add():
    """
    הוספת משימה חדשה. נתמך:
    - name
    - hours, minutes, seconds
    - optional: insert_position (1-based)
    """
    data = request.json or {}
    name = (data.get("name") or "משימה חדשה").strip()
    h = int(data.get("hours", 0))
    m = int(data.get("minutes", 0))
    ssec = int(data.get("seconds", 0))
    dur = max(0, h * 3600 + m * 60 + ssec)

    insert_pos = data.get("insert_position", None)
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()

        if insert_pos is None or insert_pos <= 0 or insert_pos > len(tasks) + 1:
            # מוסיף בסוף
            pos = len(tasks)
            s.add(Task(name=name, duration=dur, remaining=dur, status="pending", position=pos))
        else:
            # דוחף למיקום המבוקש (1-based → index 0-based)
            idx_new = insert_pos - 1
            # מזיז את כל המשימות ממיקום זה ואילך
            for t in tasks:
                if t.position >= idx_new:
                    t.position += 1
                    s.add(t)
            # יוצר משימה במקום המבוקש
            s.add(Task(name=name, duration=dur, remaining=dur,
                       status="pending", position=idx_new))
    return jsonify({"ok": True})


@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    """
    מפעיל משימה (אם אין אחרת רצה).
    """
    with session_scope() as s:
        # בודק אם יש כבר רצה
        running_exists = s.query(Task).filter(Task.status == "running").first()
        if running_exists:
            # לא נפעיל יותר מאחת
            return jsonify({"ok": False, "error": "already running"}), 400

        t = s.get(Task, tid)
        if t and t.status in ("pending", "paused"):
            t.status = "running"
            t.end_time = now() + timedelta(seconds=t.remaining)
            s.add(t)

    return jsonify({"ok": True})


@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    """
    השהיית המשימה: מחשב remaining מחדש ועוצר end_time.
    """
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
    - מחזיר remaining = duration
    - לא מפעיל שום משימה אחרת
    - אם הייתה running – הופך ל-paused (או pending, אתה יכול לשנות לטעמך)
    """
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.remaining = t.duration
            # אם היא הייתה רצה – נעצור אותה למצב pending
            if t.status == "running":
                t.status = "pending"
                t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


@app.route("/set_pending/<int:tid>", methods=["POST"])
def set_pending(tid):
    """
    משנה סטטוס ל-pending (גם מ-paused או מ-done).
    לא מפעיל אוטומטית.
    """
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "pending"
            t.end_time = None
            # לא משנים remaining
            s.add(t)
    return jsonify({"ok": True})


@app.route("/skip/<int:tid>", methods=["POST"])
def skip(tid):
    """
    מדלג על משימה:
    - מסמן כ-done
    - remaining = 0
    - מפעיל המשימה הבאה שיש לה status = pending
    """
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        now_ts = now()

        # מסמן המשימה כ-done
        idx = None
        for i, t in enumerate(tasks):
            if t.id == tid:
                t.status = "done"
                t.remaining = 0
                t.end_time = None
                s.add(t)
                idx = i
                break

        if idx is not None:
            # מפעיל הבא עם pending אחרי המשימה הזו
            for j in range(idx + 1, len(tasks)):
                nxt = tasks[j]
                if nxt.status == "pending":
                    nxt.status = "running"
                    nxt.end_time = now_ts + timedelta(seconds=nxt.remaining)
                    s.add(nxt)
                    break

    return jsonify({"ok": True})


@app.route("/done/<int:tid>", methods=["POST"])
def mark_done(tid):
    """
    מסמן משימה כ-done בלבד.
    לא מפעיל שום משימה אחרת.
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
    עדכון שם + זמן (duration).
    אפשרי רק אם המשימה לא running (pending/paused/done).
    """
    data = request.json or {}
    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404

        if t.status == "running":
            return jsonify({"ok": False, "error": "cannot edit running task"}), 400

        name = data.get("name", None)
        if name is not None:
            name = name.strip()
            if name:
                t.name = name

        if any(k in data for k in ("hours", "minutes", "seconds")):
            h = int(data.get("hours", 0))
            m = int(data.get("minutes", 0))
            ssec = int(data.get("seconds", 0))
            dur = max(0, h * 3600 + m * 60 + ssec)
            t.duration = dur
            t.remaining = dur
            t.end_time = None

        s.add(t)

    return jsonify({"ok": True})


@app.route("/extend/<int:tid>", methods=["POST"])
def extend_task(tid):
    """
    הארכת משימה:
    - מוסיף לזמן duration ו-remaining
    - אם running – גם ל-end_time
    """
    data = request.json or {}
    extra = int(data.get("hours", 0)) * 3600 + int(data.get("minutes", 0)) * 60 + int(data.get("seconds", 0))
    if extra <= 0:
        return jsonify({"ok": False, "error": "extra must be > 0"}), 400

    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404

        t.duration += extra
        if t.status == "running" and t.end_time:
            if t.end_time.tzinfo is None:
                t.end_time = TZ.localize(t.end_time)
            rem = max(0, int((t.end_time - now()).total_seconds()))
            t.remaining = rem + extra
            t.end_time += timedelta(seconds=extra)
        else:
            t.remaining += extra
        s.add(t)

    return jsonify({"ok": True})


@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    """
    העברת משימה למיקום חדש.
    קלט JSON: { "task_id": ..., "new_position": ... }  (1-based)
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
        # new_pos הוא 1-based, הופכים ל-0-based
        new_idx = max(0, min(new_pos - 1, len(ids) - 1))

        # מסדרים מחדש
        ids.insert(new_idx, ids.pop(old_idx))

        for idx, tid in enumerate(ids):
            obj = s.get(Task, tid)
            if obj and obj.position != idx:
                obj.position = idx
                s.add(obj)

    return jsonify({"ok": True})


@app.route("/workflag/<int:tid>", methods=["POST"])
def workflag(tid):
    """
    עדכון is_work עבור משימה.
    """
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
    """
    ייצוא כל המשימות לקובץ JSON (כולל is_work).
    """
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
    ייבוא קובץ JSON בפורמט {"tasks":[...]}.
    מוחק את כל המשימות הקיימות ובונה מחדש.
    """
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
                is_work=bool(t.get("is_work", False))
            ))
    return jsonify({"ok": True})


@app.route("/set_all_pending", methods=["POST"])
def set_all_pending():
    """
    הופך את כל המשימות ל-pending (reset כולל סטטוסים).
    לא משנה משך.
    """
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        for t in tasks:
            t.status = "pending"
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)