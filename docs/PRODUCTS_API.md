# 农资商店商品接口

现有 FastAPI 后端增加了200件农业商品、562张本地商品图片。商品不再来自 DummyJSON，价格、计价单位、所选规格、起批量、描述和来源信息均保留采集原文。

部署到现有云服务器后，服务根地址仍为 `http://170.106.137.89`。商品、识别、问答和知识库复用同一个后端。

| 请求 | 说明 |
| --- | --- |
| `GET /api/v1/products?limit=200&offset=0` | 全部200件商品，返回 `{products,total,offset,limit}` |
| `GET /api/v1/products?category=tools&q=剪&sort=price_asc` | 分类、关键词、价格排序 |
| `GET /api/v1/products/categories` | 七类及各类数量 |
| `GET /api/v1/products/hn-4253818` | 单件详情，包含报价说明、完整来源描述和图库 |
| `GET /api/v1/products/images/hn-4253818-01.jpg` | 本地原始商品图片，无需第三方图片服务 |

`limit` 默认24，范围1到200；`offset` 从0开始。`q` 支持单个汉字，也支持以空白分隔的多个词，所有词均需匹配。搜索范围包括名称、原始标题、规格、摘要、分类名称、商家和关键词。`sort` 为 `default`、`price_asc` 或 `price_desc`，按所选规格的单价排序，不跨单位换算。

类别为 `insect`、`disease`、`biological`、`tools`、`irrigation`、`fertilizer`、`supplies`。无匹配商品或分页已到末尾时返回空的 `products` 数组。不存在的商品和图片返回404；非法参数返回422；商品素材缺失或损坏返回503，错误码 `product_catalog_unavailable`。

图片地址在响应中为同域相对路径，前端将服务根地址与路径拼接。服务器只提供目录中被商品实际引用的图片，不公开任意文件。原始图片地址、尺寸和摘要另保存在 `source_image_urls`、`image_metadata`。

## 素材与配置

MySQL 模式从 `plant_health` 的 `product_categories`、`products`、`product_variants`、`product_images` 读取目录，采用 30 秒缓存。图片文件仍由 `Shop/data/images/` 提供；可通过 `PRODUCT_DATA_DIR` 指定素材目录，相对路径继续以 `Visual` 为基准。数据库保存原图来源、尺寸和摘要，数据库变化会在缓存到期后的查询中生效。

`products.json` 保留为初次迁移来源和离线测试素材。首次导入由 `python -m tools.migrate_mysql --apply` 完成，并核对全部图片摘要；目标库必须为空。后续目录更新需要在事务中同步商品、规格和图库记录，并同时提供对应图片文件。启动方法仍为根目录的 `start.ps1` / `run.py`。

## 前端显示

鸿蒙商品服务复用识园页的 `recognition.baseUrl` 连接设置，默认连接现有云服务器。完整加载200件后，分类、搜索和价格排序在本地列表上操作。详情支持多图、来源链接、计价单位和起批量。

接口不编造库存、评分、销量、原价或折扣。购物车是毕业设计演示，按批数计算：单价 × 来源起批量 × 批数，金额未含运费。存在区间价时采用最低公开报价估算。

## 验证

在 `RearEnd` 使用现有虚拟环境运行 `python -m pytest -q`。商品测试覆盖200件数据、562张图片实际请求及SHA256、分页、类别、关键词、价格、异常输入、路径安全和原接口共存。另在鸿蒙工具链执行 `assembleHap` 验证前端。
