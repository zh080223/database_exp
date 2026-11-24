CREATE TABLE IF NOT EXISTS scenes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    location TEXT NOT NULL,
    start_us BIGINT NOT NULL,
    end_us BIGINT NOT NULL CHECK (end_us > start_us)
);

CREATE TABLE IF NOT EXISTS regions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    min_x DOUBLE PRECISION NOT NULL,
    min_y DOUBLE PRECISION NOT NULL,
    max_x DOUBLE PRECISION NOT NULL CHECK (max_x > min_x),
    max_y DOUBLE PRECISION NOT NULL CHECK (max_y > min_y),
    reference_scene TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS track_points (
    id TEXT PRIMARY KEY,
    scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    timestamp_us BIGINT NOT NULL,
    x DOUBLE PRECISION NOT NULL,
    y DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    timestamp_us BIGINT NOT NULL,
    x DOUBLE PRECISION NOT NULL,
    y DOUBLE PRECISION NOT NULL,
    acceleration DOUBLE PRECISION NOT NULL,
    speed_before DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS keyframes (
    id TEXT PRIMARY KEY,
    scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    timestamp_us BIGINT NOT NULL,
    filename TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS scene_start_idx ON scenes (start_us);
CREATE INDEX IF NOT EXISTS point_scene_time_idx ON track_points (scene_id, timestamp_us);
CREATE INDEX IF NOT EXISTS event_scene_time_idx ON events (scene_id, timestamp_us);
CREATE INDEX IF NOT EXISTS frame_scene_time_idx ON keyframes (scene_id, timestamp_us);
