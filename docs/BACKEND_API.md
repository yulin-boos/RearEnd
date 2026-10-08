# 病虫害图片识别后端

用户注册、登录、退出、个人资料及登录后聊天归属规则见 [用户接口说明](USERS_API.md)。

后端已按功能拆分到 `RearEnd/API`、`Chat`、`Jev`、`Knowledge`、`Common` 和 `Visual/recognition`。完整目录说明和统一启动方法见 [后端目录说明](../README.md)。启动、依赖安装和测试统一在 `RearEnd` 根目录执行。

当前使用 `model/best.pt`：YOLO11 图片分类模型，训练输入尺寸为 **224×224**，支持 **38 个植物病害、叶螨危害及健康类别**。接口先检查叶片并识别病害，再结合识别结果分析和对话：优先复用适用的已有问答，否则由 DeepSeek 回复。TypeSafe Jev 检查问题、知识适用性和最终回复，只放行植物病因、症状及防治相关的交流。

**分类模型无法定位病斑，结果中没有检测框。** 后续若需要定位多个病斑或虫体，需要提供检测模型，并增加检测推理接口。类别清单以 `GET /api/v1/classes` 为准，模型无法识别清单以外的全部病虫害。

## 启动

当前机器的依赖已经安装在 `Visual/.venv`，C++ 模块已经编译，叶片检查模型已下载到 `model/clip_leaf_gate`。打开 PowerShell，在 `RearEnd` 目录执行：

```powershell
.\Visual\.venv\Scripts\python.exe .\run.py
```

- 接口文档：<http://127.0.0.1:8000/docs>
- 模型状态：<http://127.0.0.1:8000/health>
- 中文类别：<http://127.0.0.1:8000/api/v1/classes>

ArkTS 项目 `PestandDiseasePlatform` 的底部「识别」页面已接入本服务。
模拟器或 USB 调试设备可使用 `hdc rport tcp:8000 tcp:8000` 配合默认地址。
同一 Wi-Fi 下的真机可执行 `.\start.ps1 -Lan`，再在识别页的连接设置中填写电脑 IPv4 地址。
`run.py` 也支持 `--host` 和 `--port`，普通启动仍只监听 `127.0.0.1`。
完整操作说明见前端项目的 `RECOGNITION_API.md`。识别页使用 `auto_analyze=false`，无需在线问答模型密钥。

在 `/docs` 中展开 `POST /api/v1/recognize`，点击 **Try it out**，选择图片，点击 **Execute**。响应中的 `result_image_url` 是相对 API 服务地址的图片路径。

最简单的测试网页位于 `C:\Users\yulin\Desktop\Graduation_Project\Text\index.html`。先启动后端，然后双击该文件，选择图片并点击“识别”，下面会显示首轮分析，输入问题即可继续交流。复用的答案标记为“已有知识参考”，新生成的答案标记为“DeepSeek”。当前 `.env` 已允许本地文件页面的 `null` 来源；已运行的后端需要重启后才会读取新配置。

## DeepSeek 对话

在 `Visual/.env` 中填写 `DEEPSEEK_API_KEY` 和 `TYPESAFE_API_KEY`，然后重启后端。两个服务使用各自的密钥，均由后端读取，不放在 HTML 或接口响应中。缺少 Jev 密钥时暂停对话；缺少 DeepSeek 密钥时仍可复用通过 Jev 检查的已有答案，未命中则提示配置。缺少密钥不影响图片识别。`.env.example` 是配置示例，服务不会直接读取它。

