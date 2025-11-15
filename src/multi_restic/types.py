from typing import Literal, Optional, TypedDict


class ConfigData(TypedDict):
    agent: dict[str, 'AgentConfig']

class AgentConfig(TypedDict):
    ip: str
    ssh_port: Optional[str] = "22"
    ssh_user: Optional[str] = "root"
    install_location: Optional[str] = "/opt/multi-restic"
    scheduler: Literal['cron', 'systemd'] = 'cron'
    backup_root: str
    to_backup: list[str]
    repositories: dict[str, 'RepositoryConfig']
    pre_command: Optional[list[str]] = None
    post_command: Optional[list[str]] = None

class RepositoryConfig(TypedDict):
    endpoint: str
    env_vars: list[str]
    forget_arguments: Optional[str] = None
