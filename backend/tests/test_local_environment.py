import os

from backend.app import load_local_environment


def test_local_file_overrides_old_shell_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "old-value")
    config = tmp_path / ".env.local"
    config.write_text("OPENAI_API_KEY=new-value\n", encoding="utf-8")

    load_local_environment(config)

    assert os.environ["OPENAI_API_KEY"] == "new-value"
