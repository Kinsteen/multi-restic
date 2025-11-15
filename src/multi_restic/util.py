import paramiko

from multi_restic.types import AgentConfig


def exec_cmd(client: 'paramiko.SSHClient', command: str) -> bool:
    """Execute a command on the remote SSH client and return stdout, stderr, and exit code."""
    _, stdout, _ = client.exec_command(command)
    exit_code = stdout.channel.recv_exit_status()
    return exit_code == 0


def connect_ssh(agent: AgentConfig) -> 'paramiko.SSHClient':
    """Establish an SSH connection to the specified IP, port, and user."""
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        agent.get("ip"),
        port=int(agent.get("ssh_port", "22")),
        username=agent.get("ssh_user", "root"),
        timeout=3
    )
    return client
