import json
import logging
import os
import threading
from datetime import datetime

import click
import paramiko
import tqdm

from multi_restic import generate_script
from multi_restic.config import Config
from multi_restic.schedule import Scheduler
from multi_restic.util import connect_ssh, exec_cmd

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

pass_config = click.make_pass_decorator(Config)

@click.group()
@click.option("--config", type=click.Path(exists=True), default="central.toml", help="Path to the configuration file.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable verbose output.")
def main(config, verbose) -> None:
    config = Config(config)
    click.get_current_context().obj = config
    if verbose:
        logger.setLevel(logging.DEBUG)

@main.command(short_help="Check the status of agents.")
@pass_config
def check(config: Config) -> None:
    """
    \b
    This will go through all agents defined in the configuration file and check:
     - SSH connectivity
     - If multi-restic is installed
     - If crontab is available
     - If restic is available
     - If the backup root exists
     - If the paths to backup exist
    
    No changes are made to the agents.
    """
    logger.info("Checking agents...")
    for (agent_name, agent_data) in config.get_agents().items():
        install_location = agent_data.get("install_location", "/opt/multi-restic")
        try:
            with connect_ssh(agent_data) as client:
                logger.info(f"✅ Connected to agent {agent_name} at {agent_data.get('ip')}:{agent_data.get('ssh_port', 22)} as {agent_data.get('ssh_user', 'root')}")
                if exec_cmd(client, f"ls {install_location}/backup.sh"):
                    logger.info("✅ Multi-restic is already installed.")
                else:
                    logger.info(f"❕ Agent {agent_name} is reachable but multi-restic is not installed.")

                if exec_cmd(client, "crontab -l"):
                    logger.info("✅ Crontab is available, will be used.")
                else:
                    logger.info("⚠️ Crontab seems to be unavailable. You can instead use systemd timers.")

                _, stdout, _ = client.exec_command(f"{install_location}/restic version")
                if stdout.channel.recv_exit_status() == 0:
                    logger.info(f"✅ Restic is available in PATH, will be used. (Version: {stdout.read().decode().strip()})")
                else:
                    _, stdout, _ = client.exec_command(f"{install_location}/restic version")
                    if stdout.channel.recv_exit_status() == 0:
                        logger.info(f"✅ Restic is available in multi-restic installation folder, will be used. (Version: {stdout.read().decode().strip()})")
                    else:
                        logger.info(f"⚠️ Restic seems to be unavailable. Will be downloaded to {install_location}/restic.")

                if exec_cmd(client, f"test -e {agent_data.get("backup_root")}"):
                    logger.info(f"✅ Backup root exists: {agent_data.get("backup_root")}")
                else:
                    logger.info(f"⚠️ Backup root does not exist: {agent_data.get("backup_root")}.")
                    continue # No point in checking further if backup root doesn't exist

                # Checking that folders to backup exist
                for item in agent_data.get("to_backup", []):
                    path = f"{agent_data.get("backup_root")}/{item}"
                    if exec_cmd(client, f"test -e {path}"):
                        logger.info(f"✅ Path to backup exists: {path}")
                    else:
                        logger.info(f"❌ Path to backup does not exist: {path} (is it generated from pre_command?)")
        except Exception:
            logger.exception(f"Failed to connect to agent {agent_name}")

