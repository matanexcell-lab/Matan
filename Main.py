from flask import Flask, render_template_string

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Task Timer</title>
  <style>
    body{font-family:Arial,Helvetica,sans-serif;text-align:center;margin:40px}
    h1{color:#1976d2}
    .timer{font-size:48px;margin:24px 0}
    button{font-size:18px;padding:10px 16px;margin:6px;border:0;border-radius:8px;background:#1976d2;color:white}
    button:hover{filter:brightness(1.05)}
  </style>
</head>
<body>
  <h1>⏳ Task Timer</h1>
  <div class="timer" id="timer">00:00:00</div>
  <button onclick="startTimer()">Start</button>
  <button onclick="stopTimer()">Stop</button>
  <button onclick="resetTimer()">Reset</button>

  <script>
    // לוגיקה מבוססת-שעה: לא מאבדת זמן גם אם המסך ננעל/עובר לרקע
    let startTime = null;
    let running = false;
    let elapsedBefore = 0; // מ״ש שנצברו לפני הפעלה מחדש
    let handle = null;

    function fmt(t){
      const h = String(Math.floor(t/3600)).padStart(2,'0');
      const m = String(Math.floor((t%3600)/60)).padStart(2,'0');
      const s = String(Math.floor(t%60)).padStart(2,'0');
      return `${h}:${m}:${s}`;
    }

    function tick(){
      const now = Date.now();
      const elapsed = running ? ((now - startTime)/1000 + elapsedBefore) : elapsedBefore;
      document.getElementById('timer').textContent = fmt(elapsed);
    }

    function startTimer(){
      if (running) return;
      running = true;
      startTime = Date.now();
      handle = setInterval(tick, 250);
    }

    function stopTimer(){
      if (!running) return;
      elapsedBefore = (Date.now() - startTime)/1000 + elapsedBefore;
      running = false;
      clearInterval(handle);
      handle = null;
      tick();
    }

    function resetTimer(){
      running = false;
      startTime = null;
      elapsedBefore = 0;
      if (handle){ clearInterval(handle); handle = null; }
      tick();
    }

    tick();
  </script>
</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
