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

# הערה: אם אתה עובד עם משתנה סביבה של Render – תוכל להחליף לשורה הבאה:
# DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///tasks.db")
# ולהשאיר את תיקון הפרפקס של postgres -> postgresql במידת הצורך.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///tasks.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

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
    duration = Column(Integer, nullable=False)      # משך שהוגדר (שניות)
    remaining = Column(Integer, nullable=False)     # כמה נשאר (שניות)
    status = Column(String, nullable=False)         # pending|running|paused|done
    end_time = Column(DateTime(timezone=True))      # מתוזמן לסיום כש-running
    position = Column(Integer, nullable=False, default=0)  # סדר מפורש
    is_work = Column(Boolean, nullable=False, default=False)  # משימה של עבודה?

    def to_dict(self):
        rem = self.remaining
        now_ts = now()
        if self.status == "running" and self.end_time:
            # הגדרה אחידה לאזור זמן
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
            "position": int(self.position),
            "is_work": bool(self.is_work),
        }

Base.metadata.create_all(engine)

# ודא שקיימת עמודת position גם אם נוצרה טבלה ישנה
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 0"))
        conn.commit()
    except Exception:
        pass
# ודא שקיימת is_work אם צריך
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE tasks ADD COLUMN is_work BOOLEAN DEFAULT FALSE"))
        conn.commit()
    except Exception:
        pass

# ====================================================
# אפליקציית Flask
# ====================================================
app = Flask(__name__)