@main.command(short_help="Install multi-restic on the agents.")
@click.argument("agents", nargs=-1)
@click.option("--skip-test-backup", is_flag=True, default=False, help="Skip the test backup after installation.")
@pass_config
def install(config: Config, agents: list[str], skip_test_backup: bool) -> None:
    """
    Install multi-restic on the agents.
    This command will create a backup in all repositories defined, to check their validity.

    If no AGENTS are specified, all agents in the configuration file will be processed.
    If AGENTS are specified, only those agents will be processed.
    """
    for (agent_name, agent_data) in config.get_agents().items():
        if len(agents) > 0 and agent_name not in agents:
            logger.debug(f"Skipping agent {agent_name} as it's not in the specified agents list.")
            continue
        install_location = agent_data.get("install_location", "/opt/multi-restic")

        try:
            client = connect_ssh(agent_data)
        except Exception:
            logger.exception(f"Failed to connect to agent {agent_name}")
            return

        with connect_ssh(agent_data) as client:
            logger.info(f"✅ Connected to agent {agent_name} at {agent_data.get('ip')}:{agent_data.get('ssh_port', 22)} as {agent_data.get('ssh_user', 'root')}")
            _, stdout, stderr = client.exec_command(f"install -d {install_location} -m 700") # As we add this folder to PATH, make it only accessible to the SSH user
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                logger.error(f"❌ Failed to create installation directory {install_location}. Does the SSH user have the necessary permissions?")
                logger.error(stdout.read().decode())
                logger.error(stderr.read().decode())
                break

            restic_in_path = exec_cmd(client, "restic version")
            restic_in_multi_restic = exec_cmd(client, f"{install_location}/restic version")
            download_restic = not restic_in_path and not restic_in_multi_restic
            if download_restic:
                logger.warning(f"⚠️ Restic is not available on the agent. Downloading restic to {install_location}/restic.")
                restic_url = "https://github.com/restic/restic/releases/download/v0.18.1/restic_0.18.1_linux_amd64.bz2"
                commands = [
                    f"cd {install_location}",
                    f"curl -LO {restic_url}",
                    "bzip2 -d restic_0.18.1_linux_amd64.bz2",
                    "mv restic_0.18.1_linux_amd64 restic",
                    "chmod +x restic",
                    "./restic self-update",
                ]
                _, stdout, stderr = client.exec_command(" && ".join(commands))
                exit_code = stdout.channel.recv_exit_status()
                if exit_code == 0:
                    logger.info("✅ Restic downloaded successfully.")
                else:
                    logger.error("❌ Failed to download restic.")
                    logger.error(stdout.read().decode())
                    logger.error(stderr.read().decode())
                    break

            # Upload environment files and backup script
            env_files = generate_script.generate_env_files(agent_data)
            backup_script = generate_script.generate_script(agent_data)
            with client.open_sftp() as sftp:
                for (repo_name, env_content) in env_files.items():
                    env_remote_path = f"{install_location}/.env.{repo_name}"
                    with sftp.file(env_remote_path, "w") as remote_file:
                        remote_file.write(env_content)
                    sftp.chmod(env_remote_path, 0o600)
                    logger.info(f"✅ Uploaded environment file for repository {repo_name} to {env_remote_path}")

                script_remote_path = f"{install_location}/backup.sh"
                with sftp.file(script_remote_path, "w") as remote_file:
                    remote_file.write(backup_script)
                sftp.chmod(script_remote_path, 0o700)
                logger.info(f"✅ Uploaded backup script to {script_remote_path}")

            if not skip_test_backup:
                # Run one backup to test
                logger.info(f"🚀 Running backup on agent {agent_name} to test configuration...")
                _, stdout, stderr = client.exec_command(f"{install_location}/backup.sh")
                exit_code = stdout.channel.recv_exit_status()
                if exit_code == 0:
                    logger.info(f"✅ Backup completed successfully on agent {agent_name}.")
                else:
                    logger.error(f"❌ Backup failed on agent {agent_name}. Please check the output below:")
                    logger.error(stdout.read().decode())
                    logger.error(stderr.read().decode())
                    logger.error(f"You can SSH into the agent: multi-restic ssh {agent_name} [REPO_NAME] to investigate further.")
                    return

            # Set up scheduling job
            scheduler = Scheduler.create(agent_data)
            scheduler.remove_schedule(agent_data, client)  # Remove existing schedule if any
            scheduler.add_schedule(agent_data, client)
            logger.info(f"✅ Scheduled backups on agent {agent_name} using {agent_data.get('scheduler', 'cron')}.")

@main.command("ssh", short_help="Open an SSH session to the specified agent.")
@click.argument("agent_name")
@click.argument("repository", required=False)
@click.argument("command", required=False, nargs=-1)
@click.option("--shell", required=False, show_default=True, default="/bin/bash", help="Shell to use on the remote agent.")
@pass_config
def ssh_agent(config: Config, agent_name: str, repository: str, command: list[str], shell: str) -> None:
    """
    Open an SSH session to the specified agent, with the env variables loaded.

    You can specify a REPOSITORY to load the environment for that repository.

    It uses the default SSH program on your system.
    """
    agent_data = config.get_agents().get(agent_name)
    if not agent_data:
        logger.error(f"Agent {agent_name} not found in configuration.")
        return

    install_location = agent_data.get("install_location", "/opt/multi-restic")
    commands = [
        f"export PATH={install_location}:$PATH",
        f"cd {agent_data.get('backup_root')}",
    ]
    if repository:
        if repository in agent_data.get("repositories", {}):
            commands.append(f"source {install_location}/.env.{repository}")
            logger.info(f"ℹ️ Loaded environment for repository {repository}.")
        else:
            logger.error(f"Repository {repository} not found for agent {agent_name}. Available repositories: {', '.join(agent_data.get('repositories', {}).keys())}")
            return
    else:
        logger.warning(f"ℹ️ No repository specified, but the restic command is available. You can see the available repositories by doing 'ls {install_location}/.env.*' and sourcing the one you want.")

    full_command = f"ssh {agent_data.get('ssh_user', 'root')}@{agent_data.get('ip')} -p {agent_data.get('ssh_port', 22)} -t {shell} -c '{'; '.join(commands)}; {shell} -i'"
    logger.debug(full_command)
    os.system(full_command)