默认配置：

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_TIMEOUT_SECONDS=60
DEEPSEEK_MAX_TOKENS=1000
TYPESAFE_API_KEY=
TYPESAFE_BASE_URL=https://api.typesafe.ai
TYPESAFE_MODEL=jev-latest
TYPESAFE_TIMEOUT_SECONDS=15
JEV_MIN_RELEVANCE=0.8
JEV_MAX_VIOLATION=0.2
CHAT_DB_PATH=storage/chat.sqlite3
DATABASE_BACKEND=mysql
DB_NAME=plant_health
DB_USER=plant_health_app
CHAT_SESSION_TTL_SECONDS=86400
CHAT_HISTORY_TURNS=10
CHAT_CONTEXT_TOP_K=5
KNOWLEDGE_DB_PATH=storage/knowledge.sqlite3
KNOWLEDGE_MIN_VALUE=0.8
KNOWLEDGE_MIN_MATCH=0.85
KNOWLEDGE_TOP_K=3
KNOWLEDGE_CANDIDATE_LIMIT=6
KNOWLEDGE_DIRECT_ENABLED=true
KNOWLEDGE_DIRECT_MIN_MATCH=0.85
```

把 `DEEPSEEK_API_KEY=` 的空值替换成自己的密钥。默认模型名与非思考模式参数依据 [DeepSeek 官方 Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)；如有其他可用模型，可修改 `DEEPSEEK_MODEL`。

工作流程：

1. 未通过叶片检查时返回 `not_leaf`，不创建对话、不调用 DeepSeek。
2. 通过后，后端保存前 5 个候选病害/健康类别、各自置信度、作物、叶片检查结果和是否达到病害阈值。即使 `top_k=1`，DeepSeek 默认也能收到 5 个候选。
3. Jev 检查默认首轮问题，通过后检索知识；能完整回答时直接复用，否则调用 DeepSeek 解释可能成因、需要核实的信息与下一步建议。两种回复都需通过 Jev 检查，才返回到识别响应的 `chat.reply`。
4. 后续向 `/api/v1/chat` 提交识别响应中的 `request_id` 和问题。后端每轮自动加入原识别结果及近期历史，不接受前端传入的候选、系统角色或对话历史。每轮仍执行提问和回复的两次 Jev 检查。

发送给 DeepSeek 的内容是结构化识别结果和对话文字。候选病害是图片模型推测，不是已经确认的病因；提示词要求对低置信度结果明确不确定，并避免声称直接看过原图。成因分析仍需结合用户补充的症状和种植环境。

正常通过的一轮分析或追问包含两次 Jev 话题检查；检索到候选知识时增加一次 Jev 匹配请求。直接复用时不调用 DeepSeek，也不重新整理或录入同一答案，但 Jev 调用仍有费用。未直接复用时调用一次 DeepSeek 生成；有新增录入价值时再增加一次 DeepSeek 整理和一次 Jev 条目复核。知识价值判断与回复检查在同一次 Jev 请求中完成。输入被拦截时不会调用 DeepSeek；预设答案未通过话题检查时移除该参考并尝试生成，Jev 超时或返回无效话题分数时暂停对话。上传时设置 `auto_analyze=false` 可跳过首轮调用，识别上下文仍然保留，后续追问仍需检查。首轮请求等待检查、回复及适用的知识整理完成或失败后才返回，`elapsed_ms` 仅记录图片识别阶段，`chat.elapsed_ms` 记录对话整体耗时。

DeepSeek 或 Jev 调用失败不会丢掉识别结果：识别接口仍返回 200，并在 `chat.status` 和 `chat.error` 中给出原因。追问接口按情况返回 503（未配置、密钥/余额问题）、429（频率限制）、504（超时）或 502（上游/连接问题）。不会把上游原始报错或密钥回传给网页。失败或被拦截的对话轮次不写入历史，可以重试。

上下文与历史保存在 MySQL `plant_health` 的 `conversations`、`chat_messages` 表，后端重启后仍可凭原 `request_id` 继续对话。会话有效期内保存逐条历史，模型上下文默认读取最近 10 个完整轮次，每次成功回复延长 24 小时有效期。过期记录在启动或创建新会话时清理，过期/不存在的对话返回 404。每段对话同时只生成一个回复，重复并发请求返回 409；跨进程修改通过行锁与版本校验避免覆盖历史。SQL、令牌及连接密码不会出现在接口响应中，数据库不可用返回 503 `database_unavailable`。

也可以运行 `start.ps1`；开发时使用 `start.ps1 -Reload`。两种方式都会读取 `Visual/.env` 中的 HOST 和 PORT。每个进程在启动时加载一次模型，推理在工作线程内执行，同一模型的预测通过锁串行处理。

## Jev 话题限制

Jev 通过 [TypeSafe 官方 API](https://docs.typesafe.ai/api) 的 `POST /v1/systemone` 调用，默认模型 `jev-latest`。从 [TypeSafe 控制台](https://console.typesafe.ai/home) 获取独立密钥，填入后端 `.env` 的 `TYPESAFE_API_KEY`；不要用 DeepSeek 的密钥替代。

判断规则在 `Jev/guard.py` 的 `SCOPE` 和 `questions_for()`，DeepSeek 的辅助范围约束在 `Chat/service.py` 的 `SYSTEM_PROMPT`。每次 Jev 请求同时提出三个独立 Noul 判断：是否与植物健康有关、是否包含无关任务、是否试图绕过话题规则或读取内部信息。通过条件为相关分数 `>= JEV_MIN_RELEVANCE`，另两个违规分数均 `< JEV_MAX_VIOLATION`。

允许询问病因、病斑症状、害虫、防治、识别不确定性和影响病害的种植环境，也允许结合上下文的“为什么”“继续”“怎么处理”和症状补充。通用编程、写作、娱乐等请求，以及“先说病害，顺便帮我写代码”这类混合任务会被检查；纠正作物识别和要求回答简短属于正常交流。仅出现“叶子”“病害”等关键词不能直接放行。

Jev 收到服务端保存的识别结果、最近 3 个对话轮次（每条历史文字最多 2000 字符）、当前问题，以及输出检查时的 DeepSeek 回复。输出检查同时评价知识价值、依据与私人信息，并选择整理重点。原图和密钥不放入检查内容。提问和回复均需通过话题检查后才保存完整轮次，网页只显示通过检查的回复。前端不能提交检查分数或关闭检查；Jev 缺少密钥、超时、网络失败或返回无效话题分数时暂停对话。仅知识价值字段缺失时返回正常聊天回复，跳过知识录入并标记失败。

| 情况 | 追问 HTTP 状态 | 错误码 |
| --- | --- | --- |
| 无关问题或混合无关任务 | 403 | `chat_off_topic` |
| 绕过规则或读取内部信息 | 403 | `chat_instruction_rejected` |
| 相关性不足、无法明确确认 | 422 | `chat_topic_uncertain` |
| DeepSeek 回复未通过检查 | 502 | `chat_reply_rejected` |
| Jev 未配置 | 503 | `jev_not_configured` |
| Jev 无效响应、连接失败、上游不可用 | 502 | `jev_invalid_response` / `jev_connection_failed` / `jev_unavailable` |
| Jev 密钥/余额问题、限流或超时 | 503 / 429 / 504 | `jev_auth_failed` / `jev_insufficient_balance` / `jev_rate_limited` / `jev_timeout` |

`/health` 的 `jev` 字段显示检查启用状态、密钥是否存在、模型和阈值；存在密钥不代表验证过有效性。模型可能误判，默认阈值是项目初始配置，并未用真实 Jev 中文样本校准。[TypeSafe 官方模型说明](https://docs.typesafe.ai/models)指出中文支持与英语效果不同，配置密钥后需用实际问题验证。`jev-latest` 会随版本变化；校准后可将 `TYPESAFE_MODEL` 固定为验证过的版本。

## 每轮对话的知识录入与查询

会话历史用于多轮续聊，保存在 MySQL `chat_messages`。Jev 判断这轮内容是否值得新增到同库的 `knowledge_entries`，来源及复用关联保存在 `message_knowledge_links`。知识库保存通用问答，不保存原图、联系方式或可识别的私人经历。知识条目不跟随会话的 24 小时有效期过期，后端重启后仍可查询。

新生成的回复通过话题检查后：

1. Jev 评价当前问答的复用价值、依据与私人信息，并选择成因、症状、管理、预防、诊断核实等整理重点。价值和依据分数均至少 `KNOWLEDGE_MIN_VALUE`、私人信息分数低于 `JEV_MAX_VIOLATION` 且整理重点不是 `none`，才进入整理。重点分类的置信度作为参考，不代替价值判断。
2. 不符合条件时，`knowledge.status=skipped`，不调用额外的 DeepSeek 整理，也不新增知识记录。
3. 符合条件时，将 Jev 所选重点映射为固定整理指令，调用 DeepSeek 的 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 提炼标题、作物、病害/健康问题、通用问句、精简答案、适用条件、不确定性与关键词。DeepSeek 不接触数据库，不生成可执行 SQL；后端校验结构后使用参数化 SQL 写入。
4. Jev 再检查条目是否忠于原问答、可复用、含私人信息，以及是否被已有候选知识覆盖。拒绝的条目不录入；语义重复的条目跳过。精确重复由唯一指纹和事务约束防止重复插入，保留不同适用条件。

问候、重复内容、单纯请求补充信息、裸类别/置信度或未经确认的个案诊断不应当作知识保存。输出被截断、缺少判断字段、整理格式无效或复核失败时不写入。整理、复核或写库失败不影响已经通过话题检查的回复，`knowledge.status=error` 和 `error_code` 给出阶段原因；正常续聊历史仍保存。

用户提问时，先使用 MySQL ngram 全文索引检索，短词或无有效全文词元时使用转义后的参数化 LIKE 兜底，再由 Jev 检查候选知识与当前作物、症状和问题是否匹配。一次请求分别判断“可供参考”和“可直接完整回答”：

匹配使用 [TypeSafe API](https://docs.typesafe.ai/api) 的 Noul 判断，并用 `criteria.true` / `criteria.false` 明确直接复用与重新生成的条件。

1. 可直接回答的分数至少 `KNOWLEDGE_DIRECT_MIN_MATCH`（默认 0.85），且参考匹配分数至少 `KNOWLEDGE_MIN_MATCH`（默认 0.85），选择直接回答分数最高的一条。两个判断的条件不同：Jev 必须进一步判断作物、病情/症状、问题意图及适用条件相符，已有答案能完整回答当前问题且无需改写。只出现同一种病名不能授权复用；新增症状、程度变化、不同作物、不同任务或额外问题需要适配时继续生成。带有不确定性的通用观察和管理建议可复用，不要求已确诊；不能套用缺少依据的确定诊断或具体用药方案。
2. 后端返回已有答案、适用条件与不确定性；低置信度识别额外提示核实。最终文本仍由 Jev 检查，成功后保存为正常续聊历史，返回 `source=knowledge`、`model=knowledge`、`knowledge.status=reused`、条目 ID 和复用分数。本轮不调用 DeepSeek、不新增知识条目；后续追问仍会重新匹配。
3. 未达到直接回答阈值时，参考分数达到 `KNOWLEDGE_MIN_MATCH` 的最多 `KNOWLEDGE_TOP_K` 条交给 DeepSeek；没有适用参考或检索/匹配失败时正常生成，不引用未经确认的候选。返回 `source=deepseek`。设置 `KNOWLEDGE_DIRECT_ENABLED=false` 可关闭直接复用，保留检索参考。

短追问会结合识别背景和近期对话做判断。知识检索是词项召回加模型筛选，不能保证所有同义问题都能命中。阈值是 Jev 的判断分数，不是经过校准的实际正确率；0.85 是少量样例检查后的初始配置，需用更多实际问题校准。可提高到 0.95 以减少直接复用，或关闭该功能保留正常生成。

知识条目的 `source_type=ai_summary`、`expert_verified=false`，保留不确定性和适用条件；它们是模型整理的参考资料，不能当作确诊证据。价值与匹配阈值是初始配置，仍需结合实际问题校准。

`chat.knowledge` 与追问响应的 `knowledge` 包含：

| 字段 | 说明 |
| --- | --- |
| `status` | `skipped` / `stored` / `duplicate` / `rejected` / `error` / `reused` |
| `entry_id` | 已存入、复用或精确重复条目的 ID；语义重复时可为空 |
| `decision` | Jev 模型、价值/依据/私人信息分数、整理重点与重点置信度 |
| `source_ids` | 本轮参考的已有知识 ID |
| `error_code` | 本轮整理或录入失败原因；不包含原始敏感报错 |
| `retrieval_error_code` | 本轮检索或匹配失败原因 |
| `reuse_probability` | 直接复用时的 Jev 完整回答分数，否则为空 |

独立查询不调用模型 API：

```powershell
curl.exe --get "http://127.0.0.1:8000/api/v1/knowledge" --data-urlencode "query=苹果叶片斑点如何管理" --data-urlencode "crop=苹果"
```

`query` 可选，填写时为 2–4000 字符；省略时按最近收录时间返回知识列表。`limit` 默认为 5、最多 50，`offset` 默认为 0 且不能为负，`crop` 可选且为精确作物名筛选。列表与搜索均支持分页，响应仍为知识条目数组，包含完整答案、适用条件和不确定性。`GET /api/v1/knowledge/{entry_id}` 查看单条知识。查询接口返回词项匹配的候选条目；自动对话会进一步由 Jev 判断适用性。浏览列表和详情只读取本地知识数据库，不调用 DeepSeek 或 Jev，也不新增记录。

主要代码：价值规则与指令重点在 `Jev/guard.py`，整理提示词与录入流程在 `Knowledge/service.py`，数据库表及检索在 `Knowledge/store.py`。

## Python 与 C++ 的职责

- Python：FastAPI 接口、图片解码与方向校正、CLIP 叶片检查、Ultralytics/PyTorch 病害模型推理、中文类别映射、结果图片生成，以及 Jev 话题检查、DeepSeek 调用与对话历史管理。
- C++：通过 pybind11 调用 `cpp/postprocess.cpp`，验证分类概率并进行 Top-K 排序；相同概率按类别 ID 排序。
- 未编译 C++ 时自动使用同等行为的 Python 实现；`/health` 和识别响应中的 `postprocessor` 显示 `cpp` 或 `python`。设置 `REQUIRE_NATIVE=true` 可要求 C++ 模块必须可用。

目前 YOLO 推理本身由 Python/PyTorch 执行。C++ 模块用于建立可扩展的原生处理接口；38 类排序计算量很小，不声称它能显著提高整条推理链路的速度。

## 非叶片拒绝识别

每次图片请求都会先调用本地 [CLIP 图像语义模型](https://huggingface.co/openai/clip-vit-base-patch32)。它比较叶片提示和人物、动物、车辆、电子设备、果实、花朵、草地、空白图片等非叶片提示的相似度。病害模型的 38 类没有“非叶片”类别，因此叶片检查独立于病害置信度。

- 确认叶片：继续执行 YOLO 病害推理。
- 非叶片或无法明确确认：返回 **HTTP 422**，`error.code=not_leaf`，提示“未检测到明确的植物叶片，已拒绝识别”。不调用病害模型、不生成结果图片、不返回病害候选。
- 请求里的 `confidence` 只控制病害结果，不能关闭或绕过叶片检查。
- 叶片检查模型缺失时服务启动失败，不会自动跳过检查。服务运行时只读取本地权重。

默认要求叶片相似度至少 `0.24`，且比最相近的非叶片提示高至少 `0.02`；通过 `.env` 的 `LEAF_MIN_SIMILARITY` 和 `LEAF_MIN_MARGIN` 调整，提示词位于 `Visual/config/leaf_prompts.json`。相似度是余弦分数，不是准确率或校准概率。`competing_category` 记录比较用的非叶片提示类别，不代表确认检测到了该物体。

这是一套模型判断，不能保证所有非叶片都被拒绝，模糊、遮挡或复杂背景下的叶片也可能被误拒。阈值应使用实际上传的叶片与非叶片样本验证；当前样例验证不代表泛化准确率。

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 病害与叶片检查模型、设备、类别数量、C++ 状态、模型配置与知识功能状态 |
| POST | `/api/v1/chat/sessions` | 创建不依赖识图的植物文字咨询会话；返回 `session_id`、`kind: general` 和标题，创建本身不调用外部服务。发送时将 `session_id` 放入现有 `/api/v1/chat` 请求的 `recognition_id` 字段，后端按真实会话类型使用对应上下文 |
| GET | `/api/v1/classes` | 模型实际类别及中文说明 |
| POST | `/api/v1/recognize` | 先检查叶片再识别病害，使用 multipart/form-data |
| POST | `/api/v1/chat` | 使用识别 ID 多轮对话，优先复用已有知识，否则由 DeepSeek 回复 |
| GET | `/api/v1/knowledge` | 浏览最近知识或按 query 搜索，支持 crop、limit、offset |
| GET | `/api/v1/knowledge/{entry_id}` | 查看单条知识 |
| GET | `/api/v1/results/{request_id}.jpg` | 下载带识别文字的结果图片 |

上传字段：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `file` | 文件 | 必填 | JPEG、PNG、静态 WebP 或 BMP |
| `confidence` | 0–1 小数 | 0.5 | 第一候选低于阈值时返回 `status=uncertain` |
| `top_k` | 1–50 整数 | 5 | 候选数量不超过模型实际类别数 |
| `save_result` | 布尔 | true | false 时不保存图片，图片路径为 null |
| `auto_analyze` | 布尔 | true | 识别后自动分析，优先复用知识，否则由 DeepSeek 回复 |

通过叶片检查后，`confidence` 判断病害结果是否达到阈值，Top-K 候选仍会完整返回。`is_healthy` 描述候选类别；应结合 `is_confident` 判断是否采纳候选，不要把低置信度的健康候选直接当作确认健康。叶片检查过滤输入类型，不验证病害是否属于这 38 类；未训练过的病害仍可能被分到某个已知类别。

PowerShell 请求示例（把图片路径换成自己的文件）：

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/recognize" `
  -F "file=@C:\path\leaf.jpg" `
  -F "top_k=5" `
  -F "confidence=0.5" `
  -F "save_result=true"
```

