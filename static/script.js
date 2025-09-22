function openEditModal(taskId) {
  const modal = document.getElementById("editModal-" + taskId);
  modal.style.display = "block";
}

function closeEditModal(taskId) {
  const modal = document.getElementById("editModal-" + taskId);
  modal.style.display = "none";
}

function saveEdit(event, taskId) {
  event.preventDefault(); // מונע רענון דף

  const newTitle = document.getElementById("taskTitle-" + taskId).value;
  
  // כאן אפשר לקרוא לשרת (באמצעות fetch) כדי לשמור את השינוי
  console.log("שמירת משימה", taskId, "כ:", newTitle);

  closeEditModal(taskId);
}
