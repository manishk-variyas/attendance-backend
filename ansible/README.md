# Ansible Automation for Infrastructure

This folder contains Ansible playbooks to automate Keycloak setup and user management.

---

## Quick Start

### 1. Test Connection

```bash
cd ansible
ansible -i inventory.ini vm -m ping
```

### 2. Full Keycloak Setup

```bash
ansible-playbook -i inventory.ini playbook.yml --tags keycloak
```

### 3. Create a User

```bash
ansible-playbook -i inventory.ini playbook.yml \
  -e user_username=john \
  -e user_password=SecurePass123 \
  --tags user
```

---

## Directory Structure

```
ansible/
├── inventory.ini              # VM inventory
├── playbook.yml             # Main playbook
├── group_vars/
│   └── all.yml             # Shared variables
├── README.md              # This file
└── roles/
    ├── keycloak/
    │   ├── tasks/
    │   │   ├── main.yml       # Full Keycloak setup
    │   │   └── create_user.yml
    │   ├── handlers/
    │   │   └── main.yml
    │   └── vars/
    │       └── main.yml
    └── redmine/
        └── tasks/
            └── main.yml
```

---

## Requirements

Install the Docker collection for Ansible:

```bash
ansible-galaxy collection install community.docker
```

---

## Variables

Edit `group_vars/all.yml` to change settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `keycloak_realm` | attendance-app | Realm name |
| `keycloak_client_id` | backend-client | Client ID |
| `keycloak_client_secret` | best-practice-secret-12345 | Client secret |
| `keycloak_container` | infra-keycloak-1 | Container name |

---

## Tags

| Tag | What it does |
|-----|-------------|
| `keycloak` | Full Keycloak setup |
| `user` | Create a user |
| `setup` | Initial setup |
| `redmine` | Redmine (future) |

---

## Example Commands

### Check Keycloak container status

```bash
ansible -i inventory.ini vm -m community.docker.docker_container_info -a name=infra-keycloak-1
```

### View Keycloak logs

```bash
ansible -i inventory.ini vm -m command -a "docker logs infra-keycloak-1"
```

### Run only specific tasks (dry run)

```bash
ansible-playbook -i inventory.ini playbook.yml --check
```

### Verbose output

```bash
ansible-playbook -i inventory.ini playbook.yml -v
```

---

## Files to Edit Before Running

1. **inventory.ini** - Update `ansible_host` with your VM's IP
2. **group_vars/all.yml** - Update secrets if needed

---

## Troubleshooting

### SSH Key Issues

If you get "Permission denied", check your SSH key:

```bash
ssh -i ~/.ssh/id_rsa app-backend@192.168.122.101
```

### Docker Collection Missing

```bash
ansible-galaxy collection install community.docker
```

### Connection Refused

Make sure VM is running and SSH is accessible:

```bash
ping 192.168.122.101
ssh app-backend@192.168.122.101
```