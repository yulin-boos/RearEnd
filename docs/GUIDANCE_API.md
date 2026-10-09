# 植物问题引导接口

实施日期：2026-10-09，后端版本 0.8.0。

后端结合保存的作物/病害候选、置信度、近期已接受的聊天和已回答的信息，返回当前分析、下一步建议及最多 3 个需要补充的问题。回答后使用同一接口继续分析，更新下一轮问题。支持图片识别会话和纯文字会话。

## 1. 生成第一轮问题

图片识别时推荐使用 `auto_analyze=false`，将识别响应的 `request_id` 作为 `recognition_id`。纯文字咨询使用 `POST /api/v1/chat/sessions` 返回的 `session_id`。

```http
POST /api/v1/chat/guidance
Content-Type: application/json
```

```json
{
  "recognition_id": "0123456789abcdef0123456789abcdef"
}
```

首次请求可选传入 `message`，描述已经观察到的信息。候选、分数、历史和检查结果均从后端读取，客户端不能覆盖这些字段。

成功响应的核心字段示意如下；实际响应还包含 `confidence`、`guard`、`usage`、`elapsed_ms` 和 `knowledge`：

```json
{
  "recognition_id": "0123456789abcdef0123456789abcdef",
  "revision": 1,
  "stale": false,
  "status": "needs_information",
  "analysis": "苹果病害是图像候选，需要结合实际作物和症状核实。",
  "advice": ["观察叶片正反面，记录斑点变化。"],
  "questions": [
    {
      "id": "crop",
      "question": "实际种植的是什么作物？",
      "reason": "确认候选作物是否适用。",
      "options": ["苹果", "马铃薯"]
    },
    {
      "id": "symptoms",
      "question": "叶片主要有哪些症状？",
      "reason": "区分病害候选、虫害和环境问题。",
      "options": ["褐色斑点", "发黄", "卷曲"]
    }
  ],
  "answered_information": {},
  "model": "deepseek-flash",
  "history_length": 2
}
```

问题按优先顺序排列。`options` 可以为空，前端始终允许自由填写答案；无需强制用户回答全部问题。

## 2. 提交补充信息并更新问题

把上次响应中的 `revision` 原样带回。`answers` 的键对应本轮 `questions[].id`，可以只回答其中一部分；其他信息或作物纠正通过 `message` 提交。

```json
{
  "recognition_id": "0123456789abcdef0123456789abcdef",
  "revision": 1,
  "answers": {
    "crop": "苹果",
    "symptoms": "叶片出现少量褐色斑点"
  },
  "message": "最近连续下雨，盆土比较湿。"
}
```

后端结合补充信息重新分析，返回新 `revision`、更新的 `analysis`、`advice` 和 `questions`。`answered_information` 保存用户明确回答的内容；从自由补充和新增普通聊天提取的信息必须逐字引用用户原文，不能从模型候选、助手回复或参考知识生成观察。

已回答的字段不会再次作为同一项问题返回，包括回答“不清楚”的字段。还未回答的问题可以继续出现；有必要时改为询问其他可观察信息。支持后续通过 `message` 纠正先前信息，本轮显式 `answers` 不会被模型提取结果覆盖。

`status=needs_information` 表示仍有追问；`status=advice_ready` 表示当前已给出下一步建议，`questions=[]`。后一状态不表示已经确诊，也不改变原图像模型的分数。

## 3. 恢复当前引导状态

```http
GET /api/v1/chat/guidance/0123456789abcdef0123456789abcdef
```

页面重新打开或后端重启后，可获取已保存的分析、问题、补充信息和当前会话版本。此请求不调用模型。

如果期间使用普通 `/api/v1/chat` 更新了会话，GET 返回 `stale=true` 和当前 `revision`，分析/问题仍是上次引导快照。先 POST 同一个 `recognition_id` 和当前 `revision`，不带 `answers`，刷新引导后再回答。刷新会吸收新增普通聊天中的用户描述。

当引导仍是最新版本时，不带 `answers` 或 `message` 的重复 POST 返回已有结果，不增加对话轮次、不重复调用模型。发生 409 时获取当前状态并刷新，不要继续重发旧版本的回答。

## 请求限制与响应字段

| 字段 | 说明 |
| --- | --- |
| `recognition_id` | 32 位小写十六进制会话 ID |
| `revision` | 当前会话版本，回答时必填；已有引导时自由补充也必填 |
| `answers` | 本轮已提出问题的回答，每项 1–1000 字符，不能提交未提出的字段 |
| `message` | 可选自由补充或纠正，1–2000 字符 |

`answers` 和 `message` 的总长度上限为 4000 字符，空白文本、未知字段以及客户端传入候选或检查分数均返回 422。

