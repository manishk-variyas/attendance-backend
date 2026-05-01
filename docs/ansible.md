# Ansible Automation Guide

This guide teaches you Ansible from scratch and shows how to automate setting up Keycloak users and other services like Redmine on your VM.

---

## Part 1: Ansible Basics (Beginner)

### What is Ansible?

Ansible is a tool that **runs commands on remote computers automatically**. You write a "playbook" (a list of tasks) on your local machine, and Ansible executes them on your VM.

**Why use it?**
- No more manual copy-paste commands
- Run entire setup in one command
- Replayable - same result every time
- Documentation - your playbook IS your documentation

### Core Concepts

| Concept | What it means |
|---------|-------------|
| **Control Node** | Your local machine (where you run ansible) |
| **Managed Node** | Remote machine (your VM) - connect via SSH |
| **Target** | Docker containers **inside** the VM |
| **Inventory** | List of IPs/hosts to target |
| **Playbook** | YAML file with tasks to run |
| **Module** | Built-in command (docker_exec, shell, etc.) |
| **docker_exec** | Ansible module to run commands INSIDE Docker containers |

### How It Works

```
Your Laptop (Ansible)  --SSH-->  VM (docker)  --docker exec-->  Keycloak Container
```

Ansible connects to your VM via SSH
2. Then uses `docker exec` commands inside containers
3. Commands run inside the container (where Keycloak is)

### Container Names (from our docker-compose)

These are the containers inside your VM:

| Container | Service | What it does |
|-----------|---------|-------------|
| `infra-keycloak-1` | keycloak | Identity provider |
| `infra-keycloak-db-1` | keycloak-db | PostgreSQL for Keycloak |
| `infra-redis-1` | redis | Session storage |
| `infra-backend-1` | backend | Our FastAPI app |
| `infra-nginx-1` | nginx | Reverse proxy |

Use these names in Ansible tasks to target specific containers.

### Connection Flow

```bash
# 1. Ansible connects to VM via SSH
ansible -i inventory.ini vm -m ping

# 2. Then executes commands inside containers
# This runs INSIDE the Keycloak container:
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh ...
```

### Your First Ansible Command

Ping your VM to test connection:

```bash
ansible -i inventory.ini vm -m ping
```

Expected output:
```
vm | SUCCESS => {
    "changed": false,
    "ping": "pong"
}
```

### Inventory File

`inventory.ini` connects to your VM via SSH:

```ini
[vm]
attendance-vm ansible_host=192.168.122.101 ansible_user=app-backend ansible_ssh_private_key_file=~/.ssh/id_rsa

[vm:vars]
ansible_python_interpreter=/usr/bin/python3
```

- `vm` = group name (can have multiple machines)
- `attendance-vm` = hostname
- `ansible_host` = VM's IP address (connect via SSH)
- `ansible_user` = SSH username (your VM user)
- `ansible_ssh_private_key_file` = path to SSH key

### Simple Playbook

`hello.yml`:

```yaml
---
- name: Hello World Playbook
  hosts: vm
  gather_facts: no

  tasks:
    - name: Say hello
      debug:
        msg: "Hello from Ansible!"

    - name: Check uptime
      command: uptime
      register: uptime_output

    - name: Show uptime result
      debug:
        msg: "Uptime: {{ uptime_output.stdout }}"
```

Run it:
```bash
ansible-playbook -i inventory.ini hello.yml
```

### Anatomy of a Task

```yaml
- name: What this task does
  module_name:
    module_arg1: value1
    module_arg2: value2
  register: result_variable  # Store output
  when: condition         # Run only if true
  failed_when: false   # Don't fail on error
```

Common modules:
- `debug` - Print message
- `command` - Run shell command
- `docker_exec` - Run command in Docker container
- `docker_container_info` - Get container info
- `file` - Create files/directories
- `copy` - Copy files
- `template` - Copy file with variable substitution

### Variables

Define variables in playbook or separate file:

```yaml
- name: Example with variables
  hosts: vm

  vars:
    my_var: hello

  vars_files:
    - secrets.yml  # Load from file

  tasks:
    - name: Use variable
      debug:
        msg: "{{ my_var }}"
```

### Conditionals

Run task only if condition is true:

