async function loadTasks() {
  let res = await fetch("/get_tasks");
  let tasks = await res.json();
  let container = document.getElementById("tasks");
  container.innerHTML = "";

  tasks.forEach(t => {
    let div = document.createElement("div");
    div.id = `task-${t.id}`;
    let now = new Date();
    let remaining = 0;

    if (t.status === "running") {
      remaining = Math.max(0, Math.floor((t.end_time * 1000 - now) / 1000));
    } else if (t.status === "paused" && t.end_time > 0) {
      remaining = Math.max(0, Math.floor(t["end_time"] - t["paused_time"]));
    }

    let hrs = Math.floor(remaining / 3600);
    let mins = Math.floor((remaining % 3600) / 60);
    let secs = remaining % 60;
    let endTime = t.end_time > 0 ? new Date(t.end_time * 1000) : null;

    div.innerHTML = `
      <b>${t.name}</b> - מצב: ${t.status}<br>
      ⏱ נותר: ${hrs}:${mins.toString().padStart(2,"0")}:${secs.toString().padStart(2,"0")}<br>
      ${endTime ? `🕒 סיום משוער: ${endTime.toLocaleTimeString()}<br>` : ""}
      ${t.status === "waiting" ? `<button onclick="startTask(${t.id})">▶ התחל</button>` : ""}
      <button onclick="pauseTask(${t.id})">⏸ עצור</button>
      <button onclick="resumeTask(${t.id})">▶ המשך</button>
      <button onclick="finishTask(${t.id})">✔ סיים</button>
      <button onclick="deleteTask(${t.id})">🗑 מחק</button>
      <button onclick="showEditForm(${t.id}, '${t.name}')">✏ ערוך</button>
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

async function startTask(id) {
  await fetch("/start_task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id: Number(id)})
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

async function deleteTask(id) {
  await fetch("/delete_task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id: Number(id)})
  });
  loadTasks();
}

function showEditForm(id, currentName) {
  let div = document.getElementById(`task-${id}`);
  let formDiv = document.createElement("div");
  formDiv.innerHTML = `
    <input id="editName${id}" value="${currentName}">
    <input id="editHours${id}" type="number" placeholder="שעות" min="0">
    <input id="editMinutes${id}" type="number" placeholder="דקות" min="0">
    <input id="editSeconds${id}" type="number" placeholder="שניות" min="0">
    <button onclick="saveEdit(${id})">שמור</button>
    <button onclick="loadTasks()">ביטול</button>
  `;
  div.appendChild(formDiv);
}

async function saveEdit(id) {
  let newName = document.getElementById(`editName${id}`).value;
  let hours = parseInt(document.getElementById(`editHours${id}`).value) || 0;
  let minutes = parseInt(document.getElementById(`editMinutes${id}`).value) || 0;
  let seconds = parseInt(document.getElementById(`editSeconds${id}`).value) || 0;
  let totalSeconds = hours * 3600 + minutes * 60 + seconds;

  await fetch("/edit_task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id: Number(id), name: newName, seconds: totalSeconds})
  });
  loadTasks();
}

setInterval(loadTasks, 1000);
loadTasks();
