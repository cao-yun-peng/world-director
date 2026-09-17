"""A06 独立工具契约：行动附简短理由与已授权来源，兼容旧 memory schema。"""

import json
from copy import deepcopy

from app.turn_tools import MEMORY_TOOL_SCHEMAS, parse_memory_terminal


SCENE_TERMINALS = {'end_turn', 'move', 'give', 'whisper', 'wait'}
SCENE_TOOL_SCHEMAS = deepcopy(MEMORY_TOOL_SCHEMAS)
for _tool in SCENE_TOOL_SCHEMAS:
    if _tool['function']['name'] in SCENE_TERMINALS:
        _params = _tool['function']['parameters']
        _params['properties'].update({
            'reason': {'type': 'string', 'minLength': 1, 'maxLength': 200,
                       'description': '简短决策摘要，不是内部思维链，也不是成功证明。'},
            'source_refs': {'type': 'array', 'minItems': 0, 'maxItems': 6,
                            'items': {'type': 'string'},
                            'description': '依据的已提交可见经历 event_id；不引用本轮私语候选 ID。'},
        })
        _params['required'] += ['reason', 'source_refs']

SCENE_INSTRUCTIONS = '''
本轮属于受控场景。结合自己的目标、授权场景、已提交经历选择下一步。
每个终结工具必须附 reason（200 字符内的简短理由）和 source_refs（最多 6 个已提交可见事件 ID，没有时用空列表）。
本轮玩家私语候选来源尚未提交，不能作为 source_refs。理由是待核验解释，不是行动成功证据。
若已收到授权的暂缓选择，应核对、澄清或 wait，不强行执行该计划；收到失败回执不能声称目标已经完成。
'''


def parse_scene_terminal(call, allowed_refs):
    try:
        arguments = json.loads(call['function']['arguments'])
    except json.JSONDecodeError:
        raise ValueError('工具参数必须是 JSON 对象。') from None
    if not isinstance(arguments, dict):
        raise ValueError('工具参数必须是 JSON 对象。')
    reason, refs = arguments.get('reason'), arguments.get('source_refs')
    if (not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 200
            or not isinstance(refs, list) or not 0 <= len(refs) <= 6
            or not all(isinstance(ref, str) and ref in allowed_refs for ref in refs)
            or len(set(refs)) != len(refs)):
        raise ValueError('决策摘要或来源不可用。')
    stripped = deepcopy(call)
    stripped['function']['arguments'] = json.dumps(
        {key: value for key, value in arguments.items() if key not in ('reason', 'source_refs')},
        ensure_ascii=False)
    proposal = parse_memory_terminal(stripped)
    return proposal, {'reason': reason.strip(), 'source_refs': list(refs), 'verified': False}
