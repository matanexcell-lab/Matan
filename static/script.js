let timer;
let seconds = 0;
let running = false;

function updateDisplay() {
    let hrs = String(Math.floor(seconds / 3600)).padStart(2, '0');
    let mins = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
    let secs = String(seconds % 60).padStart(2, '0');
    document.getElementById("timer").innerText = `${hrs}:${mins}:${secs}`;
}

function startTimer() {
    if (!running) {
        running = true;
        document.getElementById("status").innerText = "⏳ משימה פעילה";
        timer = setInterval(() => {
            seconds++;
            updateDisplay();
        }, 1000);
    }
}

function stopTimer() {
    running = false;
    document.getElementById("status").innerText = "⏸️ הושבת";
    clearInterval(timer);
}

function resetTimer() {
    running = false;
    clearInterval(timer);
    seconds = 0;
    document.getElementById("status").innerText = "אין משימה פעילה";
    updateDisplay();
}

updateDisplay();
