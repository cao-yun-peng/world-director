"""D003 教学后台资料；校验词和标记均为虚构测试数据。"""

KNOWN_ACTORS = {"lin_yan", "other_npc"}

FACTS = [
    {
        "id": "F_PUBLIC",
        "text": "值班室桌上放着一盏台灯。",
        "visibility": "public",
        "known_by": [],
        "internal_note": "后台测试标记：鹭羽-3841。",
    },
    {
        "id": "F_LIN",
        "text": "林砚知道备用抹布放在值班室抽屉里。",
        "visibility": "private",
        "known_by": ["lin_yan"],
    },
    {
        "id": "F_OTHER",
        "text": "封存盒校验词是杉木-7291。",
        "visibility": "private",
        "known_by": ["other_npc"],
    },
    {
        "id": "F_UNASSIGNED",
        "text": "未开放剧情标记为晚潮-6158。",
        "visibility": "private",
        "known_by": [],
    },
]
