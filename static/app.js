const fmt = (v) => (Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10));

function paintStars(el, value) {
  el.querySelectorAll(".star-btn").forEach((btn) => {
    const star = Number(btn.dataset.star);
    const filled = value >= star;
    const half = !filled && value >= star - 0.5;
    btn.classList.toggle("fill", filled);
    btn.classList.toggle("half", half);
    btn.classList.toggle("empty", !(filled || half));
  });
}

function starsValue(btn) {
  const stars = btn.closest("[data-stars]");
  const star = Number(btn.dataset.star);
  const rect = btn.getBoundingClientRect();
  const half = (eventX(btn, rect) <= rect.width / 2);
  const val = half ? star - 0.5 : star;
  return Math.max(0, val);
}

function eventX(btn, rect) {
  if (btn._lastX !== undefined) return btn._lastX;
  return 0;
}

function valueToStars(v) {
  return Math.max(0, Math.min(5, v / 2));
}

function starsToValue(s) {
  return Math.round(Math.max(0, Math.min(5, s)) * 2);
}

function paint(day) {
  document.querySelectorAll("[data-task-id]").forEach((el) => {
    const id = el.dataset.taskId;
    if (!(id in day.values)) return;
    const value = day.values[id];
    const out = el.querySelector('[data-role="value"]');
    if (out) {
      out.textContent = fmt(value);
      out.classList.toggle("done", value > 0);
    }
    const hidden = el.querySelector('[data-role="hidden-value"]');
    if (hidden) hidden.value = value;
    const stars = el.querySelector("[data-stars]");
    if (stars) paintStars(stars, valueToStars(value));
  });

  const s = day.summary;
  const set = (role, text) => {
    const el = document.querySelector(`[data-role="summary-${role}"]`);
    if (el) el.textContent = text;
  };
  set("avg", s.avg === null || s.avg === undefined ? "-" : fmt(s.avg));
  set("done", s.done);
  set("total", s.total);
  set("leaf-done", s.leaf_done);
  set("leaf-total", s.leaf_total);

  const avg = document.querySelector('[data-role="summary-avg"]');
  if (avg) {
    const hint = avg.parentElement.querySelector(".hint");
    if (hint) hint.textContent = `${Math.round((s.completion || 0) * 100)}% completadas`;
  }
}

async function sendValue(form, value) {
  const data = new FormData(form);
  const payload = {
    task_id: Number(data.get("task_id")),
    day: data.get("day"),
    value: value,
  };
  const note = data.get("note");
  if (note !== null) payload.note = note;

  const res = await fetch("/api/entry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    window.alert(err.error || "No se pudo guardar");
    return;
  }
  paint(await res.json());
}

document.addEventListener("click", (event) => {
  const btn = event.target.closest(".star-btn");
  if (!btn) return;
  event.preventDefault();
  const form = btn.closest("[data-form]");
  if (!form) return;
  const val = starsToValue(starsValue(btn));
  sendValue(form, val);
});

document.addEventListener("mousedown", (event) => {
  const btn = event.target.closest(".star-btn");
  if (!btn) return;
  btn._lastX = event.clientX - btn.getBoundingClientRect().left;
});

document.addEventListener("touchstart", (event) => {
  const btn = event.target.closest(".star-btn");
  if (!btn) return;
  const t = event.changedTouches[0];
  btn._lastX = t.clientX - btn.getBoundingClientRect().left;
}, { passive: true });

let noteTimer = null;
document.addEventListener("input", (event) => {
  const area = event.target.closest('[data-role="day-note"]');
  if (!area) return;
  clearTimeout(noteTimer);
  const day = area.closest("form").querySelector('[name="day"]').value;
  noteTimer = setTimeout(async () => {
    const res = await fetch("/api/nota-dia", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ day, content: area.value }),
    });
    const saved = document.querySelector('[data-role="saved"]');
    if (saved && res.ok) {
      saved.textContent = "guardado";
      setTimeout(() => (saved.textContent = ""), 1500);
    }
  }, 700);
});
