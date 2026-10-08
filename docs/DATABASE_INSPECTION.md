# stu626 数据库核验记录

核验日期：2026-10-08（北京时间）。

本文记录实施前的只读检查。后续已为当前项目独立创建 `plant_health`，本地应用切换至 MySQL；迁移数量、备份及验证见 [数据库接入记录](MYSQL_MIGRATION.md)。`stu626` 继续承载原设备管理业务。

连接地址：`170.106.137.89:3306`；数据库：`stu626`；已使用用户确认的 `root` 账号成功登录。

服务器版本：`8.0.46-0ubuntu0.24.04.3`。本次连接使用 TLS。

仅在只读事务中查询元数据、建表定义和记录数量，没有修改远程表结构或数据。下列用途依据表名、字段及外键推断，数据库未填写表注释。

共 6 张基本表、50 个字段、19 个索引和 7 条外键；未发现视图或触发器。

## 表清单

| 表名 | 推断用途 | 记录数 | 字段数 |
| --- | --- | ---: | ---: |
| `categories` | 设备分类 | 5 | 3 |
| `departments` | 部门 | 4 | 5 |
| `equipment` | 设备资产 | 48 | 15 |
| `maintenance_records` | 设备维修记录 | 9 | 11 |
| `operation_logs` | 操作日志 | 72 | 7 |
| `users` | 用户账号 | 5 | 9 |

记录数来自 `SELECT COUNT(*)`，以核验时的只读事务快照为准；后续业务写入可能改变数量。所有表均为 InnoDB，排序规则为 `utf8mb4_unicode_ci`。

## 与当前后端的对应情况

当前代码提供病虫害识别、对话、知识库、用户账号和农资商品功能；远程库的结构围绕设备资产、部门和维修记录组织。

- 用户、登录会话和对话目前保存在本地 SQLite，涉及 `users`、`auth_sessions` 和 `conversations`；远程仅有同名 `users` 表。
- 本地 `users.id` 是文本 UUID，远程 `users.id` 是自增整数；本地使用 `nickname`、`phone`、`bio` 和 `updated_at`，远程使用 `display_name`、`role`、`department_id`、`active` 和 `token_version`。这些结构不能直接替换。
- 长期知识目前保存在本地 SQLite，涉及 `knowledge_entries` 和 SQLite FTS5 全文索引 `knowledge_search`；远程库没有这些表，全文检索也需要适配 MySQL。
- 商品目录目前来自 `Shop/data/products.json`；远程库没有商品表，设备表 `equipment` 的字段和外键不能作为现有商品模型直接使用。

本次完成连接与结构核验。完整接入需要为当前业务设计表结构和迁移方案，并处理远程已有 `users` 表的名称与字段冲突。

## 外键关系

| 来源字段 | 引用字段 | 删除规则 | 更新规则 |
| --- | --- | --- | --- |
| `equipment.category_id` | `categories.id` | RESTRICT | NO ACTION |
| `equipment.department_id` | `departments.id` | RESTRICT | NO ACTION |
| `maintenance_records.equipment_id` | `equipment.id` | RESTRICT | NO ACTION |
| `maintenance_records.reported_by` | `users.id` | RESTRICT | NO ACTION |
| `operation_logs.user_id` | `users.id` | RESTRICT | NO ACTION |
| `operation_logs.department_id` | `departments.id` | RESTRICT | NO ACTION |
| `users.department_id` | `departments.id` | RESTRICT | NO ACTION |

## 字段与索引

### categories

| 字段 | 类型 | 可空 | 默认值 | 键标记 | 附加属性 |
| --- | --- | --- | --- | --- | --- |
| `id` | `int` | 否 | NULL | PRI | auto_increment |
| `name` | `varchar(80)` | 否 | NULL | UNI | — |
| `description` | `varchar(255)` | 否 | NULL | — | — |

索引：

- `name`：唯一索引，字段 `(name)`。
- `PRIMARY`：主键，字段 `(id)`。

### departments

| 字段 | 类型 | 可空 | 默认值 | 键标记 | 附加属性 |
| --- | --- | --- | --- | --- | --- |
| `id` | `int` | 否 | NULL | PRI | auto_increment |
| `name` | `varchar(80)` | 否 | NULL | UNI | — |
| `code` | `varchar(20)` | 否 | NULL | UNI | — |
| `contact` | `varchar(80)` | 否 | NULL | — | — |
| `created_at` | `datetime` | 否 | NULL | — | — |

索引：

- `code`：唯一索引，字段 `(code)`。
- `name`：唯一索引，字段 `(name)`。
- `PRIMARY`：主键，字段 `(id)`。

### equipment