| 问题 ID | 信息 |
| --- | --- |
| `crop` | 实际作物 |
| `symptoms` | 可见症状 |
| `onset` | 出现时间 |
| `spread` | 扩散情况 |
| `affected_parts` | 受影响部位 |
| `watering` | 浇水与土壤湿度 |
| `fertilizing` | 近期施肥或用药 |
| `environment` | 种植环境 |
| `pests` | 虫体、虫卵、蛛网等 |

`confidence` 提供原 `top_confidence`、`threshold`、`is_confident`、前两候选的 `candidate_gap`、按作物归组的 `crop_candidates` 和 `uncertainty_reasons`。作物分数取保留候选中该作物的最高类别分数，未相加或重新归一化，不代表作物确认概率。

低置信度或不同作物候选接近时优先确认作物；否则优先核实症状。前两类别分差或前两作物分差不大于 0.1 时标记 `close_candidates` 或 `different_crops`。这些是追问排序策略，未经诊断准确率校准。缺少实际作物或症状时，后端保证提出相应问题。纯文字咨询标记 `no_image_result`，图像分数字段为空。

## 权限、存储和模型调用

登录创建的会话继续要求 `Authorization: Bearer <token>`，其他用户及匿名请求无法访问，返回 404；匿名会话沿用现有随机 ID 访问方式。有效期与原会话一致，成功生成一轮引导会续期。

使用当前已配置的 DeepSeek 和 Jev。输入和返回给前端的分析、建议、问题、原因及选项均经过原话题检查。检索知识时优先使用实际报告的作物和症状，并通过 Jev 匹配；结构化引导由 DeepSeek 生成，即使已有知识可以直接回答普通问答，也不会跳过引导生成。引导轮次不自动整理为长期知识，`knowledge.status=skipped`，`source_ids` 记录实际使用的参考。

新生成一轮通常调用一次 DeepSeek、两次 Jev；知识检索存在候选时还会进行一次 Jev 匹配。GET 和最新状态的重复空 POST 不调用这些服务。`elapsed_ms` 记录生成和检查阶段的耗时。

无需修改现有 12 张表。引导状态保存在 `conversations.context_snapshot.guidance`，本轮完整问答、模型用量和检查结果保存在 `chat_messages`；二者在同一事务中更新。正常聊天也能读取已补充的信息。引导回答独立保留，不随最近聊天历史的裁剪丢失；会话过期后一起清理。SQLite 离线实现采用相同的原子保存规则。

同一会话的引导和普通聊天共用生成锁；数据库写入仍校验用户、有效期和版本，跨进程并发不会覆盖新状态。模型超时、无效 JSON、截断、未提供任何下一步问题/建议、缺乏用户原文依据或 Jev 拒绝时，整轮不保存，已有引导和回答保留。

## 常见错误

| HTTP | 错误码 | 处理 |
| --- | --- | --- |
| 404 | `conversation_not_found` | 会话不存在、过期或无访问权限 |
| 404 | `guidance_not_found` | 首次调用 POST 生成问题 |
| 409 | `chat_busy` | 等待当前会话生成结束 |
| 409 | `conversation_changed` | 获取当前版本后刷新 |
| 409 | `guidance_outdated` | 普通聊天已更新，先刷新引导再回答 |
| 422 | `validation_error` | 检查字段、文本长度和回答版本 |
| 422 | `guidance_revision_required` | 已有引导的自由补充需带当前版本 |
| 422 | `guidance_not_started` | 先生成问题再回答 |
| 422 | `guidance_answer_unexpected` | 用本轮问题 ID；其他信息通过 `message` 补充 |
| 502 | `guidance_invalid_response` / `guidance_truncated` | 本轮未保存，保留输入后重试 |
| 503 | `database_unavailable` | 检查数据库连接 |

模型与话题错误沿用 [后端接口说明](BACKEND_API.md) 中的 DeepSeek/Jev 错误码。缺少任一模型密钥时无法生成新引导；获取已有状态不需要调用模型。

## 验证记录

2026-10-09 已通过 223 项既有常规回归、28 项引导测试和 5 项真实 MySQL 检查。引导测试覆盖问题排序、补充与纠正、已有聊天信息、重启与历史裁剪、权限、过期、同进程锁、跨进程版本冲突、无效/截断输出及话题拒绝。MySQL 检查核实引导快照与完整消息的原子保存、版本回滚和重启恢复，以及原识别、聊天和知识流程。

另用虚构识别候选与真实 DeepSeek/Jev 做了两轮有界检查，均返回 200：第一轮询问作物、症状及受影响部位；补充苹果和褐色斑点后，第二轮改问出现时间、扩散和受影响部位，已回答字段未重复，原模型分数保留。该检查验证服务联通与结构化流程，不代表诊断准确率。

测试仅写入独立临时 MySQL 库，验证后已删除并撤销其临时权限。报告在被 Git 忽略的 `test-output/guidance-tests.xml`、`guidance-regression.xml`、`guidance-mysql.xml` 和 `guidance-live-verification.json`。
