# 病虫害平台后端

后端按功能分为接口、图像识别、对话、Jev 检查、知识库和公共组件。所有模块由同一个 FastAPI 服务加载，前端继续使用原有接口地址。

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
│       └── knowledge.py        知识库列表、搜索及详情
├── Chat/                       智能对话
│   ├── service.py              首轮分析、多轮对话及知识复用
│   ├── deepseek.py             DeepSeek 请求与响应处理
│   └── store.py                会话上下文及历史记录
├── Jev/                        TypeSafe Jev 检查
│   └── guard.py                话题、问答、知识价值及适用性检查
├── Knowledge/                  长期知识库
│   ├── service.py              知识整理、复核及检索流程
│   ├── store.py                SQLite 存储、去重及全文搜索
│   └── schemas.py              知识条目与判断结果的数据结构
├── Common/                     跨模块公共组件
│   ├── config.py               环境配置及路径解析
│   ├── schemas.py              识别、对话及检查结果的数据结构
│   └── errors.py               公共业务异常
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
│   └── verification/          历史模型验证记录
├── model/                     YOLO 与 CLIP 模型权重
├── tests/                     接口及各功能模块的回归测试、图片样例
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
- 会话历史：`Visual/storage/chat.sqlite3`。
- 长期知识：`Visual/storage/knowledge.sqlite3`。
- 配置示例：`Visual/.env.example`。

模块与接口、模型行为的详细说明见 [后端接口说明](docs/BACKEND_API.md)。

## 验证

在 `RearEnd` 中执行：

```powershell
.\Visual\.venv\Scripts\python.exe -m pytest -q
```

测试使用模拟的 DeepSeek 与 Jev 请求。真实图片识别测试需设置 `YOLO_TEST_REAL_MODEL=1`，会使用本地 YOLO 和 CLIP 权重。

如果系统临时目录没有访问权限，可以将测试临时目录指定为项目内一个未使用的目录：

```powershell
New-Item -ItemType Directory -Path .\test-output -Force | Out-Null
.\Visual\.venv\Scripts\python.exe -m pytest -q --basetemp=.\test-output\run-1
```

`--basetemp` 目录仅用于测试临时数据。执行测试时 pytest 会清空该目录，请勿指定业务数据目录。

C++ 编译方式为 `.\Visual\build_native.ps1`，编译输出位于 `Visual/recognition/`。依赖安装与测试统一使用根目录的依赖文件和测试配置。

整理前的源码备份保存在 `.reorganization-backup/before-modules-20261005-171328.zip`。

本次整理验证：152 项常规回归测试通过，另已通过需要手动开启的真实模型识别测试；整理前后完整 OpenAPI 定义一致，原有环境配置、模型和数据库文件摘要一致，C++ 扩展已成功重新编译。
