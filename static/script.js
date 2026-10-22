// Текущие выбранные значения
let mode = "url";
let tone = "friendly";

const $ = (id) => document.getElementById(id);
const errorBox = $("error");
const post = $("post");

// Переключение вкладок «По ссылке» / «Текстом»
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    mode = btn.dataset.mode;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    $("pane-url").hidden = mode !== "url";
    $("pane-text").hidden = mode !== "text";
    errorBox.hidden = true;
  });
});

// Выбор тона
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    tone = chip.dataset.tone;
    document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === chip));
  });
});

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

// Счётчик символов
function updateCount() {
  const n = post.value.length;
  $("count").textContent = n;
  $("count").parentElement.classList.toggle("over", n > 1500);
}
post.addEventListener("input", updateCount);

// Кнопка «Сформировать пост»
$("go").addEventListener("click", async () => {
  errorBox.hidden = true;
  const btn = $("go");
  btn.disabled = true;
  btn.textContent = "Пишу пост…";

  try {
    const resp = await fetch("/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode,
        tone,
        url: $("url").value,
        text: $("text").value,
        source: $("source").value,
      }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      showError(data.error || "Что-то пошло не так. Попробуйте ещё раз.");
      return;
    }

    post.value = data.post;
    setEditing(false);
    updateCount();
    $("result").hidden = false;
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    showError("Не удалось связаться с сервером. Проверьте, что python app.py запущен.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Сформировать пост";
  }
});

// Карандаш: включает и выключает редактирование
function setEditing(on) {
  post.readOnly = !on;
  $("edit").classList.toggle("on", on);
  if (on) post.focus();
}
$("edit").addEventListener("click", () => setEditing(post.readOnly));

// Показ уведомления
function toast(message) {
  const t = $("toast");
  if (message) t.textContent = message;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 1800);
}

// Переключатель «Отложить публикацию»: показываем выбор даты, по умолчанию — через час
$("schedule-on").addEventListener("change", () => {
  const on = $("schedule-on").checked;
  $("schedule-at").hidden = !on;
  $("publish").textContent = on ? "Отложить публикацию" : "Опубликовать во ВКонтакте";
  if (on && !$("schedule-at").value) {
    const d = new Date(Date.now() + 60 * 60 * 1000);
    d.setMinutes(Math.ceil(d.getMinutes() / 5) * 5, 0, 0);
    // datetime-local ждёт местное время в формате ГГГГ-ММ-ДДTЧЧ:ММ
    const pad = (n) => String(n).padStart(2, "0");
    $("schedule-at").value =
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
});

// Кнопка «Опубликовать во ВКонтакте»
$("publish").addEventListener("click", async () => {
  $("vk-error").hidden = true;
  $("vk-done").hidden = true;

  if (!post.value.trim()) {
    $("vk-error").textContent = "Пост пустой — сначала сформируйте его.";
    $("vk-error").hidden = false;
    return;
  }
  // Если включено «Отложить» — берём выбранное время (в секундах, как ждёт ВКонтакте)
  let publishDate = "";
  if ($("schedule-on").checked) {
    const when = new Date($("schedule-at").value);
    if (!$("schedule-at").value || isNaN(when)) {
      $("vk-error").textContent = "Выберите дату и время публикации.";
      $("vk-error").hidden = false;
      return;
    }
    if (when.getTime() < Date.now() + 3 * 60 * 1000) {
      $("vk-error").textContent = "Время публикации должно быть минимум через 3 минуты.";
      $("vk-error").hidden = false;
      return;
    }
    publishDate = String(Math.floor(when.getTime() / 1000));
  }

  // Подтверждение, чтобы пост не ушёл случайно
  const question = publishDate
    ? `Отложить публикацию на ${new Date($("schedule-at").value).toLocaleString("ru-RU")}?`
    : "Опубликовать этот пост в сообществе ВКонтакте?";
  if (!confirm(question)) return;

  const btn = $("publish");
  btn.disabled = true;
  btn.textContent = "Публикую…";
  try {
    const form = new FormData();
    form.append("post", post.value);
    if (publishDate) form.append("publish_date", publishDate);
    const resp = await fetch("/publish", { method: "POST", body: form });
    const data = await resp.json();
    if (!resp.ok) {
      $("vk-error").textContent = data.error || "Не удалось опубликовать. Попробуйте ещё раз.";
      $("vk-error").hidden = false;
      return;
    }
    $("vk-link").href = data.url;
    $("vk-link").textContent = data.url;
    $("vk-done-text").textContent = data.scheduled
      ? "Пост отложен, он лежит в «Отложенных» сообщества:"
      : "Опубликовано:";
    $("vk-done").hidden = false;
    toast(data.scheduled ? "Публикация отложена" : "Пост опубликован");
  } catch (e) {
    $("vk-error").textContent = "Не удалось связаться с сервером. Проверьте, что python app.py запущен.";
    $("vk-error").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = $("schedule-on").checked ? "Отложить публикацию" : "Опубликовать во ВКонтакте";
  }
});

// Кнопка «Скопировать»
$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(post.value);
  } catch (e) {
    // Запасной вариант для браузеров без доступа к буферу
    post.select();
    document.execCommand("copy");
  }
  toast();
});
