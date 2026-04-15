from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from krok_helper.config import APP_NAME
from krok_helper.pipeline import DEFAULT_OFF_NAME_TEMPLATE, DEFAULT_ON_NAME_TEMPLATE, OUTPUT_NAME_MODE_FIXED


SETTINGS_FILE_NAME = "settings.json"


@dataclass
class AppSettings:
    output_name_mode: str = OUTPUT_NAME_MODE_FIXED
    on_name_template: str = DEFAULT_ON_NAME_TEMPLATE
    off_name_template: str = DEFAULT_OFF_NAME_TEMPLATE


def get_settings_path() -> Path:
    appdata = os.getenv("APPDATA")
    if os.name == "nt" and appdata:
        return Path(appdata) / APP_NAME / SETTINGS_FILE_NAME

    config_home = os.getenv("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / APP_NAME.lower().replace(" ", "-") / SETTINGS_FILE_NAME

    return Path.home() / ".config" / APP_NAME.lower().replace(" ", "-") / SETTINGS_FILE_NAME


def load_app_settings() -> AppSettings:
    path = get_settings_path()
    if not path.is_file():
        return AppSettings()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return AppSettings()

    if not isinstance(payload, dict):
        return AppSettings()

    return AppSettings(
        output_name_mode=str(payload.get("output_name_mode", OUTPUT_NAME_MODE_FIXED)),
        on_name_template=str(payload.get("on_name_template", DEFAULT_ON_NAME_TEMPLATE)),
        off_name_template=str(payload.get("off_name_template", DEFAULT_OFF_NAME_TEMPLATE)),
    )


def save_app_settings(settings: AppSettings) -> Path:
    path = get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
