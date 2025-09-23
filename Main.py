from flask import Flask, request, jsonify, make_response
from datetime import datetime, timedelta, timezone
import pytz
import threading

app = Flask(__name__)

IL_TZ = pytz.timezone("Asia/Jerusalem")

# ========= in-memory store =========
tasks = []  # ordered list
_next_id = 1
lock = threading.Lock()


def now_utc():
    return datetime.now(timezone.utc)


def format_il(dt_utc: datetime) -> str:
    """Return IL local time string HH:MM:SS dd.mm.yyyy"""
    if dt_utc is None:
        return ""
    # dt_utc is timezone-aware UTC
    il = dt_utc.astimezone(IL_TZ)
    return il.strftime("%H:%M:%S %d.%m.%Y")


def remaining_seconds(task, ref: datetime) -> int:
    """Server-authoritative remaining seconds for a task at time ref (UTC)."""
    status = task["status"]
    if status == "done":
        return 0
    if status == "running":
        start_ts = task["start_ts"]
        if start_ts is None:
            return max(0, int(task["remaining_at_start"]))
        elapsed = int((ref - start_ts).total_seconds())
        return max(0, int(task["remaining_at_start"]) - elapsed)
    # ready / paused
    return max(0, int(task["remaining_at_start"]))


def cascade_projection(ref: datetime):
    """
    Build a projection of:
     - per task: predicted_end_utc, remaining_now
     - end_all_utc
    Also performs auto-advance: if running finished -> mark done and start next.
    """
    changed = False

    # ----- auto-advance if a running task finished -----
    # We may loop in case multiple very short tasks passed while app slept
    while True:
        running_idx = next((i for i, t in enumerate(tasks) if t["status"] == "running"), None)
        if running_idx is None:
            break
        rem = remaining_seconds(tasks[running_idx], ref)
        if rem > 0:
            break
        # mark running as done
        tasks[running_idx]["status"] = "done"
        tasks[running_idx]["finished_ts"] = ref
        tasks[running_idx]["start_ts"] = None
        tasks[running_idx]["remaining_at_start"] = 0
        changed = True
        # start next not-done (ready/paused)
        next_idx = next((i for i, t in enumerate(tasks[running_idx + 1:], start=running_idx + 1)
                         if t["status"] != "done"), None)
        if next_idx is not None:
            t = tasks[next_idx]
            t["status"] = "running"
            t["start_ts"] = ref
            # remaining_at_start already holds what’s left (for ready it's original; for paused it's saved remainder)
            changed = True
        else:
            break

    # ----- build projection -----
    projection = []
    cursor = ref
    # If there is a running task, the cursor after it will be now + remaining_running
    for idx, t in enumerate(tasks):
        rem_now = remaining_seconds(t, ref)
        if t["status"] == "running":
            predicted_end = ref + timedelta(seconds=rem_now)
            cursor = predicted_end
        elif t["status"] in ("ready", "paused"):
            predicted_end = cursor + timedelta(seconds=rem_now)
            cursor = predicted_end
        else:  # done
            predicted_end = t.get("finished_ts") or ref

        projection.append({
            "id": t["id"],
            "remaining_now": rem_now,
            "predicted_end_utc": predicted_end
        })

    end_all_utc = cursor

    return projection, end_all_utc, changed


def serialize_state():
    ref = now_utc()
    proj, end_all_utc, changed = cascade_projection(ref)

    state_tasks = []
    for base, p in zip(tasks, proj):
        state_tasks.append({
            "id": base["id"],
            "name": base["name"],
            "status": base["status"],
            "original_seconds": base["original_seconds"],
            "initial_str": seconds_to_hms(base["original_seconds"]),
            "remaining_seconds": p["remaining_now"],
            "remaining_str": seconds_to_hms(p["remaining_now"]),
            "predicted_end": format_il(p["predicted_end_utc"]),
        })

    return {
        "now_il": format_il(ref),
        "end_all": format_il(end_all_utc),
        "tasks": state_tasks
    }


def seconds_to_hms(total: int) -> str:
    total = max(0, int(total))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def normalize_duration(h, m, s) -> int:
    try:
        h = int(h or 0)
        m = int(m or 0)
        s = int(s or 0)
    except Exception:
        return 0
    if h < 0 or m < 0 or s < 0:
        return 0
    return h * 3600 + m * 60 + s


