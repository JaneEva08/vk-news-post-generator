"""Генератор постов для ВКонтакте из новости про нейросети.

Запуск: python app.py
"""
import os
import time
from urllib.parse import urlparse

import openai
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from openai import OpenAI

# Подтягиваем настройки из файла .env
load_dotenv()

app = Flask(__name__)

# Папка проекта и путь к файлу с правилами для поста
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VOICE_PATH = os.path.join(BASE_DIR, "voice.md")

# Тоны: ключ приходит с фронтенда, значение — подсказка для нейросети
TONES = {
    "expert": "Экспертный: уверенно, по делу, с точными формулировками, без панибратства.",
    "friendly": "Дружелюбный: тёплый разговорный тон, обращение к читателю на «вы», просто о сложном.",
    "short": "Короткий: максимально сжато, 2 коротких абзаца, только самое важное.",
    "humor": "С юмором: лёгкая ирония и уместные шутки, но без клоунады и без искажения фактов.",
}

# Сколько символов статьи отдаём нейросети (чтобы не раздувать запрос)
MAX_SOURCE_CHARS = 8000
# Минимум текста, при котором считаем, что статья прочиталась
MIN_SOURCE_CHARS = 200


class UserError(Exception):
    """Ошибка, текст которой можно показать пользователю как есть."""


