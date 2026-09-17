# A06 源码对照参考

2026-09-17。AI 静态阅读参考，不是学习者独立复述。固定上游提交 `fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4`；本轮未安装、运行上游，也未调用其模型。浏览器的文本行号可能与 GitHub 原文件显示不同，以下以函数名定位。

## 计划与事件反应

来源：[plan.py](https://github.com/joonspk-research/generative_agents/blob/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/reverie/backend_server/persona/cognitive_modules/plan.py)。阅读 `plan`、`_determine_action`、`_should_react` 和反应路径。

输入包括当前人物、地图、人物集合、新日标记与检索结果。新日触发长期规划；当前动作结束才选择下一动作；有聚焦事件时再判断聊天或等待。相关函数会写入人物的 scratch，返回的动作地址由后续执行使用。聊天缓冲抑制立即重复聊天。

本项目取“新事件使下一步需要重查”的思路，使用可失效的近两步计划；不引入全天日程和全体人物上下文共享。与上游不同，本项目计划本身不能修改位置或归属。反例是周澈离场后旧交接计划失效，给物仍必须经过裁定器。

## 反思与证据

来源：[reflect.py](https://github.com/joonspk-research/generative_agents/blob/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/reverie/backend_server/persona/cognitive_modules/reflect.py)。阅读 `generate_insights_and_evidence`、`run_reflect`、`reflection_trigger`。

生成函数把模型给出的节点序号映射成记忆节点 ID；调用方把想法与证据写入关联记忆。触发器检查重要性计数和是否存在记忆。生成函数的异常回退包含占位引用，因此不能把“结构里有一个 ID”当作来源可靠的证明。

本项目借鉴来源关联，先校验当前角色有权引用的已提交事件，再保存简短理由；不连续调用反思模型。引用合法仍不证明理由正确，日志标记 verified=false。反例是台词或理由声称交接完成，但缺少成功转移和当前归属条件时，线程与结局均不能据此变成成功。

## 留给学习者的独立复述

1. 没有新日、动作未结束、没有聚焦事件时，上游哪些重新决策路径不会执行？哪些清理逻辑仍可能执行？
2. 对照本项目 `Director.invalidate` 与异地练习，解释“计划失效”为什么不撤销已经发生的移动。
3. 假设甲只能引用 E1、E3，乙还拥有私语 E2，写出甲的允许引用集合。说明有引用为何仍不能直接证明结局。

尚未收到学习者对这三题的独立回答，不计入学习成绩。
