-- 生产路径 MySQL 8 DDL（开发运行时使用 SQLite，见 server/db.py 的 SCHEMA）。
-- 统一 utf8mb4；关键查询使用 (user_id_hash, ts) / (conversation_id, created_at) 复合索引。
-- 知识检索为本地结构化文档，Vector DB 集合与 metadata 过滤见 server/knowledge.py。

CREATE TABLE IF NOT EXISTS providers (
  id VARCHAR(48) PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  vendor VARCHAR(80) NOT NULL,
  unit VARCHAR(16) NOT NULL,
  used BIGINT NOT NULL DEFAULT 0,
  quota_limit BIGINT NOT NULL DEFAULT 0,
  reset_at VARCHAR(64) NOT NULL,
  endpoint VARCHAR(300) NOT NULL,
  tier VARCHAR(32) NOT NULL,
  free VARCHAR(64) NOT NULL,
  rpm INT NOT NULL DEFAULT 0,
  unlimited TINYINT(1) NOT NULL DEFAULT 0,
  limited TINYINT(1) NOT NULL DEFAULT 0,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  key_hint VARCHAR(32) NOT NULL DEFAULT 'sk-****7f3a',
  updated_at DATETIME NOT NULL,
  INDEX idx_providers_enabled (enabled, unlimited)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS quota_snapshots (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  provider_id VARCHAR(48) NOT NULL,
  used BIGINT NOT NULL,
  remaining BIGINT NOT NULL,
  captured_at DATETIME NOT NULL,
  INDEX idx_quota_provider_time (provider_id, captured_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  ts DATETIME NOT NULL,
  level VARCHAR(16) NOT NULL,
  kind VARCHAR(32) NOT NULL,
  message TEXT NOT NULL,
  provider_id VARCHAR(48) NULL,
  request_id VARCHAR(64) NULL,
  INDEX idx_events_ts (ts DESC),
  INDEX idx_events_request (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS request_logs (
  request_id VARCHAR(64) PRIMARY KEY,
  ts DATETIME NOT NULL,
  user_id_hash VARCHAR(32) NOT NULL,
  intent VARCHAR(32) NOT NULL,
  model VARCHAR(64) NOT NULL,
  prompt_version VARCHAR(48) NOT NULL,
  temperature DECIMAL(3,2) NOT NULL,
  max_tokens INT NOT NULL,
  timeout_ms INT NOT NULL,
  retrieval_hits INT NOT NULL DEFAULT 0,
  sources JSON,
  latency_ms INT NOT NULL,
  ttft_ms INT NOT NULL,
  token_usage INT NOT NULL DEFAULT 0,
  error_code VARCHAR(32) NULL,
  answer_digest VARCHAR(255),
  fallback_from VARCHAR(48),
  routed_by VARCHAR(32),
  INDEX idx_request_user_created (user_id_hash, ts),
  INDEX idx_request_model_ts (model, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS conversations (
  id VARCHAR(64) PRIMARY KEY,
  title VARCHAR(128) NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  total_tokens INT NOT NULL DEFAULT 0,
  request_count INT NOT NULL DEFAULT 0,
  switch_count INT NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS messages (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  conversation_id VARCHAR(64) NOT NULL,
  role ENUM('user','assistant','system') NOT NULL,
  content TEXT NOT NULL,
  model VARCHAR(64),
  request_id VARCHAR(64),
  sources JSON,
  created_at DATETIME NOT NULL,
  INDEX idx_messages_conv_created (conversation_id, created_at),
  CONSTRAINT fk_messages_conv FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS knowledge_docs (
  doc_id VARCHAR(64) PRIMARY KEY,
  title VARCHAR(128) NOT NULL,
  source VARCHAR(128) NOT NULL,
  url VARCHAR(300) NOT NULL,
  version VARCHAR(16) NOT NULL,
  lang VARCHAR(8) NOT NULL,
  tags JSON NOT NULL,
  content TEXT NOT NULL,
  updated_at DATETIME NOT NULL,
  INDEX idx_docs_metadata (lang, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS strategy (
  id INT PRIMARY KEY,
  current_id VARCHAR(48) NOT NULL,
  previous_id VARCHAR(48),
  auto_switch TINYINT(1) NOT NULL DEFAULT 1,
  threshold DECIMAL(4,3) NOT NULL DEFAULT 0.100,
  chain VARCHAR(255) NOT NULL,
  notify_in_app TINYINT(1) NOT NULL DEFAULT 1,
  notify_email TINYINT(1) NOT NULL DEFAULT 0,
  notify_webhook TINYINT(1) NOT NULL DEFAULT 0,
  switch_count INT NOT NULL DEFAULT 0,
  avoided INT NOT NULL DEFAULT 0,
  updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS state_flags (
  `key` VARCHAR(96) PRIMARY KEY,
  value VARCHAR(64) NOT NULL,
  updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
