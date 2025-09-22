async function loadTasks() {
  let res = await fetch("/get_tasks");
  let tasks = await res.json();

  let container = document.getElementById("tasks");
  container.innerHTML = "";

  tasks.forEach(t => {
    let div = document.createElement("div");
    let endTime = new Date(t.end_time * 1000);
    let now = new Date();
    let remaining = Math.max(0, Math.floor((t.end_time * 1000 - now) / 1000));
    let min = Math.floor(remaining / 60);
    let sec = remaining % 60;

    div.innerHTML = `
      <b>${t.name}</b> - מצב: ${t.status}  
      ⏱ נותר: ${min}:${sec.toString().padStart(2,"0")}  
      🕒 סיום משוער: ${endTime.toLocaleTimeString()}
      <button onclick="pauseTask(${t.id})">עצור</button>
      <button onclick="resumeTask(${t.id})">המשך</button>
      <button onclick="finishTask(${t.id})">סיים</button>
    `;
    container.appendChild(div);
  });
}

async function addTask() {
  let name = document.getElementById("taskName").value;
  let minutes = document.getElementById("taskMinutes").value;
  await fetch("/add_task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name, minutes})
  });
  loadTasks();
}

async function pauseTask(id) {
  await fetch("/pause_task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id})
  });
  loadTasks();
}

async function resumeTask(id) {
  await fetch("/resume_task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id})
  });
  loadTasks();
}

async function finishTask(id) {
  await fetch("/finish_task", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({id})
  });
  loadTasks();
}

setInterval(loadTasks, 1000);
loadTasks();
