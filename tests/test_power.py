from live_recorder import power


class FakeProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


def test_macos_prevents_system_sleep_but_not_display_sleep(monkeypatch):
    calls = []
    fake = FakeProcess()
    monkeypatch.setattr(power.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(power.shutil, "which", lambda _name: "/usr/bin/caffeinate")
    monkeypatch.setattr(power.subprocess, "Popen", lambda command, **kwargs: calls.append(command) or fake)

    preventer = power.SleepPreventer()
    preventer.start()

    assert calls == [["/usr/bin/caffeinate", "-i", "-w", str(power.os.getpid())]]
    assert "-d" not in calls[0]
    assert preventer.active

    preventer.stop()
    assert fake.terminated
