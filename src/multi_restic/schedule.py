

import logging
from typing import Self

import paramiko

from multi_restic.types import AgentConfig

logger = logging.getLogger(__name__)

class Scheduler:
    def create(agent: AgentConfig) -> Self:
        match agent.get("scheduler", "cron"):
            case "cron":
                return CronScheduler()
            case "systemd":
                return SystemdScheduler()
            case _:
                raise ValueError(f"Unknown scheduler type: {agent.get('scheduler')}")

class CronScheduler(Scheduler):
    def add_schedule(self, agent: AgentConfig, client: paramiko.SSHClient) -> None:
        install_location = agent.get("install_location", "/opt/multi-restic")
        cron_command = f"0 2 * * * {install_location}/backup.sh >> {install_location}/backup.log 2>&1"
        _, stdout, _ = client.exec_command("crontab -l 2>/dev/null")
        crontab_contents = stdout.read().decode()
        crontab_contents += f"# -- multi-restic -- (do not remove this comment!)\n{cron_command}"
        _, stdout, _ = client.exec_command(f'echo "{crontab_contents}" | crontab -')
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise RuntimeError("Failed to update crontab")

    def remove_schedule(self, agent: AgentConfig, client: paramiko.SSHClient) -> str:
        _, stdout, _ = client.exec_command("crontab -l 2>/dev/null")
        crontab_contents = stdout.read().decode()
        new_crontab = ""
        delete_next = False
        for line in crontab_contents.splitlines():
            if "# -- multi-restic --" in line.strip():
                delete_next = True
            elif delete_next:
                delete_next = False
            else:
                new_crontab += line + "\n"
        _, stdout, _ = client.exec_command(f'echo "{new_crontab.rstrip()}" | crontab -')
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise RuntimeError("Failed to update crontab")

class SystemdScheduler(Scheduler):
    def add_schedule(self, agent: AgentConfig, client: paramiko.SSHClient) -> str:
        install_location = agent.get("install_location", "/opt/multi-restic")
        unit_file_content = f"""[Unit]
Description=Multi-Restic Backup Service
[Service]
Type=oneshot
ExecStart={install_location}/backup.sh
"""
        timer_file_content = """[Unit]
Description=Runs Multi-Restic Backup Service daily
[Timer]
OnCalendar=daily
Persistent=true
[Install]
WantedBy=timers.target
"""
        service_file_path = f"{install_location}/multi-restic-backup.service"
        timer_file_path = f"{install_location}/multi-restic-backup.timer"

        with client.open_sftp() as sftp:
            with sftp.file(service_file_path, "w") as remote_file:
                remote_file.write(unit_file_content)
            with sftp.file(timer_file_path, "w") as remote_file:
                remote_file.write(timer_file_content)

        commands = [
            "mkdir -p ~/.config/systemd/user",
            f"ln -sf {service_file_path} ~/.config/systemd/user/multi-restic-backup.service",
            "systemctl --user daemon-reload",
            f"systemctl --user enable --now {install_location}/multi-restic-backup.timer",
        ]
        _, stdout, stderr = client.exec_command(" && ".join(commands))
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            logger.error(stdout.read().decode())
            logger.error(stderr.read().decode())
            raise RuntimeError("Failed to set up systemd timer")

    def remove_schedule(self, agent: AgentConfig, client: paramiko.SSHClient) -> str:
        install_location = agent.get("install_location", "/opt/multi-restic")
        service_file_path = f"{install_location}/multi-restic-backup.service"
        timer_file_path = f"{install_location}/multi-restic-backup.timer"

        commands = [
            "systemctl --user disable --now multi-restic-backup.timer || true",
            f"rm -f {service_file_path}",
            f"rm -f {timer_file_path}",
            "rm -f ~/.config/systemd/user/multi-restic-backup.service",
        ]
        _, stdout, stderr = client.exec_command(" && ".join(commands))
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            logger.error(stdout.read().decode())
            logger.error(stderr.read().decode())
            raise RuntimeError("Failed to remove systemd timer")
