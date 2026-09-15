"""会话只保存成功历史；每轮从可信配置重新生成 system。"""

from uuid import uuid4

from app.character import BASE_CARD, GOALS, PROMPT_VERSION, build_prompt
from app.model import ModelAdapter
from app.scene_data import FACTS
from app.view import build_view

# other_npc只是权限测试主体，目前没有可供对话的角色卡。
ACTOR_CARDS = {"lin_yan": BASE_CARD}


def validate_session(session: dict, *, expected_actor_id: str) -> None:
    """校验本版本的存档契约；也在保存和构建消息时复用。"""
    fields = {"schema_version", "session_id", "actor_id", "goal_id", "prompt_version", "history"}
    if not isinstance(session, dict) or set(session) != fields:
        raise ValueError("会话字段缺失或含有不支持的字段。")
    if type(session["schema_version"]) is not int or session["schema_version"] != 1:
        raise ValueError("不支持的会话结构版本。")
    for field in ("session_id", "actor_id", "goal_id", "prompt_version"):
        if not isinstance(session[field], str) or not session[field].strip():
            raise ValueError(f"会话字段 {field} 必须是非空字符串。")
    if session["actor_id"] != expected_actor_id or session["actor_id"] not in ACTOR_CARDS:
        raise ValueError("存档角色与当前角色不匹配或尚不支持对话。")
    if session["goal_id"] not in GOALS:
        raise ValueError("未知目标。")
    if session["prompt_version"] != PROMPT_VERSION:
        raise ValueError("提示词版本不匹配；本次不支持自动迁移。")
    history = session["history"]
    if not isinstance(history, list) or len(history) % 2:
        raise ValueError("历史必须包含完整的 user/assistant 回合。")
    for index, message in enumerate(history):
        role = "user" if index % 2 == 0 else "assistant"
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] != role or not isinstance(message["content"], str)
                or not message["content"].strip()):
            raise ValueError("历史消息必须是交替的 user/assistant 和非空字符串。")


def create_session(*, actor_id: str, goal_id: str) -> dict:
    session = {
        "schema_version": 1, "session_id": str(uuid4()), "actor_id": actor_id,
        "goal_id": goal_id, "prompt_version": PROMPT_VERSION, "history": [],
    }
    validate_session(session, expected_actor_id=actor_id)
    return session


def build_messages(session: dict, user_text: str) -> list[dict[str, str]]:
    validate_session(session, expected_actor_id=session.get("actor_id"))
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("请输入非空文字。")
    card = {**ACTOR_CARDS[session["actor_id"]], "goal": GOALS[session["goal_id"]]}
    view = build_view(FACTS, session["actor_id"])
    messages = [{"role": "system", "content": build_prompt(card, view)}]
    for message in session["history"]:
        messages.append(message.copy())
    messages.append({"role": "user", "content": user_text})
    return messages


def run_turn(session: dict, user_text: str, model: ModelAdapter) -> tuple[dict, str]:
    messages = build_messages(session, user_text)
    reply = model.generate(messages)
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("模型没有返回有效文本。")
    reply = reply.strip()
    # 只有成功才提交，而且新旧会话不共享可变的历史字典。
    history = [message.copy() for message in session["history"]]
    history.extend([{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}])
    return {**session, "history": history}, reply
