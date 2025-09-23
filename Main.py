from flask import Flask, request, jsonify, make_response, render_template_string
from datetime import datetime, timedelta, timezone
import pytz, threading, os

app = Flask(__name__)

IL_TZ = pytz.timezone("Asia/Jerusalem")

tasks = []
_next_id = 1
lock = threading.Lock()


def now_utc():
    return datetime.now(timezone.utc)


def format_il(dt_utc: datetime) -> str:
    if dt_utc is None:
        return ""
    il = dt_utc.astimezone(IL_TZ)
    return il.strftime("%H:%M:%S %d.%m.%Y")


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


# ========= ROUTES =========

@app.get("/")
def index():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            html = f.read()
        return make_response(html)
    else:
        return render_template_string("""
            <html><body><h1>🚀 השרת רץ</h1>
            <p>אבל index.html לא נמצא. ודא שהעלית אותו!</p>
            </body></html>
        """)


@app.get("/ping")
def ping():
    return "ok", 200


# כאן באים כל שאר ה־routes (state, tasks, start, pause, reset, update, delete)
# בדיוק מהקוד המלא ששלחתי לך בהודעה הקודמת – הם נשארים אותו דבר.
