from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone, timedelta
import os

app = Flask(__name__)

# --- DB config: SQLite by default; can use PostgreSQL via DATABASE_URL ---
db_url = os.getenv("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    # SQLAlchemy requires postgresql://
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url or "sqlite:///tasks.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)

    # seconds total planned for task
    total_seconds = db.Column(db.Integer, nullable=False, default=0)
    # seconds still remaining (updated when pausing/finishing)
    remaining_seconds = db.Column(db.Integer, nullable=False, default=0)

    status = db.Column(db.String(20), nullable=False, default="pending")  # pending|running|paused|done

    started_at = db.Column(db.DateTime, nullable=True)  # when last started/resumed (UTC)
    finished_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def as_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "total_seconds": self.total_seconds,
            "remaining_seconds": self.calc_remaining_live(),
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    # live remaining (if running, subtract elapsed since started_at)
    def calc_remaining_live(self):
        if self.status == "running" and self.started_at:
            elapsed = int((datetime.now(timezone.utc) - self.started_at).total_seconds())
            left = max(0, self.remaining_seconds - elapsed)
            return left
        return self.remaining_seconds

    # finalize if needed (and auto-start next)
    def tick_and_transition_if_needed(self):
        if self.status == "running":
            left = self.calc_remaining_live()
            if left <= 0:
                # mark as done
                self.status = "done"
                self.remaining_seconds = 0
                self.finished_at = datetime.now(timezone.utc)
                self.started_at = None
                db.session.add(self)
                db.session.commit()
                auto_start_next()
                return True
        return False


def get_running():
    return Task.query.filter_by(status="running").first()


def auto_start_next():
    """Start the next pending task automatically (first created)."""
    already_running = get_running()
    if already_running:
        return
    next_task = Task.query.filter_by(status="pending").order_by(Task.created_at.asc()).first()
    if next_task:
        next_task.status = "running"
        next_task.started_at = datetime.now(timezone.utc)
        # ensure remaining is set (in case of edits)
        if next_task.remaining_seconds <= 0:
            next_task.remaining_seconds = next_task.total_seconds
        db.session.add(next_task)
        db.session.commit()


@app.before_first_request
def init_db():
    db.create_all()


@app.route("/")
def index():
    return render_template("index.html")


# ---- API ----

@app.route("/api/tasks", methods=["GET"])
def api_list():
    # heartbeat: update transitions if needed
    for t in Task.query.filter(Task.status == "running").all():
        t.tick_and_transition_if_needed()
    tasks = Task.query.order_by(Task.created_at.asc()).all()
    return jsonify([t.as_dict() for t in tasks])


@app.route("/api/tasks", methods=["POST"])
def api_create():
    data = request.get_json(force=True)
    name = data.get("name", "").strip() or "משימה ללא שם"
    hours = int(data.get("hours", 0) or 0)
    minutes = int(data.get("minutes", 0) or 0)
    seconds = int(data.get("seconds", 0) or 0)
    total = hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        return jsonify({"error": "יש להזין זמן גדול מ־0"}), 400

    task = Task(
        name=name,
        total_seconds=total,
        remaining_seconds=total,
        status="pending",
    )
    db.session.add(task)
    db.session.commit()
    return jsonify(task.as_dict()), 201


@app.route("/api/tasks/<int:task_id>/start", methods=["POST"])
def api_start(task_id):
    running = get_running()
    if running and running.id != task_id:
        return jsonify({"error": "כבר קיימת משימה פעילה"}), 409

    task = Task.query.get_or_404(task_id)
    # normalize state
    for t in Task.query.filter(Task.status == "running").all():
        if t.id != task.id:
            # should not happen due to check above
            t.status = "paused"
            t.remaining_seconds = t.calc_remaining_live()
            t.started_at = None
            db.session.add(t)

    if task.status in ("pending", "paused"):
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        db.session.add(task)
        db.session.commit()
        return jsonify(task.as_dict())

    return jsonify({"error": "לא ניתן להתחיל משימה במצב זה"}), 400


@app.route("/api/tasks/<int:task_id>/pause", methods=["POST"])
def api_pause(task_id):
    task = Task.query.get_or_404(task_id)
    if task.status == "running":
        task.remaining_seconds = task.calc_remaining_live()
        task.status = "paused"
        task.started_at = None
        db.session.add(task)
        db.session.commit()
        return jsonify(task.as_dict())
    return jsonify({"error": "המשימה אינה פעילה"}), 400


@app.route("/api/tasks/<int:task_id>/resume", methods=["POST"])
def api_resume(task_id):
    running = get_running()
    if running and running.id != task_id:
        return jsonify({"error": "כבר קיימת משימה פעילה"}), 409

    task = Task.query.get_or_404(task_id)
    if task.status == "paused" and task.remaining_seconds > 0:
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        db.session.add(task)
        db.session.commit()
        return jsonify(task.as_dict())
    return jsonify({"error": "לא ניתן להמשיך משימה במצב זה"}), 400


@app.route("/api/tasks/<int:task_id>/finish", methods=["POST"])
def api_finish(task_id):
    task = Task.query.get_or_404(task_id)
    # finalize regardless of state
    task.remaining_seconds = 0
    task.status = "done"
    task.started_at = None
    task.finished_at = datetime.now(timezone.utc)
    db.session.add(task)
    db.session.commit()
    # start next if available
    auto_start_next()
    return jsonify(task.as_dict())


@app.route("/api/tasks/<int:task_id>", methods=["PATCH"])
def api_edit(task_id):
    task = Task.query.get_or_404(task_id)
    data = request.get_json(force=True)
    if "name" in data:
        task.name = data["name"].strip() or task.name

    # allow changing planned time only if not running
    if any(k in data for k in ("hours", "minutes", "seconds")):
        if task.status == "running":
            return jsonify({"error": "לא ניתן לערוך זמן כשמשימה רצה"}), 409
        hours = int(data.get("hours", 0) or 0)
        minutes = int(data.get("minutes", 0) or 0)
        seconds = int(data.get("seconds", 0) or 0)
        total = hours * 3600 + minutes * 60 + seconds
        if total <= 0:
            return jsonify({"error": "יש להזין זמן גדול מ־0"}), 400
        task.total_seconds = total
        # אם טרם רצה או בהשהיה/ממתינה – מעדכן גם remaining
        if task.status in ("pending", "paused", "done"):
            task.remaining_seconds = total if task.status != "done" else 0

    db.session.add(task)
    db.session.commit()
    return jsonify(task.as_dict())


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def api_delete(task_id):
    task = Task.query.get_or_404(task_id)
    was_running = task.status == "running"
    db.session.delete(task)
    db.session.commit()
    # אם מחקנו משימה פעילה – מפעיל את הבאה
    if was_running:
        auto_start_next()
    return jsonify({"ok": True})


if __name__ == "__main__":
    # לשימוש מקומי
    app.run(host="0.0.0.0", port=5000, debug=True)
