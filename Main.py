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
Session = scoped_session(
    sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
)
Base = declarative_base()

ACTIVE = ("running", "paused", "pending")


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


def aware(dt):
    if dt is None:
        return None
    return TZ.localize(dt) if dt.tzinfo is None else dt


def hhmmss(total_seconds):
    total_seconds = max(0, int(total_seconds or 0))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def to_ms(dt):
    return int(dt.timestamp() * 1000) if dt else None


# ====================================================
# מודלים
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

    def live_remaining(self, now_ts=None):
        if self.status == "running" and self.end_time:
            now_ts = now_ts or now()
            return max(0, int((aware(self.end_time) - now_ts).total_seconds()))
        return int(self.remaining or 0)

    def to_dict(self):
        rem = self.live_remaining()
        return {
            "id": self.id,
            "name": self.name,
            "duration": int(self.duration),
            "remaining": int(rem),
            "remaining_hhmmss": hhmmss(rem),
            "status": self.status,
            "end_time_str": aware(self.end_time).astimezone(TZ).strftime("%H:%M:%S") if self.end_time else "-",
            "position": self.position,
            "is_work": self.is_work,
        }


# 🆕 טבלת הגדרות קטנה (key/value) – שומרת את שעת הסיום המתוכננת
class Meta(Base):
    __tablename__ = "meta"
    key = Column(String, primary_key=True)
    value = Column(String)


Base.metadata.create_all(engine)


def get_meta_dt(s, key):
    m = s.get(Meta, key)
    if not m or not m.value:
        return None
    return aware(datetime.fromisoformat(m.value))


def set_meta_dt(s, key, dt):
    m = s.get(Meta, key)
    if dt is None:
        if m:
            s.delete(m)
        return
    if m:
        m.value = dt.isoformat()
    else:
        s.add(Meta(key=key, value=dt.isoformat()))


def clear_plan(s):
    set_meta_dt(s, "planned_end", None)
    set_meta_dt(s, "last_done_at", None)


def total_active_remaining(tasks, now_ts):
    return sum(t.live_remaining(now_ts) for t in tasks if t.status in ACTIVE)


# ====================================================
# אפליקציית Flask
# ====================================================
app = Flask(__name__)


def recompute_chain():
    """
    מעדכן משימות שרצות. משימה שהסתיימה → done, והבאה מתחילה
    בדיוק מרגע הסיום (גם אם האתר היה סגור באותו זמן).
    """
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        now_ts = now()

        for i, t in enumerate(tasks):
            if t.status == "running" and t.end_time:
                end = aware(t.end_time)
                if end <= now_ts:
                    t.status = "done"
                    t.remaining = 0
                    t.end_time = None
                    set_meta_dt(s, "last_done_at", end)

                    for j in range(i + 1, len(tasks)):
                        nxt = tasks[j]
                        if nxt.status == "pending":
                            nxt.status = "running"
                            nxt.end_time = end + timedelta(seconds=nxt.remaining)
                            break
                else:
                    t.remaining = int((end - now_ts).total_seconds())


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
        now_ts = now()

        planned = get_meta_dt(s, "planned_end")
        any_running = any(t.status == "running" for t in tasks)
        any_active = any(t.status in ACTIVE for t in tasks)

        if any_active:
            projected = now_ts + timedelta(seconds=total_active_remaining(tasks, now_ts))
            # כשאין משימה רצה (השהיה) – שעת הסיום הצפויה זזה עם השעון
            projected_live = not any_running
        else:
            projected = get_meta_dt(s, "last_done_at")
            projected_live = False

    ws = work_total_seconds()
    return jsonify({
        "ok": True,
        "tasks": payload,
        "work_total_seconds": ws,
        "work_total_hhmmss": hhmmss(ws),
        "now": now_ts.strftime("%H:%M:%S %d.%m.%Y"),
        "server_now_ms": to_ms(now_ts),
        "planned_end_ms": to_ms(planned),
        "projected_end_ms": to_ms(projected),
        "projected_is_live": projected_live,
    })


@app.route("/add", methods=["POST"])
def add():
    data = request.json or {}
    name = (data.get("name") or "משימה חדשה").strip()
    h = int(data.get("hours", 0))
    m = int(data.get("minutes", 0))
    ssec = int(data.get("seconds", 0))
    dur = max(0, h * 3600 + m * 60 + ssec)

    insert_pos = data.get("insert_position", None)
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()

        if insert_pos is None:
            insert_pos = len(tasks) + 1
        try:
            insert_pos = int(insert_pos)
        except Exception:
            insert_pos = len(tasks) + 1

        if insert_pos <= 0 or insert_pos > len(tasks) + 1:
            pos = len(tasks)
            s.add(Task(name=name, duration=dur, remaining=dur, status="pending", position=pos))
        else:
            idx_new = insert_pos - 1
            for t in tasks:
                if t.position >= idx_new:
                    t.position += 1
            s.add(Task(name=name, duration=dur, remaining=dur,
                       status="pending", position=idx_new))

        # 🆕 משימה חדשה = תכנון חדש, לא חריגה → דוחה את היעד
        planned = get_meta_dt(s, "planned_end")
        if planned:
            set_meta_dt(s, "planned_end", planned + timedelta(seconds=dur))
    return jsonify({"ok": True})


