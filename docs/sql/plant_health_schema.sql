-- Initial schema, provisioned and verified on MySQL 8.0.46 on 2026-10-08.
-- Initial schema for a NEW plant_health database; not a migration for stu626.
-- Target: MySQL 8.0.46, InnoDB, utf8mb4. Application timestamps are UTC.
-- Existing API UUIDs and hn-* product identifiers are preserved.

CREATE DATABASE plant_health CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE plant_health;
SET time_zone = '+00:00';

CREATE TABLE users (
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    username VARCHAR(40) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL,
    password_hash VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    nickname VARCHAR(20) NOT NULL,
    phone VARCHAR(30) NOT NULL DEFAULT '',
    bio VARCHAR(80) NOT NULL DEFAULT '',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='平台用户及个人资料';

CREATE TABLE auth_sessions (
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    user_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    expires_at DATETIME(6) NOT NULL,
    PRIMARY KEY (token_hash),
    KEY ix_auth_sessions_user (user_id),
    KEY ix_auth_sessions_expiry (expires_at),
    CONSTRAINT fk_auth_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='登录令牌摘要及过期时间';

CREATE TABLE recognitions (
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    user_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    filename VARCHAR(255) NOT NULL,
    image_width INT UNSIGNED NOT NULL,
    image_height INT UNSIGNED NOT NULL,
    model_name VARCHAR(255) NOT NULL,
    leaf_check JSON NOT NULL,
    postprocessor VARCHAR(8) NOT NULL,
    confidence_threshold DECIMAL(9,8) NOT NULL,
    requested_top_k SMALLINT UNSIGNED NOT NULL,
    status VARCHAR(16) NOT NULL,
    inference_ms DECIMAL(12,3) NOT NULL,
    elapsed_ms DECIMAL(12,3) NOT NULL,
    result_image_path VARCHAR(1024) NULL,
    result_image_expires_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    KEY ix_recognitions_user_time (user_id, created_at, id),
    KEY ix_recognitions_image_expiry (result_image_expires_at),
    CONSTRAINT fk_recognitions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT ck_recognitions_dimensions CHECK (image_width > 0 AND image_height > 0),
    CONSTRAINT ck_recognitions_threshold CHECK (confidence_threshold BETWEEN 0 AND 1),
    CONSTRAINT ck_recognitions_top_k CHECK (requested_top_k BETWEEN 1 AND 50),
    CONSTRAINT ck_recognitions_status CHECK (status IN ('recognized', 'uncertain')),
    CONSTRAINT ck_recognitions_processor CHECK (postprocessor IN ('cpp', 'python')),
    CONSTRAINT ck_recognitions_elapsed CHECK (inference_ms >= 0 AND elapsed_ms >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='一次有效的叶片识别及图片元数据';

CREATE TABLE recognition_predictions (
    recognition_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    rank_no SMALLINT UNSIGNED NOT NULL,
    class_id INT UNSIGNED NOT NULL,
    class_name VARCHAR(255) NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    crop VARCHAR(80) NULL,
    condition_name VARCHAR(120) NULL,
    is_healthy BOOLEAN NULL,
    confidence DECIMAL(9,8) NOT NULL,
    PRIMARY KEY (recognition_id, rank_no),
    UNIQUE KEY uq_predictions_class (recognition_id, class_id),
    CONSTRAINT fk_predictions_recognition FOREIGN KEY (recognition_id) REFERENCES recognitions(id) ON DELETE CASCADE,
    CONSTRAINT ck_predictions_rank CHECK (rank_no BETWEEN 1 AND 50),
    CONSTRAINT ck_predictions_confidence CHECK (confidence BETWEEN 0 AND 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='识别候选及当时的类别标签快照';

CREATE TABLE conversations (
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    user_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    recognition_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    kind VARCHAR(16) NOT NULL,
    title VARCHAR(120) NOT NULL,
    context_snapshot JSON NOT NULL,
    revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
    expires_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_conversations_recognition (recognition_id),
    KEY ix_conversations_user_time (user_id, updated_at, id),
    KEY ix_conversations_expiry (expires_at),
    CONSTRAINT fk_conversations_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT fk_conversations_recognition FOREIGN KEY (recognition_id) REFERENCES recognitions(id) ON DELETE RESTRICT,
    CONSTRAINT ck_conversations_kind CHECK (kind IN ('recognition', 'general'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='识别咨询或纯文字咨询会话';

CREATE TABLE chat_messages (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    conversation_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    turn_no BIGINT UNSIGNED NOT NULL,
    role VARCHAR(16) NOT NULL,
    content MEDIUMTEXT NOT NULL,
    reply_source VARCHAR(16) NULL,
    model_name VARCHAR(100) NULL,
    elapsed_ms DECIMAL(12,3) NULL,
    usage_json JSON NULL,
    guard_checks JSON NULL,
    knowledge_activity JSON NULL,
    truncated BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_chat_messages_turn_role (conversation_id, turn_no, role),
    CONSTRAINT fk_chat_messages_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    CONSTRAINT ck_chat_messages_turn CHECK (turn_no >= 1),
    CONSTRAINT ck_chat_messages_role CHECK (role IN ('user', 'assistant')),
    CONSTRAINT ck_chat_messages_source CHECK (reply_source IS NULL OR reply_source IN ('deepseek', 'knowledge')),
    CONSTRAINT ck_chat_messages_elapsed CHECK (elapsed_ms IS NULL OR elapsed_ms >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='逐条问答及模型、Jev、知识活动记录';

CREATE TABLE knowledge_entries (
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    title VARCHAR(120) NOT NULL,
    crop VARCHAR(80) NOT NULL,
    condition_name VARCHAR(120) NOT NULL,
    question VARCHAR(400) NOT NULL,
    answer TEXT NOT NULL,
    applicability VARCHAR(600) NOT NULL,
    uncertainty VARCHAR(600) NOT NULL,
    keywords JSON NOT NULL,
    search_text MEDIUMTEXT NOT NULL,
    source_context_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    deepseek_model VARCHAR(100) NOT NULL,
    jev_model VARCHAR(100) NOT NULL,
    source_type VARCHAR(20) NOT NULL DEFAULT 'ai_summary',
    expert_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_knowledge_fingerprint (fingerprint),
    KEY ix_knowledge_crop_time (crop, created_at, id),
    KEY ix_knowledge_recent (created_at, id),
    FULLTEXT KEY ft_knowledge_search (search_text) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='经过整理与复核的通用植物知识';

CREATE TABLE message_knowledge_links (
    message_id BIGINT UNSIGNED NOT NULL,
    knowledge_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    relation_type VARCHAR(16) NOT NULL,
    match_probability DECIMAL(9,8) NULL,
    PRIMARY KEY (message_id, knowledge_id, relation_type),
    KEY ix_message_knowledge_entry (knowledge_id),
    CONSTRAINT fk_message_knowledge_message FOREIGN KEY (message_id) REFERENCES chat_messages(id) ON DELETE CASCADE,
    CONSTRAINT fk_message_knowledge_entry FOREIGN KEY (knowledge_id) REFERENCES knowledge_entries(id) ON DELETE RESTRICT,
    CONSTRAINT ck_message_knowledge_relation CHECK (relation_type IN ('source', 'reference', 'direct')),
    CONSTRAINT ck_message_knowledge_probability CHECK (match_probability IS NULL OR match_probability BETWEEN 0 AND 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识入库来源、引用与直接复用的消息关联';

CREATE TABLE product_categories (
    code VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    name VARCHAR(40) NOT NULL,
    sort_order SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (code),
    UNIQUE KEY uq_product_categories_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='现有七类农资商品分类';

CREATE TABLE products (
    id VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_id VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    category_code VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    name VARCHAR(255) NOT NULL,
    title VARCHAR(512) NOT NULL,
    spec VARCHAR(512) NOT NULL,
    price DECIMAL(18,6) NOT NULL,
    price_max DECIMAL(18,6) NOT NULL,
    currency CHAR(3) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'CNY',
    unit VARCHAR(64) NOT NULL,
    min_order DECIMAL(18,6) NOT NULL,
    min_order_text VARCHAR(255) NOT NULL,
    price_note TEXT NOT NULL,
    description TEXT NOT NULL,
    description_kind VARCHAR(80) NOT NULL,
    source_description MEDIUMTEXT NOT NULL,
    attributes JSON NOT NULL,
    keywords JSON NOT NULL,
    variant_scope VARCHAR(80) NOT NULL,
    image_scope VARCHAR(80) NOT NULL,
    main_image_position SMALLINT UNSIGNED NOT NULL,
    source_image_urls JSON NOT NULL,
    source_url VARCHAR(2048) NOT NULL,
    source_platform VARCHAR(80) NOT NULL,
    seller VARCHAR(255) NOT NULL,
    shipping_from VARCHAR(255) NOT NULL,
    fetched_at DATETIME(6) NOT NULL,
    fetched_at_original VARCHAR(64) NOT NULL,
    sort_order INT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_products_source (source_platform, source_id),
    KEY ix_products_category_default (category_code, sort_order, id),
    KEY ix_products_category_price (category_code, price, id),
    KEY ix_products_default (sort_order, id),
    KEY ix_products_price (price, id),
    CONSTRAINT fk_products_category FOREIGN KEY (category_code) REFERENCES product_categories(code) ON DELETE RESTRICT,
    CONSTRAINT ck_products_price CHECK (price > 0 AND price_max >= price),
    CONSTRAINT ck_products_min_order CHECK (min_order > 0),
    CONSTRAINT ck_products_currency CHECK (currency = 'CNY')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='公开农资报价、展示规格与完整来源信息';

CREATE TABLE product_variants (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    position SMALLINT UNSIGNED NOT NULL,
    name VARCHAR(512) NOT NULL,
    price DECIMAL(18,6) NOT NULL,
    price_max DECIMAL(18,6) NOT NULL,
    unit VARCHAR(64) NOT NULL,
    price_text VARCHAR(255) NOT NULL,
    min_order DECIMAL(18,6) NULL,
    min_order_text VARCHAR(255) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_product_variants_position (product_id, position),
    CONSTRAINT fk_product_variants_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    CONSTRAINT ck_product_variants_price CHECK (price > 0 AND price_max >= price),
    CONSTRAINT ck_product_variants_min_order CHECK (min_order IS NULL OR min_order > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品原始规格与各规格报价';

CREATE TABLE product_images (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    position SMALLINT UNSIGNED NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    source_url VARCHAR(2048) NOT NULL,
    width INT UNSIGNED NOT NULL,
    height INT UNSIGNED NOT NULL,
    sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_product_images_position (product_id, position),
    CONSTRAINT fk_product_images_product FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    CONSTRAINT ck_product_images_dimensions CHECK (width > 0 AND height > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品图库、原图来源与文件摘要';

-- Import seven category codes, 200 products, 451 variants and 562 images separately.
-- Application invariants documented in ../DATABASE_DESIGN.md are also required.
-- No credentials, imported business rows, root grants or stu626 changes are included.
