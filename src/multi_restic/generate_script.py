import logging
from datetime import datetime
from string import Template

from dotenv import dotenv_values

from multi_restic.types import AgentConfig, RepositoryConfig


def generate_env_files(agent: AgentConfig) -> dict[str, str]:
    env_values = dotenv_values()
    env_files = {}
    for (repo_name, repo) in agent.get("repositories", {}).items():
        env_files[repo_name] = f"# Environment variables for repository {repo_name}\n"
        env_files[repo_name] += f"export RESTIC_REPOSITORY={repo.get('endpoint')}\n"
        for env_var in repo.get("env_vars", []):
            (prefix, *rest) = env_var.split(":")
            match prefix:
                case "plain":
                    value = ":".join(rest)
                    env_files[repo_name] += f"export {value}\n"
                case "env":
                    if len(rest) == 1:
                        env_files[repo_name] += f"export {rest[0]}={env_values[rest[0]]}\n"
                    elif len(rest) == 2:
                        env_files[repo_name] += f"export {rest[1]}={env_values[rest[0]]}\n"
    return env_files

def generate_repository_script(agent: AgentConfig, repo_name: str, repo: RepositoryConfig) -> str:
    script = f"""# START {repo_name}
source {agent.get("install_location", "/opt/multi-restic")}/.env.{repo_name}

if ! restic cat config >/dev/null 2>&1; then
    # Restic automatically does `mkdir -p` if necessary
    restic init
fi

restic backup {" ".join(agent.get("to_backup", []))}
"""
    if repo.get("forget_arguments"):
        script += f"restic forget {repo.get("forget_arguments")} --prune\n"
    script += f"# END {repo_name}\n"
    logging.getLogger(__name__).debug(f"Generated script for repository {repo_name}:\n{script}")
    return script

def generate_script(agent: AgentConfig) -> str:
    template = Template("""#!/bin/bash -e
# Generated on ${date} by multi-restic

export PATH=${install_location}:$$PATH
cd ${backup_root}

# Pre-backup commands
${pre_commands}

${repository_scripts}
# Post-backup commands
${post_commands}
""")
    return template.substitute(
        date=datetime.now().isoformat(),
        install_location=agent.get("install_location", "/opt/multi-restic"),
        backup_root=agent.get("backup_root"),
        pre_commands=" && ".join(agent.get("pre_command", [])) if agent.get("pre_command") else "",
        repository_scripts="".join([generate_repository_script(agent, repo_name, repo) for (repo_name, repo) in agent.get("repositories", {}).items()]),
        post_commands=" && ".join(agent.get("post_command", [])) if agent.get("post_command") else "",
    )
