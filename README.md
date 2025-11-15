# Multi-restic

App that enables a central restic configuration and monitoring.

## Features
- Multi agent, all setup within a central configuration file
- Multi repository (one agent can have multiple repositories)
- Easy SSH into an agent, with everything setup to run manual restic commands
- List snapshots made by an agent
- Download a snapshot

TODO:
- [ ] Create backups remotely
- [ ] Service to check periodically that backups are done correctly, with notifications if not

## Flow
1. Write the configuration in central.toml
1. Run `multi-restic check` to check connectivity, steps that the script will run. This will check:
    1. SSH connectivity
    1. is the agent already installed
    1. cron availability
    1. restic availability
1. On central host, run `multi-restic install`. This will, for each agent:
    1. Install restic in the agent directory
    1. Install the bash script and env file (with correct rights)
    1. Install the crontab


## Goal
- Running the script is idempotent. Whatever the state of the agent, it should always end up in a correct configuration
- Atomicity would be nice