响应包含 `top_prediction` 和 `predictions`，每项均有 `class_id`、`class_name`、`display_name`、`crop`、`condition`、`is_healthy` 和 `confidence`。还包含原图尺寸、模型名称、叶片检查信息 `leaf_check`、耗时及图片路径。`inference_ms` 包含 YOLO 预处理、推理和概率读取，`leaf_check.elapsed_ms` 记录叶片检查耗时，`elapsed_ms` 另外包含图片解码和保存，不包含上传时间及工作线程排队时间。

`chat` 包含 `recognition_id`、`configured`、`guard_configured`、`model`、`source`、`status`、`reply`、`error`、`elapsed_ms`、`truncated`、`guard` 和 `knowledge`。`source` 为 `deepseek` 或 `knowledge`，成功时表示答案来源。状态为 `ready`、`not_configured`、`error` 或 `skipped`；`configured` 表示存在 DeepSeek 密钥，`guard_configured` 表示存在 Jev 密钥，均不代表已验证有效性。成功时 `guard.question` 和 `guard.reply` 包含实际 Jev 模型版本、话题判断分数及耗时；`guard.reply.knowledge_decision` 是价值判断结果，`knowledge` 是实际整理/录入或复用结果。回复被输出上限截断时 `truncated=true`，可继续追问。

