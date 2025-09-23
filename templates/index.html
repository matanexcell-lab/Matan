const el = (sel, root = document) => root.querySelector(sel);
const els = (sel, root = document) => [...root.querySelectorAll(sel)];
const tasksEl = el('#tasks');

let tasks = [];
let editId = null;

function secsToHMS(secs) {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  const pad = n => String(n).padStart(2, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

function hmsInputsToSeconds(h, m, s) {
  return (parseInt(h||0)*3600) + (parseInt(m||0)*60) + parseInt(s||0);
}

async function api(path, opts={}) {
  const res = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts});
  if (!res.ok) {
    let msg = 'שגיאה';
    try { const j = await res.json(); if (j.error) msg = j.error; } catch(_) {}
    throw new Error(msg);
  }
  return res.json();
}

function render() {
  tasksEl.innerHTML = '';
  if (tasks.length === 0) {
    tasksEl.innerHTML = '<div class="empty">אין משימות עדיין</div>';
    return;
  }

  tasks.forEach(t => {
    const row = document.createElement('div');
    row.className = `task ${t.status}`;

    const name = document.createElement('div');
    name.className = 'name';
    name.textContent = t.name;

    const status = document.createElement('div');
    status.className = 'status';
    status.textContent = ({
      pending: 'ממתינה',
      running: 'פעילה',
      paused:  'בהשהיה',
      done:    'הושלמה'
    })[t.status];

    const time = document.createElement('div');
    time.className = 'time';
    time.textContent = secsToHMS(t.remaining_seconds);

    const actions = document.createElement('div');
    actions.className = 'actions';

    // buttons
    const startBtn = document.createElement('button');
    startBtn.textContent = 'התחל';
    startBtn.onclick = async () => { 
      try { await api(`/api/tasks/${t.id}/start`, {method:'POST'}); await refresh(); }
      catch(e){ alert(e.message); }
    };

    const pauseBtn = document.createElement('button');
    pauseBtn.textContent = 'השהה';
    pauseBtn.onclick = async () => { 
      try { await api(`/api/tasks/${t.id}/pause`, {method:'POST'}); await refresh(); }
      catch(e){ alert(e.message); }
    };

    const resumeBtn = document.createElement('button');
    resumeBtn.textContent = 'המשך';
    resumeBtn.onclick = async () => { 
      try { await api(`/api/tasks/${t.id}/resume`, {method:'POST'}); await refresh(); }
      catch(e){ alert(e.message); }
    };

    const finishBtn = document.createElement('button');
    finishBtn.textContent = 'סיים';
    finishBtn.onclick = async () => { 
      try { await api(`/api/tasks/${t.id}/finish`, {method:'POST'}); await refresh(); }
      catch(e){ alert(e.message); }
    };

    const editBtn = document.createElement('button');
    editBtn.textContent = 'ערוך';
    editBtn.onclick = () => openEditDialog(t);

    const delBtn = document.createElement('button');
    delBtn.textContent = 'מחק';
    delBtn.onclick = async () => {
      if (!confirm('למחוק את המשימה?')) return;
      await api(`/api/tasks/${t.id}`, {method:'DELETE'});
      await refresh();
    };

    // show relevant controls by status
    if (t.status === 'pending') actions.append(startBtn, editBtn, delBtn);
    if (t.status === 'running') actions.append(pauseBtn, finishBtn);
    if (t.status === 'paused') actions.append(resumeBtn, finishBtn, editBtn, delBtn);
    if (t.status === 'done') actions.append(editBtn, delBtn);

    row.append(name, status, time, actions);
    tasksEl.append(row);
  });
}

async function refresh() {
  tasks = await api('/api/tasks');
  render();
}

// add task
el('#addBtn').onclick = async () => {
  const name = el('#taskName').value.trim();
  const hours = el('#hours').value;
  const minutes = el('#minutes').value;
  const seconds = el('#seconds').value;
  const total = hmsInputsToSeconds(hours, minutes, seconds);
  if (total <= 0) return alert('יש להזין זמן גדול מ־0');

  await api('/api/tasks', {
    method:'POST',
    body: JSON.stringify({ name, hours, minutes, seconds })
  });
  el('#taskName').value = '';
  el('#hours').value = 0;
  el('#minutes').value = 0;
  el('#seconds').value = 0;
  await refresh();
};

// --- Edit dialog ---
function openEditDialog(task) {
  editId = task.id;
  el('#editName').value = task.name;
  // fill time from remaining if not running/done; אחרת מהכללי
  const useSecs = (task.status === 'running') ? task.total_seconds : task.remaining_seconds || task.total_seconds;
  const h = Math.floor(useSecs/3600);
  const m = Math.floor((useSecs%3600)/60);
  const s = useSecs%60;
  el('#editHours').value = h;
  el('#editMinutes').value = m;
  el('#editSeconds').value = s;

  const dlg = el('#editDialog');
  dlg.showModal();
}

el('#saveEdit').addEventListener('click', async (e) => {
  e.preventDefault();
  if (editId == null) return;
  const name = el('#editName').value.trim();
  const hours = parseInt(el('#editHours').value || 0);
  const minutes = parseInt(el('#editMinutes').value || 0);
  const seconds = parseInt(el('#editSeconds').value || 0);

  const payload = { name };
  // שינוי זמן מותר רק כשלא רצה – השרת יאמת
  payload.hours = hours; payload.minutes = minutes; payload.seconds = seconds;

  try {
    await api(`/api/tasks/${editId}`, { method:'PATCH', body: JSON.stringify(payload) });
    el('#editDialog').close();
    editId = null;
    await refresh();
  } catch (err) {
    alert(err.message);
  }
});

el('#cancelEdit').addEventListener('click', (e) => {
  e.preventDefault();
  el('#editDialog').close();
  editId = null;
});

// Poll every second so the server can finalize & auto-start next
setInterval(refresh, 1000);
refresh();
