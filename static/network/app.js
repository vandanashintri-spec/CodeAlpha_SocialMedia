document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-confirm]");
  if (form && !window.confirm(form.dataset.confirm)) {
    event.preventDefault();
  }
});

document.querySelectorAll(".message").forEach((message) => {
  window.setTimeout(() => message.classList.add("message-dismissed"), 4500);
});

document.querySelectorAll("[data-image-preview]").forEach((preview) => {
  const form = preview.closest("form");
  const input = form?.querySelector('input[type="file"][name="image"]');
  const image = preview.querySelector("img");
  const removeButton = preview.querySelector("[data-remove-image]");
  if (!input || !image || !removeButton) return;

  let previewUrl;
  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (!file) {
      preview.hidden = true;
      return;
    }
    if (!file.type.startsWith("image/")) {
      input.value = "";
      preview.hidden = true;
      window.alert("Choose an image file.");
      return;
    }
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    image.src = previewUrl;
    preview.hidden = false;
  });

  removeButton.addEventListener("click", () => {
    input.value = "";
    preview.hidden = true;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  });
});