前端示例：

```javascript
const form = new FormData();
form.append("file", selectedFile);
form.append("top_k", "5");
const response = await fetch("http://127.0.0.1:8000/api/v1/recognize", {
  method: "POST", body: form,
});
const data = await response.json();
if (!response.ok) throw new Error(data.error.message);
console.log(data.top_prediction.display_name, data.top_prediction.confidence);
const imageUrl = data.result_image_url
  ? new URL(data.result_image_url, "http://127.0.0.1:8000").href
  : null;
```

追问示例：

```javascript
const response = await fetch("http://127.0.0.1:8000/api/v1/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    recognition_id: recognitionResult.request_id,
    message: "斑点已经出现三天，而且还在扩散，应该怎么处理？",
  }),
});
const answer = await response.json();
if (!response.ok) throw new Error(answer.error.message);
console.log(answer.reply);
```

`message` 最多 4000 个字符；省略该字段时发起或重试默认的首轮分析。成功响应包含 `reply`、`history_length`、`usage` 和 `truncated` 等信息。重新上传图片会创建一段新的对话，避免混用不同图片的上下文。

不要手动设置 FormData 的 Content-Type；浏览器会自动加入 multipart boundary。前端跨域时，将其地址写入 `.env` 的 `CORS_ORIGINS`。

