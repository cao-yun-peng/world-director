# A03 源码阅读笔记

2026-09-15 在线核对固定提交 fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4。
这是 AI 阅读示范，不是学习者独立阅读记录；没有运行该外部项目。

## 一、环境存储与角色感知

来源：[maze.py](https://github.com/joonspk-research/generative_agents/blob/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/reverie/backend_server/maze.py)。

1. 输入：add_event_from_tile 接收事件和坐标；access_tile 接收坐标。
2. 输出／副作用：前者原地加入该格的事件集合、返回 None；后者返回该格数据字典。
3. 消费方：perceive 通过 access_tile 读取邻近环境。
4. 采用与不采用：采用环境与感知分开的职责；本课要求旧快照不变，因此不用原地修改环境的更新方式。

本地对照：[WorldState 与 adjudicate](../app/world.py)、[只读查询](../app/tools.py)。

## 二、环境怎样变成个人记忆

来源：[perceive.py](https://github.com/joonspk-research/generative_agents/blob/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/reverie/backend_server/persona/cognitive_modules/perceive.py)。

1. 输入：perceive 接收角色和环境。
2. 输出／副作用：按附近范围、所在区域和注意力数量筛选事件，检查近期重复，更新该角色记忆并返回新节点。
3. 消费方：Persona.move 把感知结果交给 retrieve，再把检索结果交给 plan。
4. 采用与不采用：采用“环境事实经过筛选进入个人记忆”；本课使用明确观察和权限检查，不把空间筛选当作安全授权。

调用顺序另见 [Persona.move](https://github.com/joonspk-research/generative_agents/blob/fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4/reverie/backend_server/persona/persona.py)。
本地对照：adjudicate 的 inspect 分支先读取授权描述，再生成 DiscoveryEvent 和观察者知识。

## 学习者填写

请合上参考答案，为上述两处各写四句话：输入、输出／副作用、消费方、采用与不采用。
重点解释：“世界里存在”为什么不等于“每个角色都知道”。