```yaml
- name: Create file only on Ubuntu
  file:
    path: /tmp/test
    state: touch
  when: ansible_os_family == "Debian"
```

### Loops

Run task multiple times:

```yaml
- name: Create multiple users
  user:
    name: "{{ item }}"
  loop:
    - alice
    - bob
    - charlie
```

### Error Handling

Don't fail on error:
```yaml
- name: Optional task
  docker_exec:
    container: mycontainer
    cmd: some-command
  failed_when: false
```

### Tags

Label tasks to run selectively:

```yaml
- name: Task A
  debug:
    msg: "A"
  tags: [a]

- name: Task B
  debug:
    msg: "B"
  tags: [b]
```

Run only tagged tasks:
```bash
ansible-playbook -i inventory.ini playbook.yml --tags a
```

---

## Part 2: Prerequisites

### 1. Install Ansible on your local machine

**macOS:**
```bash
brew install ansible
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ansible
```

**Windows (WSL):**
```bash
sudo apt update
sudo apt install ansible
```

Verify:
```bash
ansible --version
```

---

## Directory Structure

Create this structure:

```
ansible/
├── inventory.ini          # VM inventory
├── playbook.yml         # Main playbook
├── group_vars/
│   └── all.yml         # Shared variables
├── roles/
│   ├── keycloak/
│   │   ├── tasks/
│   │   │   └── main.yml
│   │   └── handlers/
│   │       └── main.yml
│   └── redmine/
│       ├── tasks/
│       │   └── main.yml
│       └── handlers/
│           └── main.yml
```

---

### Running Commands Inside Docker Containers

Ansible uses a special `community.docker.docker_exec` module to run commands **inside** containers:

```yaml
- name: Login to Keycloak admin
  community.docker.docker_exec:
    container: infra-keycloak-1
    cmd: >-
      /opt/keycloak/bin/kcadm.sh config credentials
      --server http://keycloak:8080
      --realm master
      --user admin
      --password admin
```

**Arguments:**
- `container` = container name
- `cmd` = command to run inside container
- `chdir` = working directory inside container
- `stdin` = input to pass

**Return values:**
- `stdout` = command output
- `stdout_lines` = output split by lines
- `rc` = return code (0 = success)

```yaml
- name: Get users
  community.docker.docker_exec:
    container: infra-keycloak-1
    cmd: /opt/keycloak/bin/kcadm.sh get users -r attendance-app
  register: users_output

- name: Show users
  debug:
    msg: "{{ users_output.stdout }}"
```

---

## Keycloak Full Setup Tasks

This is the complete Keycloak setup we use in production:

### Keycloak Role Variables

`ansible/roles/keycloak/vars/main.yml`:

```yaml
---
keycloak_realm: attendance-app
keycloak_url: http://keycloak:8080
keycloak_admin_user: admin
keycloak_admin_password: admin
keycloak_client_id: backend-client
keycloak_client_secret: best-practice-secret-12345
keycloak_container: infra-keycloak-1
```

### Keycloak Tasks

`ansible/roles/keycloak/tasks/main.yml`:

