"""本人独立变式起点；不预填预测，不以工程测试代替独立成绩。"""
from pathlib import Path

PREDICTION = None
# 先保存五点预测，再复制 data/eval/a08_dev.jsonl 为新版本：
# 1. 将 09 的 actor_id 改为 other_npc，重新判断正向标签与指标分母。
# 2. 给 Fake 重排器注入一个从未给它的私有 chunk_id。
# 3. 验证拒绝层、回退榜单、世界归属、重放与加载后的调用数。
# 独立实现请写在此文件；参考测试位于 tests/test_a08_retrieval.py，
# 查阅参考前先记录自己的解释，不能把参考运行当作独立完成。


if __name__ == '__main__':
    print('本人预测尚未填写，独立验收 pending。' if PREDICTION is None else
          '已填写预测；继续完成变式代码与自己的解释，不据此自动评分。')
