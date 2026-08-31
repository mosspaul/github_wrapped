-- GitHub Wrapped schema.
--
-- Edit this file, then run:  python db/migrate.py
--
-- Rules of the road:
--   * Every statement must be idempotent -- this file is re-run constantly.
--   * CREATE TABLE IF NOT EXISTS for new tables.
--   * To change an EXISTING table, append an ALTER TABLE at the bottom.
--     IF NOT EXISTS will skip a table that already exists, so editing the
--     CREATE above does nothing on an existing database.
--   * Statements are split on ';' at end-of-line, so don't put a bare ';'
--     inside a string literal or a trigger body.

CREATE TABLE IF NOT EXISTS users (
  handle              VARCHAR(39)  NOT NULL,
  display_name        VARCHAR(255),
  profile_image_url   TEXT,
  bio                 TEXT,
  followers           INT          NOT NULL DEFAULT 0,
  public_repos        INT          NOT NULL DEFAULT 0,
  account_created_at  DATETIME,
  raw_json            JSON,
  fetched_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (handle)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS repos (
  id                BIGINT       NOT NULL AUTO_INCREMENT,
  handle            VARCHAR(39)  NOT NULL,
  name              VARCHAR(255) NOT NULL,
  description       TEXT,
  primary_language  VARCHAR(64),
  stars             INT          NOT NULL DEFAULT 0,
  forks             INT          NOT NULL DEFAULT 0,
  watchers          INT          NOT NULL DEFAULT 0,
  size_kb           INT          NOT NULL DEFAULT 0,
  is_fork           TINYINT(1)   NOT NULL DEFAULT 0,
  created_at        DATETIME,
  pushed_at         DATETIME,
  PRIMARY KEY (id),
  UNIQUE KEY uq_repos_handle_name (handle, name),
  KEY idx_repos_handle (handle),
  CONSTRAINT fk_repos_user FOREIGN KEY (handle)
    REFERENCES users (handle) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS repo_languages (
  repo_id   BIGINT       NOT NULL,
  language  VARCHAR(64)  NOT NULL,
  bytes     BIGINT       NOT NULL DEFAULT 0,
  PRIMARY KEY (repo_id, language),
  CONSTRAINT fk_langs_repo FOREIGN KEY (repo_id)
    REFERENCES repos (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Aggregated per repo per day, NOT one row per commit. A busy user has tens of
-- thousands of commits; this keeps both the GitHub calls and the table sane.
CREATE TABLE IF NOT EXISTS commit_history (
  id            BIGINT  NOT NULL AUTO_INCREMENT,
  repo_id       BIGINT  NOT NULL,
  commit_date   DATE    NOT NULL,
  commit_count  INT     NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uq_commits_repo_date (repo_id, commit_date),
  CONSTRAINT fk_commits_repo FOREIGN KEY (repo_id)
    REFERENCES repos (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS wrapped_jobs (
  handle      VARCHAR(39) NOT NULL,
  status      ENUM('pending','ingesting','computing','generating','ready','error')
              NOT NULL DEFAULT 'pending',
  error       TEXT,
  updated_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
              ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (handle)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS slides (
  handle        VARCHAR(39) NOT NULL,
  slide_type    VARCHAR(64) NOT NULL,
  stats_json    JSON,
  html          LONGTEXT,
  generated_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (handle, slide_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- ALTER TABLE statements go below this line.
-- MySQL has no "ADD COLUMN IF NOT EXISTS", so guard them, e.g.:
--
--   SET @c := (SELECT COUNT(*) FROM information_schema.COLUMNS
--              WHERE TABLE_SCHEMA = DATABASE()
--                AND TABLE_NAME = 'repos' AND COLUMN_NAME = 'topics')
--   SET @s := IF(@c = 0, 'ALTER TABLE repos ADD COLUMN topics JSON', 'SELECT 1')
--   PREPARE stmt FROM @s
--   EXECUTE stmt
--   DEALLOCATE PREPARE stmt
--
-- (note: no trailing semicolons inside that block -- the migrator splits on ';')
-- ---------------------------------------------------------------------------
