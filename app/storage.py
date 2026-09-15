"""JSON存档：验证后写临时文件，再替换正式文件。"""

import json
import os
import tempfile
from pathlib import Path

from app.session import validate_session


def save_session(session: dict, path: Path, *, expected_actor_id: str) -> None:
    validate_session(session, expected_actor_id=expected_actor_id)
    text = json.dumps(session, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_session(path: Path, *, expected_actor_id: str) -> dict:
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError("存档不是有效的 UTF-8 JSON。") from error
    validate_session(session, expected_actor_id=expected_actor_id)
    return session
