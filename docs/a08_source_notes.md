# A08 源码对照（AI 参考，不是本人复述）

本轮未安装上游框架。本地实现位置为 app/retrieval.py 的 reciprocal_rank_fusion、
app/reranking.py 的 rerank，以及 app/scene_save.py 的 restore_story。

## RRF

计划指定 [LangChain weighted_reciprocal_rank](https://github.com/langchain-ai/langchain/blob/5c1f28271295bb13034f4cf8964f74c117357d40/libs/langchain/langchain_classic/retrievers/ensemble.py#L304)。
本轮尝试读取固定 GitHub/raw 链接均返回 Cache miss，未伪称重新核验成功。
公式与输入输出依据用户已提供的 A08 计划：各路名次贡献求和，合并同一身份。

本地纯函数输入 ID 榜单，输出 chunk_id、融合分与各路名次，无外部请求和世界副作用。
采用稳定 chunk_id；额外先在单路去重、前置受众交集；不按正文合并身份。
由 LoreRetriever 消费融合结果，再经过重排、top_k 和装包。
并列按 chunk_id，空榜返回 []。本轮上游静态复核仍欠一次可访问的固定源码证据。

## Generative Agents

本轮成功静态读取 [固定 retrieve.py](https://raw.githubusercontent.com/joonspk-research/generative_agents/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/reverie/backend_server/persona/cognitive_modules/retrieve.py)。
new_retrieve 输入 persona、关注点和数量，返回每个关注点的记忆节点。
它按 last_accessed 排序，以列表位置计算 recency；相关性调用 embedding。
入选后更新 last_accessed，并打印中间分值。normalize_dict_floats 会原地改字典，
空字典调用 min/max 会失败；全同分走特殊分支。

采用“显式选择依据和记录失败”的思路，不复制权重、不将访问时间写入只读设定检索。
本地只读检索的副作用仅是受控请求、计算缓存与诊断；世界事实不变。

本人练习：分别写输入、输出、副作用、下游消费方、采用与不采用各一条。
原预测与解释留在 a08_prediction.md；此处不能计入独立分数。
