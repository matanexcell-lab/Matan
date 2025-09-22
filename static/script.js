async function loadTasks() {
  let res = await fetch("/get_tasks");
  let tasks = await res.json();

  let container = document.getElementById("tasks");
  container.innerHTML = "";

  tasks.forEach(t => {
    let div = document.createElement("div");
    let endTime = t.end_time > 0 ? new Date(t.end_time * 1000) : null;
    let now = new Date();

    let remaining = 0;
    if (t.status === "running") {
      remaining = Math.max(0, Math.floor((t.end_time * 1000 - now) / 1000));
    }

    let hrs = Math.floor(remaining / 3600);
    let mins = Math.floor((remaining % 3600) / 60);
    let secs = remaining % 60;

    div.innerHTML = `
      <b>${t.name}</b> - מצב: ${t.status}<br>
      ${t.status === "running" ? `⏱ נותר: ${hrs}:${mins.toString().padStart(2,"0")}:${secs.toString().padStart(2,"0")}<br>` : ""}
      ${endTime ? `🕒 סיום משוער: ${endTime.toLocaleTimeString()}<br>` : ""}
      <button onclick="pauseTask(${t.id})">עצור</button>
      <button onclick="resumeTask(${t.id})">המשך</button>
      <button onclick="finishTask(${t.id})">סיים</button>
    `;
    container.appendChild(div);
  });
}

async function addTask() {
  let name = document.getElementById("taskName").value;
  let hours = parseInt(document.getElementById("taskHours").value) || 0;
  let minutes = parseInt(document.getElementById("taskMinutes").value) || 0;
  let seconds = parseInt(document.getElementById("taskSeconds").value) || 0;

  let totalSeconds = hours * 3600 + minutes * 60 + seconds;

  await fetch("/add_task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name, seconds: totalSeconds})
  });
  loadTasks();
}

async function pauseTask(id) {
  await fetch("/pause_task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id: Number(id)})
  });
  loadTasks();
}

async function resumeTask(id) {
  await fetch("/resume_task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id: Number(id)})
  });
  loadTasks();
}

async function finishTask(id) {
  await fetch("/finish_task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id: Number(id)})
  });
  loadTasks();
}

setInterval(loadTasks, 1000);
loadTasks();
