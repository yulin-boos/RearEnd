# 用户账号与个人资料

正式运行使用 MySQL `plant_health` 中的 `users`、`auth_sessions` 和 `conversations` 表，通过 `DATABASE_BACKEND=mysql` 选择。表结构由数据库管理员预先创建，启动时只校验结构及清理过期数据。旧聊天不会分配给新注册账号；知识使用同一业务库中的 `knowledge_entries` 表。SQLite 模式保留给离线测试和旧数据迁移。

## 接口

| 方法 | 路径 | 用途 | 是否需要登录 |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/register` | 注册并返回登录凭证，成功返回 201 | 否 |
| POST | `/api/v1/auth/login` | 账号密码登录 | 否 |
| POST | `/api/v1/auth/logout` | 撤销当前登录凭证，成功返回 204 | 是 |
| GET | `/api/v1/users/me` | 获取当前用户资料 | 是 |
| PATCH | `/api/v1/users/me` | 部分更新昵称、电话、简介 | 是 |

所有请求使用 JSON。账号为 3–40 个英文字母、数字或下划线，忽略首尾空格与大小写。密码为 8–128 个字符，不能全为空格；密码中的空格和 Unicode 字符保留原样。账号不能通过资料接口修改。

注册请求：

```json
{"username": "gardener", "password": "replace-with-your-password", "nickname": "我的菜园"}
```

`nickname` 可省略，默认使用账号的前 20 个字符。不会自动创建前端原有的演示账号。

登录请求：

```json
{"username": "gardener", "password": "replace-with-your-password"}
```

注册与登录返回相同结构：

```json
{
  "access_token": "<本次登录生成的随机凭证>",
  "token_type": "bearer",
  "expires_at": 1791936000.0,
  "user": {
    "id": "<用户编号>",
    "username": "gardener",
    "nickname": "我的菜园",
    "phone": "",
    "bio": "",
    "created_at": 1791331200.0,
    "updated_at": 1791331200.0
  }
}
```

时间字段为 Unix 时间戳，单位为秒。需要登录的请求通过请求头传凭证：

```http
Authorization: Bearer <access_token>
```

`PATCH /api/v1/users/me` 至少提供一个字段；未提供的字段保持原值：

```json
{"nickname": "番茄种植者", "phone": "13800138000", "bio": "记录阳台种植"}
```

昵称去除首尾空格后为 1–20 个字符，电话最长 30 个字符，简介最长 80 个字符。清空电话或简介请传 `""`；不能传 `null`。`id`、`username`、`password`、`role` 等额外字段会返回 422。账号与资料响应均不包含密码哈希或凭证哈希，并使用 `Cache-Control: no-store`。

## 聊天与图片归属

- 登录后创建文字咨询会话或上传识别图片时，带上相同的 `Authorization` 请求头。会话会关联到当前用户。
- 后续聊天、识别后的自动分析以及结果图片访问都会检查归属。其他用户和匿名请求访问这些会话或图片时返回 404。
- 不带凭证创建的会话保持原有匿名访问方式。旧会话不会自动归属到任何账号。
- 如果传入无效、格式不正确或过期的凭证，则返回 401，不会回退成匿名操作。
- 退出后当前凭证立即失效；同账号的其他登录凭证保持有效。重新登录后仍可使用同账号尚未过期的聊天会话。
- 知识库和商品接口保持公开查询。

前端显示用户识别结果图片时，也需要携带凭证下载图片；凭证不能放在图片 URL 查询参数中。

## 数据与配置

`users` 保存用户编号、唯一账号、密码哈希、昵称、电话、简介和创建/更新时间。`auth_sessions` 保存随机凭证的 SHA-256 摘要、用户编号及有效期，通过外键关联 `users`。

密码使用随机盐与 scrypt（N=32768、r=8、p=3、输出 32 字节）。这一参数组来自 [OWASP 密码存储说明](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#scrypt)，实现使用 Python 标准库，无新增运行依赖。登录验证在工作线程执行，最多同时进行两次密码哈希运算。

```dotenv
DATABASE_BACKEND=mysql
DB_NAME=plant_health
DB_USER=plant_health_app
# DB_PASSWORD belongs in the ignored local .env.
AUTH_SESSION_TTL_SECONDS=604800
```

默认登录凭证有效期为 7 天，配置范围为 300–2592000 秒。过期凭证在初始化以及成功注册/登录时清理。重启后未过期的凭证仍有效。公开部署的账号接口应使用 HTTPS 传输密码和凭证。

## 错误

沿用后端统一的 `{"error": {"code": "...", "message": "..."}}` 响应格式。

| HTTP 状态 | code | 含义 |
| --- | --- | --- |
| 401 | `auth_required` | 未携带登录凭证 |
| 401 | `invalid_session` | 凭证无效、已退出或过期 |
| 401 | `invalid_credentials` | 账号或密码不正确，未知账号使用相同提示 |
| 409 | `username_taken` | 账号已注册 |
| 422 | `validation_error` | 请求字段不合法 |
| 404 | `conversation_not_found` | 会话不存在、过期或不属于当前用户 |

401 响应含 `WWW-Authenticate: Bearer`。CORS 已允许 `PATCH` 和 `Authorization` 请求头。
