import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import requests


class TokenManager:
    """Менеджер IAM-токенов Yandex Cloud на основе OAuth-токена."""

    def __init__(self, oauth_token: str, token_file: str = "tokens.json"):
        self.oauth_token = oauth_token
        self.token_file = token_file
        self.tokens = self._load_tokens()
        self.iam_url = "https://iam.api.cloud.yandex.net/iam/v1/tokens"

    def _load_tokens(self) -> Dict[str, Any]:
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_tokens(self) -> None:
        try:
            with open(self.token_file, "w", encoding="utf-8") as f:
                json.dump(self.tokens, f, indent=2)
        except OSError as e:
            print(f"Ошибка сохранения токенов: {e}")

    def _get_new_iam_token_via_api(self) -> Optional[str]:
        headers = {"Content-Type": "application/json"}
        data = {"yandexPassportOauthToken": self.oauth_token}
        try:
            response = requests.post(self.iam_url, headers=headers, json=data, timeout=30)
            response.raise_for_status()
            response_data = response.json()
            iam_token = response_data.get("iamToken")
            if iam_token:
                return iam_token
            print(f"IAM токен не найден в ответе API: {response_data}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"Ошибка API запроса для получения IAM токена: {e}")
            return None

    def _is_token_expired(self) -> bool:
        if "expires_at" not in self.tokens:
            return True
        try:
            expires_at = datetime.fromisoformat(self.tokens["expires_at"])
            return datetime.now() >= (expires_at - timedelta(minutes=5))
        except (ValueError, KeyError):
            return True

    def _refresh_token(self) -> Optional[str]:
        print("Обновление IAM токена через API...")
        new_token = self._get_new_iam_token_via_api()
        if new_token:
            expiration_time = datetime.now() + timedelta(hours=12)
            self.tokens = {
                "access_token": new_token,
                "expires_at": expiration_time.isoformat(),
                "last_updated": datetime.now().isoformat(),
                "oauth_token_used": self.oauth_token[:8] + "...",
            }
            self._save_tokens()
            print("IAM токен успешно обновлен через API")
            return new_token
        return None

    def get_valid_token(self) -> Optional[str]:
        if "access_token" in self.tokens and not self._is_token_expired():
            if self.tokens.get("oauth_token_used", "").startswith(self.oauth_token[:8]):
                return self.tokens["access_token"]
            print("Обнаружен измененный OAuth токен, требуется обновление IAM токена")
        return self._refresh_token()
