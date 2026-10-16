from __future__ import annotations

import subprocess

import pytest

from ssu_lecture_downloader.ssh_tunnel import SshTunnel, TunnelError, build_ssh_command, is_valid_ssh_host


class FakeProcess:
    def __init__(self, *, exit_code: int | None = None) -> None:
        self.exit_code = exit_code
        self.terminated = False
        self.killed = False
        self.on_exit = lambda: None

    def poll(self):
        return self.exit_code

    def die(self, code: int = 1) -> None:
        self.exit_code = code
        self.on_exit()

    def terminate(self) -> None:
        self.terminated = True
        self.die(-15)

    def kill(self) -> None:
        self.killed = True
        self.die(-9)

    def wait(self, timeout=None):
        return self.exit_code


class Harness:
    """A tunnel whose ssh is a fake: it 'listens' on the local port as soon as it was spawned."""

    def __init__(self, *, exits_immediately: bool = False, preexisting: set[int] | None = None) -> None:
        self.commands: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.processes: list[FakeProcess] = []
        self.listening: set[int] = set(preexisting or ())
        self.exits_immediately = exits_immediately

    def popen(self, command, **kwargs):
        self.commands.append(command)
        self.kwargs.append(kwargs)
        process = FakeProcess(exit_code=255 if self.exits_immediately else None)
        self.processes.append(process)
        if not self.exits_immediately:
            port = int(command[command.index('-L') + 1].split(':')[0])
            self.listening.add(port)
            process.on_exit = lambda: self.listening.discard(port)
        return process

    def tunnel(self, **options) -> SshTunnel:
        return SshTunnel(
            popen=self.popen,
            is_listening=lambda port: port in self.listening,
            free_port=lambda: 45678,
            ssh_path=lambda: '/usr/bin/ssh',
            ready_timeout=1.0,
            register_atexit=False,
            **options,
        )


def test_command_is_an_argv_with_the_forward_and_safe_options():
    command = build_ssh_command('/usr/bin/ssh', 'macstudio', 8795, 18795)

    assert command[:2] == ['/usr/bin/ssh', '-N']
    assert ['-o', 'ExitOnForwardFailure=yes'] == command[2:4]
    assert ['-o', 'ServerAliveInterval=30'] == command[4:6]
    assert 'BatchMode=yes' in command
    assert command[-1] == 'macstudio'
    assert command.index('-L') < command.index('macstudio')
    assert command[command.index('-L') + 1] == '18795:127.0.0.1:8795'


@pytest.mark.parametrize('host', ['macstudio', 'mac.studio.local', 'user@host', '100.64.0.7', 'a'])
def test_valid_hosts(host):
    assert is_valid_ssh_host(host)


@pytest.mark.parametrize('host', ['', '-oProxyCommand=evil', '-v', 'two words', 'host;rm -rf', 'host\nname', '$(id)', 'a..b', 'host name', "ho'st", None, 5])
def test_invalid_hosts(host):
    assert not is_valid_ssh_host(host)
    with pytest.raises(ValueError):
        build_ssh_command('/usr/bin/ssh', host, 8795, 18795)  # type: ignore[arg-type]


@pytest.mark.parametrize('remote,local', [(0, 18795), (8795, 70000), (-1, 5), (True, 5)])
def test_invalid_ports_are_rejected(remote, local):
    with pytest.raises(ValueError):
        build_ssh_command('/usr/bin/ssh', 'host', remote, local)


def test_ensure_spawns_without_a_shell_and_reuses_a_live_tunnel():
    harness = Harness()
    tunnel = harness.tunnel()

    assert tunnel.ensure('macstudio', 8795, 18795) == 18795
    assert tunnel.ensure('macstudio', 8795, 18795) == 18795

    assert len(harness.commands) == 1
    assert isinstance(harness.commands[0], list)
    assert 'shell' not in harness.kwargs[0]
    assert harness.kwargs[0]['stdin'] == subprocess.DEVNULL
    assert tunnel.running() and tunnel.active_port() == 18795


def test_ensure_restarts_when_the_process_died_or_the_target_changed():
    harness = Harness()
    tunnel = harness.tunnel()
    tunnel.ensure('host-a', 8795, 18795)
    harness.processes[0].die()  # ssh died, its port closes

    tunnel.ensure('host-a', 8795, 18795)
    tunnel.ensure('host-b', 8795, 18795)

    assert len(harness.commands) == 3
    assert harness.commands[2][-1] == 'host-b'
    assert harness.processes[1].terminated


def test_missing_local_port_picks_a_free_one_and_keeps_it():
    harness = Harness()
    tunnel = harness.tunnel()

    port = tunnel.ensure('host', 8795, None)
    again = tunnel.ensure('host', 8795, None)

    assert port == again == 45678
    assert len(harness.commands) == 1
    assert harness.commands[0][harness.commands[0].index('-L') + 1] == '45678:127.0.0.1:8795'


def test_an_existing_listener_on_the_local_port_is_reused_without_spawning_ssh():
    harness = Harness(preexisting={18795})
    tunnel = harness.tunnel()

    assert tunnel.ensure('macstudio', 8795, 18795) == 18795

    assert harness.commands == []
    assert tunnel.running()
    tunnel.close()
    assert not tunnel.running()


def test_ssh_exiting_early_reports_its_error():
    harness = Harness(exits_immediately=True)
    tunnel = harness.tunnel()

    with pytest.raises(TunnelError) as caught:
        tunnel.ensure('host', 8795, 18795)

    assert caught.value.code == 'ssh_tunnel_failed'
    assert not tunnel.running()


def test_invalid_host_never_reaches_ssh():
    harness = Harness()
    tunnel = harness.tunnel()

    with pytest.raises(TunnelError) as caught:
        tunnel.ensure('-oProxyCommand=evil', 8795, 18795)

    assert caught.value.code == 'invalid_ssh_host'
    assert harness.commands == []


def test_close_terminates_the_process():
    harness = Harness()
    tunnel = harness.tunnel()
    tunnel.ensure('host', 8795, 18795)

    tunnel.close()

    assert harness.processes[0].terminated
    assert tunnel.active_port() is None
