from pathlib import Path

from exp_core.pool import ExperiencePool, PoolConfig


def test_pool_config_from_env_uses_consistent_storage_paths(monkeypatch, tmp_path):
    root = tmp_path / "root"
    db_path = tmp_path / "database" / "custom.db"
    trajectories = tmp_path / "payloads"
    monkeypatch.setenv("EXP_ROOT", str(root))
    monkeypatch.setenv("EXP_DB_PATH", str(db_path))
    monkeypatch.setenv("EXP_TRAJECTORIES_DIR", str(trajectories))

    config = PoolConfig.from_env()

    assert config.root == root
    assert config.db_path == db_path
    assert config.trajectories_dir == trajectories


def test_pool_creates_override_directories(monkeypatch, tmp_path):
    root = tmp_path / "root"
    db_path = tmp_path / "database" / "custom.db"
    trajectories = tmp_path / "payloads"
    monkeypatch.setenv("EXP_ROOT", str(root))
    monkeypatch.setenv("EXP_DB_PATH", str(db_path))
    monkeypatch.setenv("EXP_TRAJECTORIES_DIR", str(trajectories))

    pool = ExperiencePool()
    try:
        assert db_path.exists()
        assert trajectories.is_dir()
        assert pool.config.db_path == db_path
    finally:
        pool.close()


def test_pool_config_defaults_derive_from_root(monkeypatch, tmp_path):
    monkeypatch.setenv("EXP_ROOT", str(tmp_path))
    monkeypatch.delenv("EXP_DB_PATH", raising=False)
    monkeypatch.delenv("EXP_TRAJECTORIES_DIR", raising=False)

    config = PoolConfig.from_env()

    assert config.db_path == Path(tmp_path) / "pool.db"
    assert config.trajectories_dir == Path(tmp_path) / "trajectories"
