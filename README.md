# 病虫害平台后端

后端按功能分为接口、图像识别、对话、用户账号、Jev 检查、知识库和公共组件。所有模块由同一个 FastAPI 服务加载，前端继续使用原有接口地址。

## 目录

```text
RearEnd/
├── API/                        HTTP 接口
│   ├── main.py                 应用创建、服务初始化及路由注册
│   ├── dependencies.py         获取识别和对话服务
│   ├── errors.py               统一错误响应
│   ├── middleware.py           上传大小限制
│   └── routes/
│       ├── health.py           健康检查
│       ├── recognition.py      图片识别、类别列表及结果图片
│       ├── chat.py             创建会话及发送问题
│       ├── knowledge.py        知识库列表、搜索及详情
│       ├── products.py         农资目录、分类和图片
│       └── users.py            注册、登录、退出及个人资料
├── Chat/                       智能对话
│   ├── service.py              首轮分析、多轮对话及知识复用
│   ├── deepseek.py             DeepSeek 请求与响应处理
│   ├── store.py                离线 SQLite 测试存储
│   └── mysql_store.py          MySQL 会话、逐条消息及识别记录
├── Jev/                        TypeSafe Jev 检查
│   └── guard.py                话题、问答、知识价值及适用性检查
├── Knowledge/                  长期知识库
│   ├── service.py              知识整理、复核及检索流程
│   ├── store.py                离线 SQLite 存储、去重及全文搜索
│   ├── mysql_store.py          MySQL 知识存储与中文全文检索
│   └── schemas.py              知识条目与判断结果的数据结构
├── Common/                     跨模块公共组件
│   ├── config.py               环境配置及路径解析
│   ├── database.py             共享 MySQL 连接池、事务及时间转换
│   ├── schemas.py              识别、对话及检查结果的数据结构
│   └── errors.py               公共业务异常
├── Users/                      用户账号
│   ├── store.py                离线 SQLite 用户测试存储
│   ├── mysql_store.py          MySQL 用户、资料及登录会话
│   ├── security.py             密码哈希与凭证摘要
│   └── schemas.py              账号与资料请求、响应结构
├── Shop/                       农资目录
│   ├── mysql_catalog.py        MySQL 商品、分类、规格和图库
│   ├── catalog.py              目录验证、筛选及离线数据读取
│   └── data/images/            已采集商品图片
├── Visual/                     图像识别及现有运行资源
│   ├── recognition/
│   │   ├── service.py          叶片检查、分类、结果保存及会话初始化流程
│   │   ├── recognizer.py       YOLO 病虫害分类
│   │   ├── leaf_gate.py        CLIP 叶片检查
│   │   ├── images.py           图片解码及结果图片生成
│   │   ├── labels.py           中文类别映射
│   │   ├── postprocess.py      分类排序与 Python 回退
│   │   └── _native.*           已编译的 C++ 排序扩展
│   ├── config/                中文类别与叶片检查提示配置
│   ├── cpp/                   C++ 源码
│   ├── tools/                 叶片模型下载工具
│   ├── storage/               原有数据库、结果图片及运行缓存
│   ├── .env                   原有环境配置
│   ├── .venv/                 原有 Python 环境
│   ├── setup_native.py        C++ 扩展构建配置
│   └── build_native.ps1       C++ 扩展编译入口
├── docs/
│   ├── BACKEND_API.md         接口、模型及配置详细说明
│   ├── USERS_API.md           用户接口及前端接入说明
│   ├── DATABASE_DESIGN.md     12 张表的设计及关系图
│   ├── MYSQL_MIGRATION.md     MySQL 接入、备份和验证记录
│   ├── sql/                   初始建表定义
│   └── verification/          历史模型验证记录
├── model/                     YOLO 与 CLIP 模型权重
├── tests/                     接口及各功能模块的回归测试、图片样例
├── tools/migrate_mysql.py     从 SQLite 和商品 JSON 迁移至空 MySQL 库
├── requirements.txt           运行依赖
├── requirements-dev.txt       开发与测试依赖
├── pytest.ini                 根目录测试配置
├── run.py                     统一 Python 启动入口
└── start.ps1                  统一 PowerShell 启动入口
```

原 `Visual/app` 中的业务代码已移入对应功能目录，旧兼容入口、重复的启动与依赖文件、编译缓存和测试临时目录已清理。应用入口为 `API.main`，开发时直接修改对应功能目录中的实现。

