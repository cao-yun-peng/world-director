"""先预测异地失败，再修改 MOVE_FIRST，自己解释事件链。"""

import asyncio
import json
from scripts.a05_demo import receiver_variant

# 第一次保持 False，预期 NOT_COLOCATED 且没有新的接收记忆。
# 第二次自己改为 True：丙通过正常 move 到值班室，然后接收甲的私语。
MOVE_FIRST = False

if __name__ == '__main__':
    print(json.dumps(asyncio.run(receiver_variant(move_first=MOVE_FIRST)), ensure_ascii=False, indent=2))