@app.get("/")
def index():
    # serve the static index.html that lives next to Main.py
    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()
    return make_response(html)


@app.get("/state")
def state():
    with lock:
        data = serialize_state()
    return jsonify(data)


@app.post("/tasks")
def add_task():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip() or f"משימה {len(tasks) + 1}"
    total = normalize_duration(body.get("h"), body.get("m"), body.get("s"))

    with lock:
        global _next_id
        t = {
            "id": _next_id,
            "name": name,
            "status": "ready",              # ready | running | paused | done
            "original_seconds": total,      # the base original
            "remaining_at_start": total,    # when starting/running, what is left at start_ts
            "start_ts": None,               # UTC aware
            "finished_ts": None,
        }
        _next_id += 1
        tasks.append(t)
        data = serialize_state()
    return jsonify(data)


@app.post("/start/<int:tid>")
def start_task(tid):
    ref = now_utc()
    with lock:
        # pause currently running (if any)
        run = next((t for t in tasks if t["status"] == "running"), None)
        if run and run["id"] != tid:
            # compute remainder and pause
            rem = remaining_seconds(run, ref)
            run["status"] = "paused"
            run["start_ts"] = None
            run["remaining_at_start"] = rem

        # start this one (only if not done)
        t = next((t for t in tasks if t["id"] == tid), None)
        if not t or t["status"] == "done":
            data = serialize_state()
            return jsonify(data)

        # compute fresh remainder for safety
        rem = remaining_seconds(t, ref)
        t["status"] = "running"
        t["start_ts"] = ref
        t["remaining_at_start"] = rem
        t["finished_ts"] = None

        data = serialize_state()
    return jsonify(data)


@app.post("/pause/<int:tid>")
def pause_task(tid):
    ref = now_utc()
    with lock:
        t = next((t for t in tasks if t["id"] == tid), None)
        if t and t["status"] == "running":
            rem = remaining_seconds(t, ref)
            t["status"] = "paused"
            t["start_ts"] = None
            t["remaining_at_start"] = rem
        data = serialize_state()
    return jsonify(data)


@app.post("/reset/<int:tid>")
def reset_task(tid):
    """Reset to original time AND start immediately from the beginning."""
    ref = now_utc()
    with lock:
        # pause any running task (except the target)
        run = next((t for t in tasks if t["status"] == "running" and t["id"] != tid), None)
        if run:
            rem = remaining_seconds(run, ref)
            run["status"] = "paused"
            run["start_ts"] = None
            run["remaining_at_start"] = rem

        t = next((t for t in tasks if t["id"] == tid), None)
        if t:
            t["status"] = "running"
            t["start_ts"] = ref
            t["remaining_at_start"] = t["original_seconds"]
            t["finished_ts"] = None

        data = serialize_state()
    return jsonify(data)


@app.post("/update/<int:tid>")
def update_task():
    """
    Allowed only when NOT running.
    Accepts: name (optional), h/m/s (optional)
    If duration is changed:
       - ready/paused -> set remaining_at_start = new_total
       - done -> becomes ready with remaining_at_start = new_total and finished_ts cleared
    """
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name")
    h, m, s = body.get("h"), body.get("m"), body.get("s")
    new_total = normalize_duration(h, m, s) if (h is not None or m is not None or s is not None) else None

    with lock:
        t = next((t for t in tasks if t["id"] == tid), None)
        if t and t["status"] != "running":
            if isinstance(name, str):
                t["name"] = name.strip()

            if new_total is not None:
                t["original_seconds"] = new_total
                if t["status"] in ("ready", "paused"):
                    t["remaining_at_start"] = new_total
                elif t["status"] == "done":
                    t["status"] = "ready"
                    t["remaining_at_start"] = new_total
                    t["finished_ts"] = None

        data = serialize_state()
    return jsonify(data)


@app.post("/delete/<int:tid>")
def delete_task(tid):
    with lock:
        idx = next((i for i, t in enumerate(tasks) if t["id"] == tid), None)
        if idx is not None:
            del tasks[idx]
        data = serialize_state()
    return jsonify(data)


# Optional: simple health endpoint
@app.get("/ping")
def ping():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=False)