# ====================================================
# פונקציות עזר
# ====================================================
def recompute_chain():
    """סוגר רצות שנגמרו, מעדכן remaining, ומפעיל אוטומטית את הבאה בתור לפי position."""
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        now_ts = now()
        for i, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                if t.end_time.tzinfo is None:
                    t.end_time = TZ.localize(t.end_time)
                rem = int((t.end_time - now_ts).total_seconds())
                if rem <= 0:
                    # סיימה
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    s.add(t)
                    # מפעיל אוטומטית את הבאה
                    if i + 1 < len(tasks):
                        nxt = tasks[i + 1]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = now_ts + timedelta(seconds=int(nxt.remaining))
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
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        payload = [t.to_dict() for t in tasks]
    # שעת סיום כוללת (מחשבים לפי remaining ברגע הקריאה)
    total_left = sum(t["remaining"] for t in payload if t["status"] in ("running", "paused", "pending"))
    overall_end = now() + timedelta(seconds=total_left) if total_left > 0 else None
    overall_end_str = overall_end.astimezone(TZ).strftime("%H:%M:%S %d.%m.%Y") if overall_end else "-"

    return jsonify({
        "ok": True,
        "tasks": payload,
        "overall_end_time": overall_end_str,
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
    dur = max(0, h * 3600 + m * 60 + ssec)
    with session_scope() as s:
        pos = s.query(Task).count()
        s.add(Task(name=name, duration=dur, remaining=dur, status="pending", position=pos))
    return jsonify({"ok": True})

@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    with session_scope() as s:
        # מתחילים רק אם אין כבר רצה
        is_running = s.query(Task).filter(Task.status == "running").first()
        t = s.get(Task, tid)
        if t and (not is_running or t.status == "running"):
            t.status = "running"
            t.end_time = now() + timedelta(seconds=int(t.remaining))
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
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.remaining = int(t.duration)
            t.status = "running"
            t.end_time = now() + timedelta(seconds=int(t.remaining))
            s.add(t)
    return jsonify({"ok": True})

@app.route("/delete/<int:tid>", methods=["POST"])
def delete(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            s.delete(t)
            # נורמליזציה של positions
            tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
            for idx, x in enumerate(tasks):
                if x.position != idx:
                    x.position = idx
                    s.add(x)
    return jsonify({"ok": True})

@app.route("/update/<int:tid>", methods=["POST"])
def update(tid):
    """עריכת שם/זמן כאשר המשימה לא רצה (pending/paused/done)."""
    data = request.json or {}
    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404
        if t.status not in ("pending", "paused", "done"):
            return jsonify({"ok": False, "error": "cannot edit running task"}), 400

        if "name" in data:
            nm = (data.get("name") or "").strip()
            if nm:
                t.name = nm

        if any(k in data for k in ("hours", "minutes", "seconds", "duration")):
            h = int(data.get("hours") or 0)
            m = int(data.get("minutes") or 0)
            ssec = int(data.get("seconds") or 0)
            duration = int(data.get("duration") or (h * 3600 + m * 60 + ssec))
            duration = max(0, duration)
            t.duration = duration
            t.remaining = duration
            t.end_time = None
            if t.status == "done":
                t.status = "pending"

        s.add(t)
    return jsonify({"ok": True})

@app.route("/extend/<int:tid>", methods=["POST"])
def extend(tid):
    """הארכת משימה בזמן חופשי (שעות/דקות/שניות)."""
    data = request.json or {}
    extra = int(data.get("hours", 0)) * 3600 + int(data.get("minutes", 0)) * 60 + int(data.get("seconds", 0))
    if extra <= 0:
        return jsonify({"ok": False, "error": "extra must be > 0"}), 400

    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404

        t.duration = int(t.duration) + extra
        if t.status == "running" and t.end_time:
            rem = max(0, int((t.end_time - now()).total_seconds()))
            t.remaining = rem + extra
            t.end_time = t.end_time + timedelta(seconds=extra)
        else:
            t.remaining = int(t.remaining) + extra

        s.add(t)
    return jsonify({"ok": True})

@app.route("/skip/<int:tid>", methods=["POST"])
def skip(tid):
    """דלג לבאה: מסמן רצה כ-done ומפעיל את הבאה בתור לפי position."""
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        ids = [t.id for t in tasks]
        t = s.get(Task, tid)
        if t and t.status == "running":
            t.status = "done"
            t.remaining = 0
            t.end_time = None
            s.add(t)
            if tid in ids:
                idx = ids.index(tid)
                if idx + 1 < len(tasks):
                    nxt = tasks[idx + 1]
                    if nxt.status == "pending":
                        nxt.status = "running"
                        nxt.end_time = now() + timedelta(seconds=int(nxt.remaining))
                        s.add(nxt)
    return jsonify({"ok": True})

@app.route("/set_pending/<int:tid>", methods=["POST"])
def set_pending(tid):
    """הפיכת משימה ל-pending (כולל DONE חוזר לפנדינג עם remaining=duration)."""
    with session_scope() as s:
        t = s.get(Task, tid)
        if t and t.status in ("paused", "done", "pending"):
            if t.status == "done":
                t.remaining = int(t.duration)
            t.status = "pending"
            t.end_time = None
            s.add(t)
    return jsonify({"ok": True})

@app.route("/reorder_single", methods=["POST"])
def reorder_single():
    """
    מקבל: task_id, new_position (1-based).
    מזיז משימה אחת למיקום החדש ומעדכן את כל ה-position בהתאם.
    """
    data = request.json or {}
    task_id = data.get("task_id")
    try:
        new_position = int(data.get("new_position", 0))
    except Exception:
        return jsonify({"ok": False, "error": "invalid new_position"}), 400

    if not task_id:
        return jsonify({"ok": False, "error": "no task_id provided"}), 400

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        ids = [t.id for t in tasks]

        if task_id not in ids:
            return jsonify({"ok": False, "error": "task not found"}), 404

        old_index = ids.index(task_id)
        new_index = max(0, min(new_position - 1, len(ids) - 1))

        if new_index == old_index:
            return jsonify({"ok": True})

        ids.insert(new_index, ids.pop(old_index))

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
            s.add(Task(
                name=t.get("name", "משימה"),
                duration=int(t.get("duration", 0)),
                remaining=int(t.get("remaining", t.get("duration", 0))),
                status=t.get("status", "pending"),
                position=i,
                is_work=bool(t.get("is_work", False)),
                end_time=None
            ))
    return jsonify({"ok": True})

# ============== סימולציה ==============
@app.route("/simulate", methods=["POST"])
def simulate():
    """
    קלט: {"start_time": "08:00"} או {"start_iso": "2025-11-07T08:00:00"}
    מחשב זמני התחלה/סיום צפויים לכל משימה (לפי remaining הנוכחי),
    בסדר position, ומחזיר layout בלי לשנות את המסד.
    """
    data = request.json or {}
    start_dt = None

    if "start_iso" in data and data["start_iso"]:
        try:
            # אם קיבלת זמן ISO – נניח שהוא מקומי ל-Asia/Jerusalem אם ללא tz
            st = datetime.fromisoformat(data["start_iso"])
            start_dt = st if st.tzinfo else TZ.localize(st)
        except Exception:
            start_dt = None

    if start_dt is None and "start_time" in data:
        # "HH:MM" של היום הנוכחי לפי TZ
        hhmm = str(data["start_time"]).strip()
        try:
            h, m = hhmm.split(":")
            base = now()
            start_dt = TZ.localize(datetime(base.year, base.month, base.day, int(h), int(m), 0))
        except Exception:
            start_dt = None

    if start_dt is None:
        start_dt = now()

    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()

    # נחשב לפי remaining (אם מישהו כבר התקדם – יחושב לפי מה שנשאר)
    layout = []
    cursor = start_dt
    for t in tasks:
        # נכלול רק משימות שלא DONE
        if t.status in ("pending", "paused", "running"):
            dur = max(0, int(t.remaining))
        else:
            dur = 0
        st = cursor
        en = cursor + timedelta(seconds=dur)
        layout.append({
            "id": t.id,
            "start": st.astimezone(TZ).strftime("%H:%M:%S %d.%m.%Y"),
            "end": en.astimezone(TZ).strftime("%H:%M:%S %d.%m.%Y"),
        })
        cursor = en

    return jsonify({
        "ok": True,
        "layout": layout,
        "overall_end": cursor.astimezone(TZ).strftime("%H:%M:%S %d.%m.%Y")
    })

# ====================================================
# ריצה מקומית
# ====================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)