错误响应统一为 `{"error":{"code":"...","message":"..."}}`。空文件/损坏图片返回 400，超出大小或像素限制返回 413，不支持的图片格式返回 415；参数错误与叶片拒识均返回 422，分别用 `validation_error` 和 `not_leaf` 区分。拒识响应还包含 `error.leaf_check` 诊断分数。启动时任一模型不存在、病害模型非分类模型或原生模块不满足配置要求会直接启动失败并输出原因。

## 配置与存储

复制 `Visual/.env.example` 为 `Visual/.env` 后按需修改。相对路径始终以 `Visual` 为基准，与启动目录无关；系统环境变量优先于 `.env`。

- 默认 CPU 推理；GPU 环境可安装相应 CUDA 版 PyTorch，并设置 `YOLO_DEVICE=0`。
- 叶片模型路径为 `LEAF_MODEL_PATH`，设备由 `LEAF_DEVICE` 配置，默认 CPU；不提供请求级跳过叶片检查的选项。
- 默认文件大小上限 10 MB、像素上限 2000 万。服务对已声明长度及流式请求都限制总上传字节数，multipart 额外允许 64 KB 开销。
- 结果图片保存在 `Visual/storage/results`，默认有效期 24 小时。启动或保存新结果时清理过期 JPG；过期图片接口返回 404。
- 上传原图不单独落盘。结果图片使用随机 ID 命名，不使用上传文件名；结果图最大边长约 1600 像素，附带文字面板，原图尺寸在 JSON 中保留。
- CORS 默认关闭；需要时在 `.env` 配置来源。中文映射位于 `Visual/config/labels_zh.json`，替换分类模型后应更新映射，未知类别保留原名。

