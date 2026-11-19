const uploadForm = document.querySelector("[data-upload]");

if (uploadForm) {
  const fileInput = uploadForm.querySelector("input[type=file]");
  const fileName = uploadForm.querySelector("[data-file-name]");
  const dropzone = uploadForm.querySelector("[data-dropzone]");
  const status = uploadForm.querySelector("[data-upload-status]");
  const submitButton = uploadForm.querySelector("button[type=submit]");

  function showFileName() {
    const selected = fileInput.files[0];
    fileName.textContent = selected ? selected.name : "";
    fileName.hidden = !selected;
  }

  fileInput.addEventListener("change", showFileName);
  dropzone.addEventListener("dragover", (event) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    dropzone.classList.add("dragging");
  });
  dropzone.addEventListener("dragleave", (event) => {
    if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove("dragging");
  });
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("dragging");
    const selected = event.dataTransfer.files[0];
    if (!selected) return;
    const files = new DataTransfer();
    files.items.add(selected);
    fileInput.files = files.files;
    showFileName();
  });

  uploadForm.addEventListener("submit", () => {
    status.hidden = false;
    submitButton.disabled = true;
  });

  window.addEventListener("pageshow", () => {
    submitButton.disabled = false;
    status.hidden = true;
    showFileName();
  });
}