def read_voice():
    """Читаем правила оформления поста из voice.md (файл можно менять без кода)."""
    try:
        with open(VOICE_PATH, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        raise UserError("Не найден файл voice.md с правилами поста. Положите его рядом с app.py.")


def fetch_article(url):
    """Скачиваем страницу по ссылке и достаём из неё основной текст."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise UserError("Это не похоже на ссылку. Она должна начинаться с http:// или https://")

    headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsPostBot/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException:
        raise UserError(
            "Не удалось открыть ссылку. Сайт может быть недоступен или закрыт от ботов — "
            "попробуйте вкладку «Текстом» и вставьте текст новости."
        )

    # Подсказываем кодировку, чтобы кириллица не превратилась в кракозябры
    resp.encoding = resp.apparent_encoding if resp.encoding in (None, "ISO-8859-1") else resp.encoding
    soup = BeautifulSoup(resp.text, "html.parser")

    # Выкидываем всё, что точно не является текстом статьи
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else ""
    # Берём текст из абзацев; короткие обрывки (меню, кнопки) отбрасываем
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    body = "\n".join(p for p in paragraphs if len(p) > 40)

    if len(body) < MIN_SOURCE_CHARS:
        raise UserError(
            "Со страницы не получилось достать текст новости. "
            "Вставьте текст вручную на вкладке «Текстом»."
        )
    return title, body[:MAX_SOURCE_CHARS]


def build_messages(voice, tone_key, title, body, source_url):
    """Собираем сообщения для нейросети."""
    system = (
        "Ты — редактор, который пишет посты для ВКонтакте про нейросети и AI-сервисы.\n"
        "Следуй правилам оформления ниже. Пиши только по тексту источника, "
        "ничего не выдумывай.\n\n"
        f"=== ПРАВИЛА ПОСТА ===\n{voice}"
    )
    source_line = source_url if source_url else "не указан (в конце поста ссылку не добавляй)"
    user = (
        f"Тон поста: {TONES[tone_key]}\n"
        f"Ссылка на источник: {source_line}\n"
        f"Заголовок источника: {title or 'нет'}\n\n"
        f"Текст новости:\n{body}\n\n"
        "Напиши пост. Верни только готовый текст поста, без пояснений и markdown-разметки."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def get_client(timeout=60):
    """Создаём клиент Proxy API (OpenAI-совместимый) по настройкам из .env."""
    api_key = os.getenv("API_KEY")
    base_url = os.getenv("API_BASE_URL")
    if not (api_key and base_url and os.getenv("MODEL")):
        raise UserError("Не заполнен файл .env: нужны API_KEY, API_BASE_URL и MODEL (см. README).")
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)


def ask_model(messages):
    """Просим нейросеть написать текст."""
    client = get_client()
    resp = call_api(
        client.chat.completions.create, model=os.getenv("MODEL"), messages=messages, temperature=0.7
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise UserError("Нейросеть вернула пустой ответ. Попробуйте ещё раз.")
    return text


def call_api(func, **kwargs):
    """Вызываем метод API и превращаем любые ошибки в понятные сообщения."""
    try:
        return func(**kwargs)
    except openai.RateLimitError:
        raise UserError("Сервис нейросети сейчас перегружен или исчерпан лимит. Подождите пару минут и попробуйте снова.")
    except openai.AuthenticationError:
        raise UserError("API-ключ не подошёл. Проверьте API_KEY в файле .env.")
    except openai.PermissionDeniedError:
        raise UserError("У этого ключа нет доступа к выбранной модели или сервису. Проверьте ключ и MODEL в .env.")
    except openai.NotFoundError:
        raise UserError("Модель или адрес API не найдены. Проверьте MODEL и API_BASE_URL в файле .env.")
    except openai.APITimeoutError:
        raise UserError("Нейросеть слишком долго отвечает. Попробуйте ещё раз.")
    except openai.APIConnectionError:
        raise UserError("Не удалось подключиться к API. Проверьте интернет и API_BASE_URL в файле .env.")
    except openai.APIStatusError as e:
        print("Ошибка API:", repr(e), flush=True)  # подробности — в консоль для отладки
        raise UserError(f"Сервис нейросети вернул ошибку (код {e.status_code}). Попробуйте позже.")
    except Exception as e:
        print("Ошибка API:", repr(e), flush=True)
        raise UserError("Не удалось получить ответ от нейросети. Попробуйте ещё раз.")


# Понятные тексты для частых ошибок ВКонтакте (код ошибки -> сообщение)
VK_ERRORS = {
    5: "ВКонтакте не принял токен. Проверьте VK_TOKEN в файле .env.",
    9: "ВКонтакте ограничил частоту публикаций. Подождите немного и повторите.",
    14: "ВКонтакте просит капчу. Попробуйте опубликовать позже.",
    15: "Нет доступа к стене сообщества. Проверьте права ключа и VK_GROUP_ID.",
    100: "ВКонтакте не принял данные. Проверьте VK_GROUP_ID в файле .env.",
    214: "Не удалось добавить запись: возможно, превышен лимит публикаций или отложенных записей, "
         "время указано слишком близко/далеко или у ключа нет права на стену.",
    222: "ВКонтакте отклонил пост: в тексте есть запрещённая ссылка.",
}


def vk_settings():
    """Читаем настройки ВКонтакте из .env. Возвращаем (токен, id сообщества, версия API)."""
    token = os.getenv("VK_TOKEN")
    # Допускаем записи вида 123, -123, club123 и public123 — оставляем только число
    group_id = (os.getenv("VK_GROUP_ID") or "").strip().lower()
    for prefix in ("club", "public", "-"):
        group_id = group_id.removeprefix(prefix)
    version = os.getenv("VK_API_VERSION") or "5.199"
    if not (token and group_id.isdigit()):
        raise UserError("Не заполнены VK_TOKEN и VK_GROUP_ID (число) в файле .env, см. README.")
    return token, group_id, version


def vk_call(method, **params):
    """Вызываем метод API ВКонтакте и возвращаем поле response. Ошибки — понятным текстом."""
    token, _, version = vk_settings()
    params.update({"access_token": token, "v": version})
    try:
        result = requests.post(f"https://api.vk.com/method/{method}", data=params, timeout=20).json()
    except (requests.exceptions.RequestException, ValueError):
        raise UserError("Не удалось связаться с ВКонтакте. Проверьте интернет и попробуйте ещё раз.")

    if "error" in result:
        code = result["error"].get("error_code")
        print("Ошибка VK:", method, result["error"].get("error_msg"), flush=True)
        raise UserError(VK_ERRORS.get(code, f"ВКонтакте вернул ошибку (код {code}). Попробуйте позже."))
    return result["response"]


def parse_publish_date(value):
    """Превращаем время отложенной публикации (unix-секунды) в число. Пусто — публикуем сразу."""
    if not value:
        return None
    try:
        timestamp = int(value)
    except ValueError:
        raise UserError("Не удалось понять дату публикации. Выберите её ещё раз.")
    # ВКонтакте принимает только время в будущем; берём запас в пару минут
    if timestamp < time.time() + 120:
        raise UserError("Время публикации должно быть в будущем — минимум через 2–3 минуты.")
    return timestamp


def publish_to_vk(message, publish_date=None):
    """Публикуем пост на стене сообщества. Возвращаем ссылку.

    Если указан publish_date, ВКонтакте сам выложит пост в это время.
    """
    _, group_id, _ = vk_settings()
    params = {
        "owner_id": -int(group_id),  # у сообществ id отрицательный
        "from_group": 1,  # публикуем от имени сообщества
        "message": message,
    }
    if publish_date:
        params["publish_date"] = publish_date

    post_id = vk_call("wall.post", **params)["post_id"]
    return f"https://vk.com/wall-{group_id}_{post_id}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "url")
    tone = data.get("tone", "friendly")
    url = (data.get("url") or "").strip()
    text = (data.get("text") or "").strip()
    source = (data.get("source") or "").strip()  # необязательная ссылка в режиме «Текстом»

    try:
        if tone not in TONES:
            raise UserError("Выберите тон поста.")

        if mode == "url":
            if not url:
                raise UserError("Вставьте ссылку на новость.")
            title, body = fetch_article(url)
            source_url = url
        else:
            if not text:
                raise UserError("Вставьте текст новости.")
            if len(text) < 50:
                raise UserError("Текст слишком короткий — добавьте больше подробностей из новости.")
            title, body = "", text[:MAX_SOURCE_CHARS]
            source_url = source

        post = ask_model(build_messages(read_voice(), tone, title, body, source_url))
        return jsonify({"post": post})
    except UserError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/publish", methods=["POST"])
def publish():
    message = (request.form.get("post") or "").strip()
    try:
        if not message:
            raise UserError("Пост пустой — сначала сформируйте его.")
        publish_date = parse_publish_date(request.form.get("publish_date"))
        return jsonify({
            "url": publish_to_vk(message, publish_date),
            "scheduled": bool(publish_date),
        })
    except UserError as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    app.run(debug=True)