## 在新环境安装

Python 3.10 及以上，当前验证环境为 Windows / Python 3.13。CPU 版本依赖的安装命令：

```powershell
cd C:\Users\yulin\Desktop\Graduation_Project\RearEnd
python -m venv Visual/.venv
.\Visual\.venv\Scripts\python.exe -m pip install "torch>=2.6,<3" "torchvision>=0.21,<1" --index-url https://download.pytorch.org/whl/cpu
.\Visual\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\Visual\.venv\Scripts\python.exe Visual/tools/download_leaf_model.py
.\Visual\.venv\Scripts\python.exe Visual/setup_native.py build_ext --inplace
.\Visual\.venv\Scripts\python.exe run.py
```

Windows 编译 C++ 需要 Visual Studio 的“使用 C++ 的桌面开发”工具集；Linux 需要支持 C++17 的编译器。编译生成的扩展与 Python 版本及操作系统绑定，切换环境后需要重新编译。只运行 Python 版本时安装 `requirements.txt` 即可。

叶片检查使用 `openai/clip-vit-base-patch32` 的固定版本 `3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268`，权重约 605 MB。下载脚本使用 `hf download`，下载完成后可离线运行，不修改原有 `best.pt`。原始模型说明保存在 `model/clip_leaf_gate/README.md`。