| 字段 | 类型 | 可空 | 默认值 | 键标记 | 附加属性 |
| --- | --- | --- | --- | --- | --- |
| `id` | `int` | 否 | NULL | PRI | auto_increment |
| `asset_no` | `varchar(40)` | 否 | NULL | UNI | — |
| `name` | `varchar(100)` | 否 | NULL | — | — |
| `model` | `varchar(100)` | 否 | NULL | — | — |
| `serial_no` | `varchar(100)` | 否 | NULL | — | — |
| `category_id` | `int` | 否 | NULL | MUL | — |
| `department_id` | `int` | 否 | NULL | MUL | — |
| `location` | `varchar(100)` | 否 | NULL | — | — |
| `custodian` | `varchar(80)` | 否 | NULL | — | — |
| `purchase_date` | `date` | 否 | NULL | MUL | — |
| `purchase_price` | `decimal(12,2)` | 否 | NULL | — | — |
| `status` | `varchar(20)` | 否 | NULL | — | — |
| `notes` | `text` | 否 | NULL | — | — |
| `created_at` | `datetime` | 否 | NULL | — | — |
| `updated_at` | `datetime` | 否 | NULL | — | — |

索引：

- `asset_no`：唯一索引，字段 `(asset_no)`。
- `category_id`：普通索引，字段 `(category_id)`。
- `ix_equipment_dept_status`：普通索引，字段 `(department_id, status)`。
- `ix_equipment_purchase`：普通索引，字段 `(purchase_date)`。
- `PRIMARY`：主键，字段 `(id)`。

### maintenance_records

| 字段 | 类型 | 可空 | 默认值 | 键标记 | 附加属性 |
| --- | --- | --- | --- | --- | --- |
| `id` | `int` | 否 | NULL | PRI | auto_increment |
| `equipment_id` | `int` | 否 | NULL | MUL | — |
| `reported_by` | `int` | 否 | NULL | MUL | — |
| `issue` | `varchar(1000)` | 否 | NULL | — | — |
| `technician` | `varchar(80)` | 否 | NULL | — | — |
| `cost` | `decimal(12,2)` | 否 | NULL | — | — |
| `status` | `varchar(20)` | 否 | NULL | — | — |
| `previous_status` | `varchar(20)` | 否 | NULL | — | — |
| `resolution` | `varchar(1000)` | 否 | NULL | — | — |
| `started_at` | `datetime` | 否 | NULL | — | — |
| `completed_at` | `datetime` | 是 | NULL | — | — |

索引：

- `ix_maintenance_equipment_status`：普通索引，字段 `(equipment_id, status)`。
- `PRIMARY`：主键，字段 `(id)`。
- `reported_by`：普通索引，字段 `(reported_by)`。

### operation_logs

| 字段 | 类型 | 可空 | 默认值 | 键标记 | 附加属性 |
| --- | --- | --- | --- | --- | --- |
| `id` | `int` | 否 | NULL | PRI | auto_increment |
| `user_id` | `int` | 否 | NULL | MUL | — |
| `department_id` | `int` | 是 | NULL | MUL | — |
| `action` | `varchar(80)` | 否 | NULL | — | — |
| `resource` | `varchar(80)` | 否 | NULL | — | — |
| `detail` | `varchar(500)` | 否 | NULL | — | — |
| `created_at` | `datetime` | 否 | NULL | — | — |

索引：

- `ix_log_department_created`：普通索引，字段 `(department_id, created_at)`。
- `PRIMARY`：主键，字段 `(id)`。
- `user_id`：普通索引，字段 `(user_id)`。

### users

| 字段 | 类型 | 可空 | 默认值 | 键标记 | 附加属性 |
| --- | --- | --- | --- | --- | --- |
| `id` | `int` | 否 | NULL | PRI | auto_increment |
| `username` | `varchar(40)` | 否 | NULL | UNI | — |
| `display_name` | `varchar(80)` | 否 | NULL | — | — |
| `password_hash` | `varchar(255)` | 否 | NULL | — | — |
| `role` | `varchar(20)` | 否 | NULL | — | — |
| `department_id` | `int` | 是 | NULL | MUL | — |
| `active` | `tinyint(1)` | 否 | NULL | — | — |
| `token_version` | `int` | 否 | NULL | — | — |
| `created_at` | `datetime` | 否 | NULL | — | — |

索引：

- `department_id`：普通索引，字段 `(department_id)`。
- `PRIMARY`：主键，字段 `(id)`。
- `username`：唯一索引，字段 `(username)`。

`PRI` 为主键，`UNI` 为唯一键，`MUL` 表示列被非唯一索引覆盖；完整复合索引以每张表的索引列表为准。默认值 `NULL` 是 MySQL 元数据返回值，并不表示非空字段插入时可以省略。

结构原始快照保存在被 Git 忽略的 `test-output/db-inspection/stu626-schema.json`；对应建表定义保存在同目录的 `stu626-schema.sql`。这些文件不包含数据库密码或业务记录。
