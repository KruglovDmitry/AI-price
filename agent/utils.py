import json
import urllib.error
import urllib.parse
import urllib.request


def speech_to_text_yandex(
    file_path: str,
    *,
    api_key: str | None = None,
    folder_id: str | None = None,
    iam_token: str | None = None,
) -> str | None:
    """Распознаёт речь из аудиофайла через Yandex SpeechKit STT.

    Предпочтителен API-ключ сервисного аккаунта: заголовок ``Api-Key``,
    идентификатор каталога не передаётся. OAuth/IAM оставлен как запасной путь.
    """
    with open(file_path, "rb") as f:
        data = f.read()

    params = {"topic": "general", "lang": "ru-RU"}
    if api_key:
        auth = f"Api-Key {api_key}"
    elif iam_token and folder_id:
        auth = f"Bearer {iam_token}"
        params["folderId"] = folder_id
    else:
        raise RuntimeError("Yandex STT: не задан API-ключ и нет IAM-токена")

    url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize?" + urllib.parse.urlencode(
        params
    )
    request = urllib.request.Request(url, data=data)
    request.add_header("Authorization", auth)

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_data = response.read().decode("UTF-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("UTF-8", errors="replace")[:500]
        raise RuntimeError(f"Yandex STT HTTP {exc.code}: {body}") from exc

    decoded_data = json.loads(response_data)
    if decoded_data.get("error_code") is None:
        return decoded_data.get("result")
    return None
