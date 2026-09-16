"""有限对照 required/auto 的原生工具响应；不执行查询或终结工具。"""

import argparse
import asyncio
import json
from copy import deepcopy

from app.async_runtime import LOOP_INSTRUCTIONS, validate_completion
from app.execution import RunStopped
from app.loop_cli import load_model
from app.session import create_session
from app.tools import ModelProtocolError, validate_batch
from app.turn_tools import TERMINAL_TOOLS, TURN_TOOL_SCHEMAS, parse_terminal_call
from app.world import create_world
from app.world_runtime import visible_messages


def probe_messages():
    """用户报告中的虚构角色对话；固定夹具，不读取其他聊天、文件或世界存档。"""
    session = create_session(actor_id="lin_yan", goal_id="clarify")
    exchanges = [
        ("你好", "你好。我是林砚，灯塔的临时管理员。"),
        ("为什么是临时管理员", "上一任管理员离开得突然，没人接替，我就先顶着。灯塔不能没人照看。"),
        ("也是，这里有抹布吗", "你是指清洁用的抹布？还是……有别的用途？"),
        ("哎，这里怎么有封信", "信在哪里？我还没看见。"),
        ("你可以把这个东西给我吗，这不就在桌子上", "桌子上有几样东西，你能告诉我具体是哪一封？信封颜色、大小，或者有没有署名？"),
        ("啊" * 40, "你没事吧？需要我帮忙吗？"),
    ]
    for question, answer in exchanges:
        session["history"].extend([{"role": "user", "content": question},
                                   {"role": "assistant", "content": answer}])
    wire = visible_messages(session, "我是谁", actor_id="lin_yan",
                            world=create_world(session["session_id"]), include_objects=False)
    wire[0]["content"] += LOOP_INSTRUCTIONS
    return wire


async def probe(model, max_requests):
    wire = probe_messages()
    rows = []
    try:
        for index in range(max_requests):
            choice = "required" if index % 2 == 0 else "auto"
            options = {"tools": TURN_TOOL_SCHEMAS, "tool_choice": choice}
            row = {"sample": index + 1, "profile": choice}
            try:
                async with asyncio.timeout(15):
                    completion = await model.complete(deepcopy(wire), **options)
                message = validate_completion(completion)
                calls = message.get("tool_calls")
                if not calls:
                    raise RunStopped("TOOL_CALL_REQUIRED")
                validate_batch(calls)
                terminal = [call for call in calls if call["function"]["name"] in TERMINAL_TOOLS]
                if terminal:
                    if len(calls) != 1:
                        raise RunStopped("TERMINAL_TOOL_CONFLICT")
                    try:
                        parse_terminal_call(terminal[0])
                    except ValueError:
                        row.update(outcome="INVALID_ARGUMENTS")
                    else:
                        row.update(outcome="terminal_not_executed", tool_name=terminal[0]["function"]["name"])
                else:
                    row.update(outcome="tool_calls_not_executed", call_count=len(calls))
            except ModelProtocolError:
                row.update(outcome="MODEL_PROTOCOL_ERROR")
            except RunStopped as error:
                row.update(outcome=error.code)
            except TimeoutError:
                row.update(outcome="REQUEST_TIMEOUT")
            except Exception:
                # 外部服务异常正文可能包含敏感信息；不写入对照结果。
                row.update(outcome="MODEL_REQUEST_FAILED")
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        await model.aclose()
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="向已配置的模型服务发送固定虚构对话")
    parser.add_argument("--max-model-requests", type=int, default=4)
    args = parser.parse_args(argv)
    if not args.real:
        parser.error("真实对照必须显式传 --real；会发送本脚本中的固定虚构对话。")
    if not 1 <= args.max_model_requests <= 6:
        parser.error("对照请求上限为 1—6。SDK 重试关闭，每项只发送一次。")
    asyncio.run(probe(load_model(), args.max_model_requests))


if __name__ == "__main__":
    main()