@app.route("/start/<int:tid>", methods=["POST"])
def start(tid):
    with session_scope() as s:
        running_exists = s.query(Task).filter(Task.status == "running").first()
        if running_exists:
            return jsonify({"ok": False, "error": "already running"}), 400

        t = s.get(Task, tid)
        if t and t.status in ("pending", "paused"):
            now_ts = now()
            t.status = "running"
            t.end_time = now_ts + timedelta(seconds=t.remaining)

            # 🆕 הלחיצה הראשונה על "התחל" קובעת את שעת הסיום המתוכננת
            if get_meta_dt(s, "planned_end") is None:
                tasks = s.query(Task).all()
                total = total_active_remaining(tasks, now_ts)
                set_meta_dt(s, "planned_end", now_ts + timedelta(seconds=total))

    return jsonify({"ok": True})


@app.route("/pause/<int:tid>", methods=["POST"])
def pause(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t and t.status == "running" and t.end_time:
            t.remaining = t.live_remaining()
            t.end_time = None
            t.status = "paused"
    return jsonify({"ok": True})


@app.route("/reset/<int:tid>", methods=["POST"])
def reset(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.remaining = t.duration
            if t.status == "running":
                t.status = "pending"
                t.end_time = None
    return jsonify({"ok": True})


@app.route("/set_pending/<int:tid>", methods=["POST"])
def set_pending(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            if t.status == "running":
                t.remaining = t.live_remaining()
            t.status = "pending"
            t.end_time = None
    return jsonify({"ok": True})


@app.route("/skip/<int:tid>", methods=["POST"])
def skip(tid):
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        now_ts = now()

        idx = None
        for i, t in enumerate(tasks):
            if t.id == tid:
                t.status = "done"
                t.remaining = 0
                t.end_time = None
                idx = i
                set_meta_dt(s, "last_done_at", now_ts)
                break

        if idx is not None:
            for j in range(idx + 1, len(tasks)):
                nxt = tasks[j]
                if nxt.status == "pending":
                    nxt.status = "running"
                    nxt.end_time = now_ts + timedelta(seconds=nxt.remaining)
                    break

    return jsonify({"ok": True})


@app.route("/done/<int:tid>", methods=["POST"])
def mark_done(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.status = "done"
            t.remaining = 0
            t.end_time = None
            set_meta_dt(s, "last_done_at", now())
    return jsonify({"ok": True})


@app.route("/update/<int:tid>", methods=["POST"])
def update_task(tid):
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

    return jsonify({"ok": True})


@app.route("/extend/<int:tid>", methods=["POST"])
def extend_task(tid):
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
            t.remaining = t.live_remaining() + extra
            t.end_time = aware(t.end_time) + timedelta(seconds=extra)
        else:
            t.remaining += extra
        # הארכה לא משנה את היעד → נספרת כחריגה

    return jsonify({"ok": True})


@app.route("/reorder_single", methods=["POST"])
def reorder_single():
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
            obj = s.get(Task, tid)
            if obj and obj.position != idx:
                obj.position = idx

    return jsonify({"ok": True})


@app.route("/workflag/<int:tid>", methods=["POST"])
def workflag(tid):
    data = request.json or {}
    val = bool(data.get("is_work", False))
    with session_scope() as s:
        t = s.get(Task, tid)
        if t:
            t.is_work = val
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
        clear_plan(s)
        for i, t in enumerate(items):
            duration = int(t.get("duration", 0))
            status = t.get("status", "pending")
            if status == "running":
                status = "pending"
            s.add(Task(
                name=t.get("name", "משימה"),
                duration=duration,
                remaining=duration,
                status=status,
                position=i,
                is_work=bool(t.get("is_work", False))
            ))
    return jsonify({"ok": True})


@app.route("/set_all_pending", methods=["POST"])
def set_all_pending():
    with session_scope() as s:
        tasks = s.query(Task).order_by(Task.position.asc(), Task.id.asc()).all()
        for t in tasks:
            t.status = "pending"
            t.end_time = None
        clear_plan(s)  # 🆕 סשן חדש → יעד חדש בלחיצה הבאה על "התחל"
    return jsonify({"ok": True})


# 🆕 קביעת יעד מחדש
@app.route("/reset_plan", methods=["POST"])
def reset_plan():
    """
    אם משימה רצה – היעד החדש = שעת הסיום הצפויה עכשיו.
    אחרת – היעד יימחק וייקבע בלחיצה הבאה על "התחל".
    """
    with session_scope() as s:
        tasks = s.query(Task).all()
        clear_plan(s)
        if any(t.status == "running" for t in tasks):
            now_ts = now()
            total = total_active_remaining(tasks, now_ts)
            set_meta_dt(s, "planned_end", now_ts + timedelta(seconds=total))
    return jsonify({"ok": True})


@app.route("/delete/<int:tid>", methods=["POST"])
def delete_task(tid):
    with session_scope() as s:
        t = s.get(Task, tid)
        if not t:
            return jsonify({"ok": False, "error": "not found"}), 404

        # 🆕 מחיקת משימה שעוד לא בוצעה מקדימה את היעד (לא נחשב "הקדמה")
        planned = get_meta_dt(s, "planned_end")
        if planned and t.status in ACTIVE:
            set_meta_dt(s, "planned_end", planned - timedelta(seconds=t.live_remaining()))

        old_pos = t.position
        s.delete(t)

        others = (
            s.query(Task)
            .filter(Task.position > old_pos)
            .order_by(Task.position.asc())
            .all()
        )
        for o in others:
            o.position -= 1

    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