## 验证

在 `RearEnd` 根目录执行：

```powershell
.\Visual\.venv\Scripts\python.exe -m pytest -q
# 同时加载叶片检查与病害模型，验证真实图片的通过/拒绝流程：
$env:YOLO_TEST_REAL_MODEL = '1'
.\Visual\.venv\Scripts\python.exe -m pytest -q
```

测试覆盖上传与图片下载、低置信度语义、大小/像素限制、无 Content-Length 上传、参数校验、过期结果、EXIF 方向、透明图片、原生与 Python 结果一致性以及真实模型接口联通。拒识测试验证病害模型不被调用、图片不被保存、低病害阈值不能绕过检查以及叶片模型缺失时启动失败。

本机验证结果：**124 项测试通过**，包括真实叶片检查与 YOLO 推理。固定样例中，8 张健康/患病叶片通过，4 张人物、车辆与动物图片以及 5 种纯色图片被拒绝。样例与来源记录在 `tests/fixtures/leaf_gate/sources.json`，叶片检查分数记录在 `docs/verification/leaf-check-verification.json`。这些样例仅验证功能，不代表识别或拒识的泛化准确率。

DeepSeek 测试使用 HTTP 模拟传输，验证首轮自动调用、Top-1 请求仍携带多候选、低置信度上下文、后续历史、重启恢复、拒识不调用、重叠请求拒绝、截断回复、超时/余额/密钥/限流错误处理和上游敏感报错不回传。