## 启动

在 PowerShell 中进入 `RearEnd`，执行：

```powershell
.\start.ps1
# 同一局域网内的手机访问：
.\start.ps1 -Lan
# 开发时监视各功能目录的代码变化：
.\start.ps1 -Reload
```

也可以直接使用现有 Python 环境：

```powershell
.\Visual\.venv\Scripts\python.exe .\run.py
```

启动统一使用根目录的 `start.ps1` 或 `run.py`。默认地址为 `http://127.0.0.1:8000`，接口文档位于 `/docs`，健康检查位于 `/health`。

## 配置与数据

配置统一由 `Common/config.py` 读取。`.env` 继续使用 `Visual/.env`，系统环境变量优先。相对路径继续以 `Visual` 为基准，因此已有模型路径、结果图片和数据库设置无需改动。

- 模型：`model/best.pt`、`model/clip_leaf_gate/`。
- 识别结果：`Visual/storage/results/`。
- 用户、登录、识别、聊天、知识和农资目录：远程 MySQL `plant_health`，共 12 张表。
- 原 SQLite 数据库保留为迁移来源和离线测试存储；实际运行由 `DATABASE_BACKEND=mysql` 选择 MySQL。
- 商品图片仍在 `Shop/data/images/`，数据库保存图库路径与元数据。
- 配置示例：`Visual/.env.example`。

模块与接口、模型行为的详细说明见 [后端接口说明](docs/BACKEND_API.md)。

用户注册、登录、退出和个人资料接口见 [用户接口说明](docs/USERS_API.md)。本地 `.env` 已配置远程数据库的专用应用账号和 TLS。MySQL 模式启动时校验表结构，连接失败不会回退到 SQLite 或 JSON。连接密码仅保存在被 Git 忽略的 `.env` 中。

数据库架构见 [数据库设计](docs/DATABASE_DESIGN.md)，本次迁移、备份和验证记录见 [数据库接入记录](docs/MYSQL_MIGRATION.md)。新增部署需先由数据库管理员执行 `docs/sql/plant_health_schema.sql`，再为业务库配置专用的 SELECT/INSERT/UPDATE/DELETE 账号。

迁移工具默认只检查原始数据，显式加 `--apply` 后备份并导入空目标库：

```powershell
.\Visual\.venv\Scripts\python.exe -m tools.migrate_mysql
.\Visual\.venv\Scripts\python.exe -m tools.migrate_mysql --apply
```

目标库已有数据时，工具拒绝覆盖。日常启动无需再次迁移。

## 验证

在 `RearEnd` 中执行：

```powershell
.\Visual\.venv\Scripts\python.exe -m pytest -q
```

常规测试使用隔离的 SQLite、模拟的 DeepSeek 与 Jev 请求。MySQL 集成测试需要单独准备 `plant_health_test_*` 临时库，通过 `MYSQL_TEST_DB` 指定，并运行 `pytest tests/test_mysql.py`；未配置时自动跳过。真实图片识别测试需设置 `YOLO_TEST_REAL_MODEL=1`，会使用本地 YOLO 和 CLIP 权重。

如果系统临时目录没有访问权限，可以将测试临时目录指定为项目内一个未使用的目录：

```powershell
New-Item -ItemType Directory -Path .\test-output -Force | Out-Null
.\Visual\.venv\Scripts\python.exe -m pytest -q --basetemp=.\test-output\run-1
```

`--basetemp` 目录仅用于测试临时数据。执行测试时 pytest 会清空该目录，请勿指定业务数据目录。

C++ 编译方式为 `.\Visual\build_native.ps1`，编译输出位于 `Visual/recognition/`。依赖安装与测试统一使用根目录的依赖文件和测试配置。

整理前的源码备份保存在 `.reorganization-backup/before-modules-20261005-171328.zip`。

2026-10-05 目录整理验证：152 项常规回归测试通过，另已通过需要手动开启的真实模型识别测试；整理前后完整 OpenAPI 定义一致，原有环境配置、模型和数据库文件摘要一致，C++ 扩展已成功重新编译。

2026-10-08 MySQL 接入验证：223 项常规回归、10 项真实 MySQL 集成和 1 项真实模型与 MySQL 测试通过。迁移数据和详细验证记录见 [数据库接入记录](docs/MYSQL_MIGRATION.md)。
