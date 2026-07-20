from slave_bot import config, daemon, state


def test_halt_resume_and_status_use_slave_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "slave-state.json")

    assert daemon.main(["--halt", "test maintenance"]) == 0
    current = state.load()
    assert current.halted is True
    assert current.halt_reason == "test maintenance"

    assert daemon.main(["--status"]) == 0
    assert "halted=True" in capsys.readouterr().out

    assert daemon.main(["--resume"]) == 0
    current = state.load()
    assert current.halted is False
    assert current.halt_reason is None
