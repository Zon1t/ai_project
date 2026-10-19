from sr_agent import config


def test_paths_point_to_project():
    assert (config.PROJECT_ROOT / "PRD.md").is_file()
    assert (config.TARGET_ROOT / config.TARGET_CONFIG_FILE).is_file()
    assert (config.TARGET_ROOT / config.TARGET_SRC_SUBDIR / config.TARGET_PACKAGE).is_dir()
    assert config.RUNS_DIR.parent == config.PROJECT_ROOT


def test_execution_limits():
    assert config.SUBPROCESS_TIMEOUT_S == 10
    assert isinstance(config.SEED, int)
    assert config.MAX_PATCH_RETRIES == 3


def test_model_ids():
    assert config.MODEL == "gpt-5"
    assert config.MODEL_BULK == "gpt-6-luna"
