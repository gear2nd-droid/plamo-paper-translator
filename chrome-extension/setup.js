const message = new URLSearchParams(location.search).get('error');
if (message) {
  const box = document.getElementById('errorBox');
  box.textContent = `起動できませんでした: ${message}`;
  box.hidden = false;
}
