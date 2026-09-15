"""在模型调用前，用程序规则决定角色能收到哪些事实。"""

from app.scene_data import KNOWN_ACTORS


def build_view(facts: list[dict], actor_id: str) -> list[dict[str, str]]:
    """只返回可见事实的 id/text；新建字典，不修改后台数据。"""
    if actor_id not in KNOWN_ACTORS:
        raise ValueError(f"未登记的角色：{actor_id}")

    visible = []
    for fact in facts:
        visibility = fact.get("visibility")
        known_by = fact.get("known_by")
        is_public = visibility == "public"
        is_known_private = (
            visibility == "private"
            and isinstance(known_by, list)
            and all(isinstance(actor, str) for actor in known_by)
            and actor_id in known_by
        )
        if is_public or is_known_private:
            # 只取模型需要的字段，不传播后台备注和权限名单。
            visible.append({"id": fact["id"], "text": fact["text"]})
    return visible