@main.command(help="List snapshots in all repositories for the agent.")
@click.argument("agent_name")
@pass_config
def list_snapshots(config: Config, agent_name: str) -> None:
    agent_data = config.get_agents().get(agent_name)
    if not agent_data:
        logger.error(f"Agent {agent_name} not found in configuration.")
        return

    install_location = agent_data.get("install_location", "/opt/multi-restic")
    try:
        client = connect_ssh(agent_data)
    except Exception:
        logger.exception(f"Failed to connect to agent {agent_name}")
        return
    with client:
        for (repo_name, repo) in agent_data.get("repositories", {}).items():
            logger.info(f"Snapshots for repository {repo_name}:")
            commands = [
                f"export PATH={install_location}:$PATH",
                f"source {install_location}/.env.{repo_name}",
                "restic snapshots --json",
            ]
            _, stdout, stderr = client.exec_command(" && ".join(commands))
            exit_code = stdout.channel.recv_exit_status()
            if exit_code == 0:
                output = json.loads(stdout.read().decode())
                if len(output) == 0:
                    logger.info("  No snapshots found.")
                for snapshot in output:
                    time = datetime.fromisoformat(snapshot['time']).astimezone().strftime("%Y-%m-%d %H:%M:%S")
                    logger.info(f"  ID: {snapshot['short_id']}, Time: {time}, Host: {snapshot['hostname']}, Paths: {', '.join(snapshot['paths'])}")
            else:
                logger.error(f"❌ Failed to list snapshots for repository {repo_name}.")
                logger.error(stdout.read().decode())
                logger.error(stderr.read().decode())

@main.command(help="Download a snapshot from a repository.")
@click.argument("agent_name")
@click.argument("repository")
@click.argument("snapshot_ids", nargs=-1)
@click.option("--destination", type=click.Path(), default=".")
@pass_config
def download_snapshot(config: Config, agent_name: str, repository: str, snapshot_ids: list[str], destination: str) -> None:
    agent_data = config.get_agents().get(agent_name)
    if not agent_data:
        logger.error(f"Agent {agent_name} not found in configuration.")
        return

    install_location = agent_data.get("install_location", "/opt/multi-restic")
    with paramiko.SSHClient() as client:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=agent_data.get("ip"),
                port=int(agent_data.get("ssh_port", 22)),
                username=agent_data.get("ssh_user", "root"),
                timeout=3,
            )
        except Exception:
            logger.exception(f"Failed to connect to agent {agent_name}")
            return

        if repository not in agent_data.get("repositories", {}):
            logger.error(f"Repository {repository} not found for agent {agent_name}. Available repositories: {', '.join(agent_data.get('repositories', {}).keys())}")
            return

        threads: list[threading.Thread] = []
        for snapshot_id in snapshot_ids:
            def _export(snapshot_id):
                with connect_ssh(agent_data) as client_inner:
                    commands = [
                        f"export PATH={install_location}:$PATH",
                        f"source {install_location}/.env.{repository}",
                        f"rm -rf /tmp/multi-restic-restore-{snapshot_id} || true",
                        f"restic restore {snapshot_id} --target /tmp/multi-restic-restore-{snapshot_id}",
                    ]
                    logger.info(f"🚀 Restoring snapshot {snapshot_id} from repository {repository} on agent {agent_name}...")
                    _, stdout, stderr = client_inner.exec_command(" && ".join(commands))
                    exit_code = stdout.channel.recv_exit_status()
                    if exit_code != 0:
                        logger.error(f"❌ Failed to restore snapshot {snapshot_id} on agent {agent_name}.")
                        logger.error(stdout.read().decode())
                        logger.error(stderr.read().decode())
                        logger.error(f"You can SSH into the agent: multi-restic ssh {agent_name} --repository {repository} to investigate further.")
                        return
                    logger.info(f"✅ Snapshot {snapshot_id} restored successfully on agent {agent_name}, compressing...")
                    _, stdout, stderr = client_inner.exec_command(f"tar -czf /tmp/multi-restic-restore-{snapshot_id}.tar.gz -C /tmp/multi-restic-restore-{snapshot_id} .")
                    exit_code = stdout.channel.recv_exit_status()
                    if exit_code != 0:
                        logger.error(f"❌ Failed to compress restored snapshot {snapshot_id} on agent {agent_name}.")
                        logger.error(stdout.read().decode())
                        logger.error(stderr.read().decode())
                        logger.error(f"You can SSH into the agent: multi-restic ssh {agent_name} {repository} to investigate further.")
                        return
                    logger.info(f"✅ Snapshot {snapshot_id} compressed successfully!")

                    with client_inner.open_sftp() as sftp:
                        local_path = os.path.join(destination, f"{agent_name}-{repository}-{snapshot_id}.tar.gz")
                        remote_path = f"/tmp/multi-restic-restore-{snapshot_id}.tar.gz"
                        file_size = sftp.stat(remote_path).st_size
                        with sftp.file(remote_path, "rb") as remote_file, open(local_path, "wb") as local_file:
                            remote_file.prefetch() # This speeds up the download by A TON
                            with tqdm.tqdm(total=file_size, unit="B", unit_scale=True, desc=f"Downloading {local_path}") as pbar:
                                while True:
                                    data = remote_file.read(1048576)
                                    if not data:
                                        break
                                    local_file.write(data)
                                    pbar.update(len(data))
            t = threading.Thread(target=_export, args=(snapshot_id,))
            t.daemon = True
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        logger.info("✅ All snapshots downloaded successfully.")
