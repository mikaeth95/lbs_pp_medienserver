CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('DE', 'EN')),
    duration_seconds INTEGER NOT NULL CHECK (duration_seconds > 0),
    year INTEGER NOT NULL,
    video_path TEXT NOT NULL UNIQUE,
    thumbnail_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_videos_topic ON videos(topic);
CREATE INDEX IF NOT EXISTS idx_videos_language ON videos(language);
