let timer;
let seconds = 0;

function updateTimer() {
    let hrs = String(Math.floor(seconds / 3600)).padStart(2, '0');
    let mins = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
    let secs = String(seconds % 60).padStart(2, '0');
    document.getElementById("timer").innerText = `${hrs}:${mins}:${secs}`;
}

function startTimer() {
    if (!timer) {
        timer = setInterval(() => {
            seconds++;
            updateTimer();
        }, 1000);
    }
}

function stopTimer() {
    clearInterval(timer);
    timer = null;
}

function resetTimer() {
    stopTimer();
    seconds = 0;
    updateTimer();
}

updateTimer();
