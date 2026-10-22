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


# Размеры картинок: для поста — горизонтальная, для истории — вертикальная
IMAGE_SIZES = {"post": "1536x1024", "story": "1024x1536"}


def generate_image(post_text, kind):
    """Рисуем картинку по тексту поста. Возвращаем PNG в виде base64."""
    image_model = os.getenv("IMAGE_MODEL")
    if not image_model:
        raise UserError("В файле .env не указана IMAGE_MODEL (например, gpt-image-2).")
    client = get_client(timeout=180)

    # Сначала нейросеть-текстовик придумывает описание картинки по посту
    idea = ask_model([
        {"role": "system", "content": (
            "Ты арт-директор. По тексту поста напиши короткое описание иллюстрации "
            "для генератора картинок (2–3 предложения, на английском). Стиль: современная "
            "аккуратная иллюстрация или абстрактная композиция по теме поста. "
            "Строго без текста, букв, цифр, логотипов и лиц реальных людей на картинке. "
            "Верни только описание."
        )},
        {"role": "user", "content": post_text[:3000]},
    ])
    # Прокси игнорирует size и выбирает формат по описанию, поэтому формат пишем словами
    shape = "Vertical portrait 9:16 composition." if kind == "story" else "Horizontal 3:2 composition."
    prompt = f"{shape} {idea}\nNo text, no letters, no logos."

    resp = call_api(
        client.images.generate, model=image_model, prompt=prompt, size=IMAGE_SIZES[kind], n=1
    )
    b64 = getattr(resp.data[0], "b64_json", None)
    if not b64:
        raise UserError("Нейросеть не вернула картинку. Попробуйте ещё раз.")
    return b64


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


VK_ERRORS.update({
    7: "У ключа нет права на это действие. Создайте ключ с правами: стена, фотографии, истории.",
    27: "Этот метод ВКонтакте недоступен для ключа сообщества. Нужен ключ пользователя (VK_USER_TOKEN), см. README.",
    121: "ВКонтакте не принял загруженную картинку. Попробуйте другой файл.",
})

# Картинки: допустимые форматы и размер (в МБ)
ALLOWED_IMAGES = (".jpg", ".jpeg", ".png", ".gif")
MAX_IMAGE_MB = 10
app.config["MAX_CONTENT_LENGTH"] = (MAX_IMAGE_MB + 2) * 1024 * 1024


@app.errorhandler(413)
def too_big(_):
    return jsonify({"error": f"Файл слишком большой. Максимум {MAX_IMAGE_MB} МБ."}), 413


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


def vk_user_token():
    """Ключ пользователя (админа сообщества) для загрузки фото. Пустой — значит, не задан."""
    return (os.getenv("VK_USER_TOKEN") or "").strip() or None


def require_user_token():
    """Для поста с картинкой нужен ключ пользователя: не публикуем молча без картинки."""
    token = vk_user_token()
    if not token:
        raise UserError(
            "Для поста с картинкой нужен ключ пользователя (VK_USER_TOKEN), см. README. "
            "Можно убрать картинку и опубликовать только текст."
        )
    return token


def vk_call(method, token=None, **params):
    """Вызываем метод API ВКонтакте и возвращаем поле response. Ошибки — понятным текстом.

    По умолчанию используется ключ сообщества; для загрузки фото передаём ключ пользователя.
    """
    community_token, _, version = vk_settings()
    token = token or community_token
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


def read_image(file):
    """Проверяем загруженную картинку. Возвращаем (имя, байты) или None, если файла нет."""
    if file is None or not file.filename:
        return None
    name = file.filename.lower()
    if not name.endswith(ALLOWED_IMAGES):
        raise UserError("Подходят только картинки JPG, PNG или GIF.")
    data = file.read()
    if not data:
        raise UserError("Файл пустой. Выберите другую картинку.")
    if len(data) > MAX_IMAGE_MB * 1024 * 1024:
        raise UserError(f"Картинка слишком большая. Максимум {MAX_IMAGE_MB} МБ.")
    return name, data


