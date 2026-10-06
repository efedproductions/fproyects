const fmt = (v) => (Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10));

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
    const input = el.querySelector('[data-role="input"]');
    if (input && document.activeElement !== input) input.value = value;
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
  const btn = event.target.closest(".quick .q");
  if (!btn) return;
  event.preventDefault();
  sendValue(btn.closest("form"), Number(btn.value));
});

document.addEventListener("change", (event) => {
  const input = event.target.closest('[data-role="input"]');
  if (!input) return;
  sendValue(input.closest("form"), Number(input.value));
});

document.addEventListener("keydown", (event) => {
  const input = event.target.closest('[data-role="input"]');
  if (!input || event.key !== "Enter") return;
  event.preventDefault();
  sendValue(input.closest("form"), Number(input.value));
});

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