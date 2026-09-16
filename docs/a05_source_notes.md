# A05｜Generative Agents 源码对照

2026-09-16 重新静态读取固定提交 `fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4`；没有安装或执行该项目。以下为 AI 辅助阅读笔记，学习者独立解释待完成。

## retrieve.py：new_retrieve

[固定源码](https://github.com/joonspk-research/generative_agents/blob/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/reverie/backend_server/persona/cognitive_modules/retrieve.py#L183)。

- 输入：persona、focal_points、n_count。候选来自该 persona 的事件与思考，排除 idle 节点。
- 处理：按 last_accessed 升序；近期、重要、相关三项分别归一化后加权。相关性消费 embedding。
- 输出/副作用：每个关注点对应选中节点；选中节点的 last_accessed 更新为当前时间。
- 消费方：converse 中关系/想法摘要等调用者。

手算：假设衰减系数 0.9，较旧访问节点排前、指数为 1，得 0.9；较新排后、指数为 2，得 0.81。该代码方向并不符合“越新越高”的直觉，不能仅按函数名判断。此处是对固定代码与假设参数的手算，不是运行结果。

采用按问题选取部分经历；本课先做授权，再按关键词/版本排序。不照搬固定权重，也不引入 embedding。

## converse.py：agent_chat_v1

[固定源码](https://github.com/joonspk-research/generative_agents/blob/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/reverie/backend_server/persona/cognitive_modules/converse.py#L69)。

- 输入：maze、发起 persona、目标 persona。
- 处理：交换双方位置，分别检索各自记忆，构建关系摘要，再据关系和对方行动检索并形成想法摘要。
- 输出：最终对话生成函数消费双方摘要，返回生成对话。
- 副作用：底层检索更新访问时间，摘要/对话路径调用模型；本次只做静态阅读。

采用“先从各角色自己的经历取材料”。本课一次请求只扮演一个角色，不把双方私密摘要共同放进该角色请求，也不自动唤醒所有角色。

## 用本项目解释取舍

甲持有随机暗号，乙问“交接暗号是什么”。即使甲的记录相关性最高，它也不能进入乙的候选。等程序接受转述 E2，乙才从自己的 reported E2 检索。E2 的 cause 指向 E1 只供后台查验，不意味着乙获准读取 E1 的额外话。

独立阅读交付：自己画出这两个函数的输入、消费者与副作用；重算 0.9 的两个指数；解释为什么把权限筛选放在模型摘要之后已经太晚。
