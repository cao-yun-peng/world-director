# A04 源码与官方文档对照

核对日期：2026-09-15。这里只做静态阅读；没有安装或运行 smolagents、Langfuse。

## smolagents：两处源码

固定提交：`30bb1161095dbae2271e6bc3cc4c219cc3897a57`。

1. [MultiStepAgent._run_stream 与上限处理](https://github.com/huggingface/smolagents/blob/30bb1161095dbae2271e6bc3cc4c219cc3897a57/src/smolagents/agents.py)。输入任务和 max_steps，按步骤产生流式结果；得到终态或步数耗尽时退出。达到上限的分支会通过 `provide_final_answer` 再生成最终答案。本项目在上限处直接停止，所有发送必须在统一请求预算内。
2. [ToolCallingAgent 的工具执行与关联](https://github.com/huggingface/smolagents/blob/30bb1161095dbae2271e6bc3cc4c219cc3897a57/src/smolagents/agents.py)。工具结果保留调用 ID，多调用使用线程池与完成通知，之后整理观察。本项目只有已登记的只读工具，使用协程与共享 Semaphore；实际完成顺序写 trace，原调用顺序反馈模型。移动/给物继续走顺序裁定。

设计迁移以需求为依据，没有复制框架实现。源码中的一般工具并行策略不等于本世界中的写权限。

## Python 官方文档

- [任务取消](https://docs.python.org/3.11/library/asyncio-task.html#task-cancellation)：取消在协作点生效。清理使用 finally，结束后传播 CancelledError。
- [超时](https://docs.python.org/3.11/library/asyncio-task.html#timeouts)：本项目使用 `timeout_at` 维护绝对截止时间，单次调用另有较短等待上限。
- [gather](https://docs.python.org/3.11/library/asyncio-task.html#running-tasks-concurrently)：返回值按输入顺序排列。异常退出时，本项目仍显式取消并等待本批全部任务。
- [Semaphore](https://docs.python.org/3.11/library/asyncio-sync.html#semaphore)：获取名额可能需要等待；只有成功获取后才归还。它控制查询数量，不管理业务提交。

这些是官方 API 文档阅读，不算另外两处框架源码阅读。

## Langfuse 关联结构

[Observability Data Model](https://langfuse.com/docs/observability/data-model) 使用 trace、observation 和 session 组织一次请求、子操作与多轮交互。
本地 `RunTrace` 借鉴其关联思想，以 run_id 组织记录；它没有上传到 Langfuse，也没有提供远程平台能力。

## 本地 SDK 核验

项目已固定 `openai==2.50.0`。A04 新增 `AsyncRealModelAdapter`，保留原同步接口给 A01—A03。
`test_native_async_wire_retry_classification_and_close` 使用真实 SDK + 本地 MockTransport 验证原生 tool_calls、异步关闭和 `max_retries=0`。
`test_only_explicit_transient_status_codes_retry` 验证适配层分类；网络请求没有发往真实服务。

## 请自己解释

上游达到 max_steps 后仍生成一次答案，在本课的预算契约下为什么必须单独计费、计时？
完成顺序与反馈顺序不同，为什么不会让信封细节变成台灯细节？