def upload_to_vk(upload_url, field, image):
    """Отправляем картинку на сервер загрузки ВКонтакте и возвращаем его ответ."""
    name, data = image
    try:
        return requests.post(upload_url, files={field: (name, data)}, timeout=60).json()
    except (requests.exceptions.RequestException, ValueError):
        raise UserError("Не удалось загрузить картинку во ВКонтакте. Попробуйте ещё раз.")


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


def publish_to_vk(message, image=None, publish_date=None):
    """Публикуем пост на стене сообщества (с картинкой, если она есть). Возвращаем ссылку.

    Если указан publish_date, ВКонтакте сам выложит пост в это время.
    """
    _, group_id, _ = vk_settings()
    # Проверяем ключ до любых запросов, чтобы не отправить пост без картинки
    user_token = require_user_token() if image else None
    params = {
        "owner_id": -int(group_id),  # у сообществ id отрицательный
        "from_group": 1,  # публикуем от имени сообщества
        "message": message,
    }
    if publish_date:
        params["publish_date"] = publish_date

    if image:
        # 1) получаем адрес загрузки, 2) грузим файл, 3) сохраняем фото на стене
        server = vk_call("photos.getWallUploadServer", token=user_token, group_id=group_id)
        up = upload_to_vk(server["upload_url"], "photo", image)
        saved = vk_call(
            "photos.saveWallPhoto", token=user_token, group_id=group_id,
            photo=up.get("photo"), server=up.get("server"), hash=up.get("hash"),
        )
        params["attachments"] = f"photo{saved[0]['owner_id']}_{saved[0]['id']}"

    post_id = vk_call("wall.post", **params)["post_id"]
    return f"https://vk.com/wall-{group_id}_{post_id}"


def publish_story(image):
    """Публикуем историю сообщества из картинки. Возвращаем ссылку (или None)."""
    _, group_id, _ = vk_settings()
    # Истории: берём ключ пользователя, если он задан; иначе ключ сообщества, как раньше
    user_token = vk_user_token()
    server = vk_call("stories.getPhotoUploadServer", token=user_token, add_to_news=1, group_id=group_id)
    up = upload_to_vk(server["upload_url"], "file", image)
    result = up.get("response", {}).get("upload_result")
    if not result:
        raise UserError("ВКонтакте не принял картинку для истории. Попробуйте другой файл.")
    saved = vk_call("stories.save", token=user_token, upload_results=result)
    items = saved.get("items") or []
    if items:
        return f"https://vk.com/story{items[0]['owner_id']}_{items[0]['id']}"
    return None


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


@app.route("/generate-image", methods=["POST"])
def generate_image_route():
    data = request.get_json(silent=True) or {}
    post_text = (data.get("post") or "").strip()
    kind = data.get("kind") if data.get("kind") in IMAGE_SIZES else "post"
    try:
        if not post_text:
            raise UserError("Сначала сформируйте пост — картинка рисуется по его тексту.")
        return jsonify({"image": generate_image(post_text, kind)})
    except UserError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/publish", methods=["POST"])
def publish():
    # Данные приходят формой: текст поста и (необязательно) картинка
    message = (request.form.get("post") or "").strip()
    try:
        if not message:
            raise UserError("Пост пустой — сначала сформируйте его.")
        publish_date = parse_publish_date(request.form.get("publish_date"))
        image = read_image(request.files.get("image"))
        return jsonify({
            "url": publish_to_vk(message, image, publish_date),
            "scheduled": bool(publish_date),
        })
    except UserError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/story", methods=["POST"])
def story():
    try:
        image = read_image(request.files.get("image"))
        if image is None:
            raise UserError("Выберите картинку для истории.")
        return jsonify({"url": publish_story(image)})
    except UserError as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    app.run(debug=True)
