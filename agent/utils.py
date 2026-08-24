import json
import urllib.request


def speech_to_text_yandex(file_path, folder_id, token_service):
    """Распознаёт речь из аудиофайла через Yandex SpeechKit STT."""
    with open(file_path, "rb") as f:
        data = f.read()

    params = "&".join([
        "topic=general",
        "folderId=%s" % folder_id,
        "lang=ru-RU",
    ])

    url = urllib.request.Request(
        "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize?%s" % params,
        data=data,
    )
    url.add_header("Authorization", "Bearer %s" % token_service.get_valid_token())

    response_data = urllib.request.urlopen(url).read().decode("UTF-8")
    decoded_data = json.loads(response_data)

    if decoded_data.get("error_code") is None:
        return decoded_data.get("result")
    return None