```yaml
---
# Keycloak Full Setup

- name: Login to Keycloak Admin CLI
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh config credentials
      --server {{ keycloak_url }}
      --realm master
      --user {{ keycloak_admin_user }}
      --password {{ keycloak_admin_password }}

- name: Create {{ keycloak_realm }} realm
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh create realms
      -s realm={{ keycloak_realm }} -s enabled=true
  register: realm_creation
  failed_when: false

- name: Create backend-client
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh create clients -r {{ keycloak_realm }}
      -s clientId={{ keycloak_client_id }}
      -s enabled=true
      -s publicClient=false
      -s secret={{ keycloak_client_secret }}
      -s directAccessGrantsEnabled=true
      -s "redirectUris=[\"*\"]"
      -s "webOrigins=[\"*\"]"
      -s "backchannelLogoutUrl=http://backend:8000/auth/backchannel-logout"
      -s backchannelLogoutSessionRequired=true
      -s backchannelLogoutRevokeOfflineSessions=true
  register: client_creation
  failed_when: false

- name: Disable Verify Profile required action (required for ROPC)
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh update
      authentication/required-actions/VERIFY_PROFILE
      -r {{ keycloak_realm }}
      -s enabled=false
  register: verify_profile
  failed_when: false

- name: Enable login events (audit logging)
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh update events/config
      -r {{ keycloak_realm }}
      -s eventsEnabled=true
      -s "eventsListeners=[\"jboss-logging\"]"
  register: events_config
  failed_when: false

- name: Enable login events storage
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh update events
      -r {{ keycloak_realm }}
      -s enabled=true
      -s storageEnabled=true
  register: events_storage
  failed_when: false

- name: Enable service accounts on backend-client
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh update clients/{{ keycloak_client_id }}
      -r {{ keycloak_realm }}
      -s serviceAccountsEnabled=true
  register: service_accounts
  failed_when: false

- name: Get service account user ID
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh get clients/{{ keycloak_client_id }}/service-account-user
      -r {{ keycloak_realm }}
  register: service_account_output
  changed_when: false
  failed_when: false

- name: Set service account UID fact
  set_fact:
    service_account_uid: "{{ (service_account_output.stdout | from_json).id }}"
  when: service_account_output.stdout is defined and service_account_output.stdout | from_json | length > 0

- name: Get realm-admin role ID
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh get roles/realm-admin
      -r {{ keycloak_realm }}
  register: realm_admin_role
  changed_when: false
  failed_when: false

- name: Set realm-admin role ID fact
  set_fact:
    realm_admin_role_id: "{{ (realm_admin_role.stdout | from_json | first).id }}"
  when: realm_admin_role.stdout is defined and realm_admin_role.stdout | from_json | length > 0

- name: Assign realm-admin role to service account
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh add-roles
      -r {{ keycloak_realm }}
      --uid {{ service_account_uid }}
      --cclientid realm-management
      --roleid {{ realm_admin_role_id }}
  when:
    - service_account_uid is defined
    - realm_admin_role_id is defined
  register: role_assignment
  failed_when: false

- name: Create user (if username provided)
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh create users -r {{ keycloak_realm }}
      -s username={{ user_username }}
      -s enabled=true
  when: user_username is defined
  register: user_create
  failed_when: false

- name: Set user password
  docker_exec:
    container: "{{ keycloak_container }}"
    cmd: >-
      /opt/keycloak/bin/kcadm.sh set-password -r {{ keycloak_realm }}
      --username {{ user_username }}
      --new-password {{ user_password }}
      --temporary=false
  when:
    - user_username is defined
    - user_password is defined
    - user_create.rc == 0
  register: password_set
  failed_when: false
```

---

## Usage Examples

### Full setup (realm + client + service account):

```bash
cd ansible
ansible-playbook -i inventory.ini playbook.yml --tags keycloak
```

### Create a user:

```bash
ansible-playbook -i inventory.ini playbook.yml \
  -e user_username=newuser \
  -e user_password=SecurePass123 \
  --tags user
```

### Enable service accounts only:

```bash
ansible-playbook -i inventory.ini playbook.yml --tags service-account
```

### Check Keycloak status:

```bash
ansible -i inventory.ini vm -m docker_container_info -a name=infra-keycloak-1
```

`ansible/inventory.ini`:

```ini
[vm]
attendance-vm ansible_host=192.168.122.101 ansible_user=app-backend ansible_ssh_private_key_file=~/.ssh/id_rsa

[vm:vars]
ansible_python_interpreter=/usr/bin/python3
```

---

## Step 2: Create Group Variables

`ansible/group_vars/all.yml`:

```yaml
---
# Keycloak settings
keycloak_realm: attendance-app
keycloak_url: http://keycloak:8080
keycloak_admin_user: admin
keycloak_admin_password: admin

# Client settings
keycloak_client_id: backend-client
keycloak_client_secret: best-practice-secret-12345

# Database settings
postgres_db: keycloak
postgres_user: keycloak
postgres_password: keycloak
```

---

## Step 3: Create Keycloak Role

`ansible/roles/keycloak/tasks/main.yml`:

```yaml
---
# Keycloak Automation Tasks

- name: Check if Keycloak container exists
  docker_container_info:
    name: infra-keycloak-1
  register: keycloak_container

- name: Enable service accounts on backend-client
  docker_exec:
    container: infra-keycloak-1
    cmd: >-
      /opt/keycloak/bin/kcadm.sh update clients/{{ keycloak_client_id }}
      -r {{ keycloak_realm }} -s serviceAccountsEnabled=true
  when: keycloak_container.exists

- name: Get service account user ID
  docker_exec:
    container: infra-keycloak-1
    cmd: >-
      /opt/keycloak/bin/kcadm.sh get clients/{{ keycloak_client_id }}/service-account-user
      -r {{ keycloak_realm }}
  register: service_account_output
  when: keycloak_container.exists

- name: Set service account user ID
  set_fact:
    service_account_uid: "{{ (service_account_output.stdout | from_json).id }}"
  when: keycloak_container.exists and service_account_output.stdout is defined

- name: Get realm-admin role ID
  docker_exec:
    container: infra-keycloak-1
    cmd: >-
      /opt/keycloak/bin/kcadm.sh get roles/realm-admin
      -r {{ keycloak_realm }}
  register: realm_admin_role
  when: keycloak_container.exists

- name: Set realm-admin role ID
  set_fact:
    realm_admin_role_id: "{{ (realm_admin_role.stdout | from_json | first).id }}"
  when: keycloak_container.exists and realm_admin_role.stdout is defined

- name: Assign realm-admin role to service account
  docker_exec:
    container: infra-keycloak-1
    cmd: >-
      /opt/keycloak/bin/kcadm.sh add-roles -r {{ keycloak_realm }}
      --uid {{ service_account_uid }}
      --cclientid realm-management
      --roleid {{ realm_admin_role_id }}
  when:
    - keycloak_container.exists
    - service_account_uid is defined
    - realm_admin_role_id is defined

- name: Create Keycloak user
  docker_exec:
    container: infra-keycloak-1
    cmd: >-
      /opt/keycloak/bin/kcadm.sh create users -r {{ keycloak_realm }}
      -s username={{ username }}
      -s enabled=true
  register: user_creation
  failed_when: false

- name: Set user password
  docker_exec:
    container: infra-keycloak-1
    cmd: >-
      /opt/keycloak/bin/kcadm.sh set-password -r {{ keycloak_realm }}
      --username {{ username }}
      --new-password {{ password }}
      --temporary=false
  when: user_creation.rc == 0
```

---

## Step 4: Create Redmine Role (Future)

`ansible/roles/redmine/tasks/main.yml`:

```yaml
---
# Redmine Automation Tasks

- name: Check if Redmine container exists
  docker_container_info:
    name: redmine
  register: redmine_container

- name: Create Redmine data volume
  docker_volume:
    name: redmine_data
  when: not redmine_container.exists

- name: Create Redmine database
  postgresql_db:
    name: redmine
    login_host: "{{ postgres_host | default('localhost') }}"
    login_user: "{{ postgres_user }}"
    login_password: "{{ postgres_password }}"
  when: not redmine_container.exists

- name: Pull Redmine image
  docker_image:
    name: redmine
    source: pull
  when: not redmine_container.exists

- name: Start Redmine container
  docker_container:
    name: redmine
    image: redmine:latest
    ports:
      - "3000:3000"
    env:
      REDMINE_DB_POSTGRESQL: postgres
      REDMINE_DB_DATABASE: redmine
      REDMINE_DB_USERNAME: "{{ postgres_user }}"
      REDMINE_DB_PASSWORD: "{{ postgres_password }}"
    volumes:
      - redmine_data:/usr/redmine/files
    restart_policy: always
  when: not redmine_container.exists
```

---

## Step 5: Create Main Playbook

`ansible/playbook.yml`:

```yaml
---
- name: Setup Infrastructure Services
  hosts: vm
  connection: docker
  gather_facts: yes
  become: yes

  vars_files:
    - group_vars/all.yml

  roles:
    - role: keycloak
      when: setup_keycloak | default(true)

  tasks:
    - name: Update Keycloak client
      include_role:
        name: keycloak
      vars:
        setup_keycloak: true

    - name: Setup Redmine (future)
      include_role:
        name: redmine
      when: setup_redmine | default(false)
```

---

## Usage

### Run all Keycloak tasks:

```bash
cd ansible
ansible-playbook -i inventory.ini playbook.yml --tags keycloak
```

### Create a single user:

```bash
ansible-playbook -i inventory.ini playbook.yml \
  -e username=newuser \
  -e password=SecurePass123 \
  --tags user
```

### Enable service accounts only:

```bash
ansible-playbook -i inventory.ini playbook.yml \
  --tags service-account
```

---

## Manual Commands Reference

If you need to run Keycloak commands manually:

### Login to Keycloak admin:
```bash
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh config credentials \
  --server http://localhost:8080 --realm master --user admin --password admin
```

### List users:
```bash
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh get users -r attendance-app
```

### Create user:
```bash
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh create users -r attendance-app \
  -s username=username -s enabled=true
```

### Set password:
```bash
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh set-password -r attendance-app \
  --username username --new-password password --temporary=false
```

### Delete user:
```bash
docker exec infra-keycloak-1 /opt/keycloak/bin/kcadm.sh delete users/USER_ID -r attendance-app
```

---

## Automating User Creation

Create a simple script `ansible/create-user.yml`:

```yaml
---
- name: Create Keycloak User
  hosts: vm
  connection: docker
  gather_facts: no

  vars:
    username: "{{ user_name }}"
    password: "{{ user_pass }}"

  tasks:
    - name: Create user in Keycloak
      docker_exec:
        container: infra-keycloak-1
        cmd: >-
          /opt/keycloak/bin/kcadm.sh create users -r attendance-app
          -s username={{ username }} -s enabled=true

    - name: Set user password
      docker_exec:
        container: infra-keycloak-1
        cmd: >-
          /opt/keycloak/bin/kcadm.sh set-password -r attendance-app
          --username {{ username }}
          --new-password {{ password }}
          --temporary=false
```

Run:
```bash
ansible-playbook -i inventory.ini create-user.yml \
  -e user_name=john -e user_pass=Password123
```

---

## Part 3: More Concepts

### Handlers

Handlers are tasks that only run if something changed:

```yaml
tasks:
    - name: Update config file
      copy:
        src: config.yml
        dest: /tmp/config.yml
      notify: restart service  # Trigger handler

handlers:
    - name: restart service
      command: docker restart mycontainer
```

The `notify` only triggers the handler if the file actually changed.

### Check Mode (Dry Run)

Preview what would happen without making changes:

```bash
ansible-playbook -i inventory.ini playbook.yml --check
```

### Verbose Output

See more detail:

```bash
ansible-playbook -i inventory.ini playbook.yml -v      # verbose
ansible-playbook -i inventory.ini playbook.yml -vv     # more detail
ansible-playbook -i inventory.ini playbook.yml -vvv    # debug level
```

### Running on Specific Hosts

Run only on certain hosts from inventory:

```bash
ansible-playbook -i inventory.ini playbook.yml --limit production
```

### Ansible Vault (Secrets)

Store sensitive passwords securely:

```bash
# Create encrypted file
ansible-vault create secrets.yml

# Edit encrypted file
ansible-vault edit secrets.yml

# Use in playbook
ansible-playbook -i inventory.ini playbook.yml --ask-vault-pass
```

---

## Troubleshooting

### Test connection:
```bash
ansible -i inventory.ini vm -m ping
```

### Check container status:
```bash
ansible -i inventory.ini vm -m docker_container_info -a name=infra-keycloak-1
```

### View Keycloak logs:
```bash
docker logs infra-keycloak-1
```

### Restart container:
```bash
docker restart infra-keycloak-1
```

### Debug SSH issues:
```bash
ansible -i inventory.ini vm -m setup -vvv
```

---

## Quick Reference

| Command | What it does |
|---------|-------------|
| `ansible -i inventory.ini group -m ping` | Test connection |
| `ansible-playbook -i inventory.ini playbook.yml` | Run playbook |
| `ansible-playbook -i inventory.ini playbook.yml --check` | Dry run |
| `ansible-playbook -i inventory.ini playbook.yml --tags tagname` | Run tagged tasks |
| `ansible-playbook -i inventory.ini playbook.yml --start-at-task taskname` | Start from specific task |
| `ansible-vault create secrets.yml` | Create secrets file |