31 项 Jev 测试使用 HTTP 模拟传输，验证两阶段检查顺序、短追问上下文、无关/混合/绕过请求的路由、低相关性处理、偏题回复隐藏、不保存失败轮次、检查结果缺失或非法时暂停、上游错误不泄漏，以及请求无法跳过检查。新增的 29 项知识测试验证无价值时不整理/录入、结构化整理与复核、同义与并发精确重复处理、跨会话/重启查询与引用、私人信息拦截、非法结构拒绝、未匹配知识不引用、失败不影响正常聊天及 SQL 参数安全。pytest 均使用模拟模型 API，不调用付费 DeepSeek 或 Jev 服务。

17 项知识直接复用测试验证命中时零 DeepSeek 调用、阈值边界、保留条件与不确定性、不重复录入、跨用户/重启复用、短追问保留历史、没有 DeepSeek 密钥仍可命中、阈值不足与关闭功能时继续生成、预设回复被拦截后的回退、Jev 失败时暂停、无关问题和伪造请求被拒绝、非法匹配分数不引用，以及选择最适用答案。目录整理后的 152 项常规回归测试通过，另已通过真实 YOLO/CLIP 图片检查。模拟模型 API 测试验证流程，不代表真实语义匹配准确率。

另用真实 Jev 检查直接复用：在临时数据库中复制正式库的一条番茄知识，搭配虚构识别背景；完整回答分数为 0.88，通过最终话题检查，未调用 DeepSeek，适用条件、不确定性和会话历史均保留。额外的简短苹果通风浇水样例为 0.87，也成功复用；要求具体药剂剂量但原条目不提供剂量时分数为 0.05，没有复用。同轮苹果检索中的番茄候选为 0.04，没有跨作物套用。正式数据库未修改，临时数据库已清理，状态与分数在 `docs/verification/knowledge-reuse-live-verification.json`。这些有界样例只验证联通和流程，不代表中文同义问题的整体准确率。

另使用已配置的真实模型密钥和虚构植物问题完成小规模联通检查：Jev 对基础样例选择跳过录入；对可复用的观察方法问答给出价值分数 0.85、依据分数 0.92，DeepSeek 成功整理为 JSON，经 Jev 复核后写入临时知识库，随后词项查询和 Jev 匹配均命中 1 条。临时数据库已清理，正式知识库没有加入测试问答。仅记录状态和分数的验证结果在 `docs/verification/knowledge-live-verification.json`；这些样例不代表价值判断或检索的总体准确率。

另已启动真实后端并通过 HTTP 检查：叶片返回 200，前端请求 Top-1 时数据库仍保存 5 个候选；有测试用 DeepSeek 密钥而无 Jev 密钥时追问返回 503，客户端请求跳过检查返回 422；非叶片返回 422 且不创建对话，未调用外部 API。测试网页脚本语法检查通过；使用已有 httpx 依赖接入 Jev，无需安装新的 SDK。

实现参考：[Ultralytics 分类推理](https://docs.ultralytics.com/tasks/classify/)、[FastAPI 文件上传](https://fastapi.tiangolo.com/tutorial/request-files/)、[pybind11 构建扩展](https://pybind11.readthedocs.io/en/stable/compiling.html)。
