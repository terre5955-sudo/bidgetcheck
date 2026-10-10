async function submitAuth(ev, endpoint, redirect) {
  ev.preventDefault();
  const form = ev.target;
  const msg = document.getElementById("msg");
  const button = form.querySelector("button");
  const next = new URLSearchParams(location.search).get("next") || redirect;
  msg.textContent = "";
  button.disabled = true;
  const body = new FormData(form);
  try {
    const r = await fetch(endpoint, { method: "POST", body });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || "Something went wrong.");
    msg.style.color = "#0f7b46";
    msg.textContent = "Signed in. Redirecting...";
    location.href = next;
  } catch (err) {
    msg.style.color = "#a11b1b";
    msg.textContent = err.message;
    button.disabled = false;
  }
}