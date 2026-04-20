# Oracle Management System - Full Implementation Plan

## Context

The `ibre5041.ansible_oracle_modules` collection (v3.3.0, 36 modules, 13 roles) covers the full Oracle stack. This plan describes how to build a **separate automation repository** (`oracle-automation`) that consumes this collection to manage a large enterprise Oracle estate (RAC + Standalone + Data Guard) across dev/staging/prod, integrated with AAP and HashiCorp Vault.

The collection provides modules for: OS prep, Grid Infrastructure, ASM, DBCA, PDBs, users, tablespaces, parameters, services, scheduler, patching, Data Guard. The automation repo adds the configuration model, orchestration roles, and playbooks on top.

---

## 1. Repository Structure

```
oracle-automation/
├── ansible.cfg
├── requirements.yml                    # ibre5041.ansible_oracle_modules >= 3.3.0
│
├── group_vars/
│   ├── all/
│   │   ├── oracle_defaults.yml         # Paths, OS users, oracle_homes dict
│   │   ├── profiles.yml                # Standard Oracle profiles (APP_PROFILE, SVC_PROFILE, etc.)
│   │   ├── parameters.yml              # Baseline init parameters (audit, security)
│   │   └── vault_lookups.yml           # Vault lookup plugin config (no secrets stored)
│   ├── dev.yml                         # Env overrides: archivelog=false, memory scaling
│   ├── staging.yml                     # Env overrides
│   ├── prod.yml                        # Env overrides: archivelog=true, flashback, large sizing
│   ├── rac.yml                         # oracle_install_type: rac, dbconfig_type: RAC
│   ├── standalone.yml                  # oracle_install_type: standalone, dbconfig_type: SI
│   └── dataguard.yml                   # DG defaults: archivelog=true, force_logging=true
│
├── databases/                          # Database catalog - one YAML file per database
│   ├── APPDB1.yml
│   ├── APPDB2.yml
│   ├── HRDB.yml
│   └── overlays/                       # Per-environment overrides (base + overlay merge)
│       ├── dev/
│       │   └── APPDB1.yml
│       ├── staging/
│       │   └── APPDB1.yml
│       └── prod/
│           ├── APPDB1.yml
│           └── HRDB.yml
│
├── roles/
│   ├── catalog_loader/                 # Load + merge catalog YAML, filter for current host
│   ├── oracle_database/                # Converge CDB to desired state (create via DBCA, configure)
│   ├── oracle_pdb_management/          # Create/open/close PDBs per catalog
│   ├── oracle_schema_management/       # Profiles -> Roles -> Users -> Grants (strict dependency order)
│   ├── oracle_tablespace_management/   # Tablespaces per PDB
│   ├── oracle_service_management/      # CRS services (RAC) or DBMS_SERVICE (standalone)
│   ├── oracle_parameter_management/    # 3-layer merge: baseline + env + catalog
│   ├── oracle_directory_management/    # Directory objects per PDB
│   ├── oracle_scheduler_management/    # DBMS_SCHEDULER jobs, classes, schedules, windows
│   ├── oracle_patching/                # OPatch + datapatch rolling workflow
│   └── oracle_dataguard/               # DG broker setup, switchover, failover
│
├── playbooks/
│   ├── converge.yml                    # Master converge - all roles, use tags to scope
│   ├── install_infrastructure.yml      # OS prep + GI + DB homes (wraps existing collection roles)
│   ├── create_database.yml
│   ├── manage_pdbs.yml
│   ├── manage_schemas.yml
│   ├── manage_tablespaces.yml
│   ├── manage_parameters.yml
│   ├── manage_services.yml
│   ├── manage_directories.yml
│   ├── manage_scheduler.yml
│   ├── apply_patch.yml                 # serial:1 for rolling RAC patches
│   ├── dataguard_switchover.yml
│   ├── dataguard_failover.yml
│   ├── database_facts.yml              # Reporting / validation
│   └── database_refresh.yml            # PDB clone from prod to lower env
│
├── plugins/
│   └── filter/
│       └── catalog_filters.py          # Custom Jinja2 filters: catalog_merge, catalog_for_host
│
├── templates/
│   └── tnsnames.ora.j2                 # TNS entry template (optional, modules handle .ora files)
│
├── docs/
│   ├── catalog_schema.md               # Database catalog YAML schema reference
│   ├── aap_integration.md              # AAP job templates, credentials, workflows
│   └── runbook.md                      # Operational runbook
│
└── tests/
    ├── .yamllint                        # YAML lint config
    └── validate_catalog.py             # JSON Schema validation for catalog files
```

---

## 2. Configuration Architecture

### 2.1 Collection Dependency

**`requirements.yml`**:
```yaml
collections:
  - name: ibre5041.ansible_oracle_modules
    version: ">=3.3.0"
  - name: community.general
    version: ">=7.0.0"
  - name: community.hashi_vault
    version: ">=5.0.0"
```

**`ansible.cfg`**:
```ini
[defaults]
collections_paths = ./collections:~/.ansible/collections
roles_path = ./roles
filter_plugins = ./plugins/filter
hash_behaviour = merge              # NOT recommended globally - use combine() filter instead
stdout_callback = yaml
callbacks_enabled = profile_tasks

[privilege_escalation]
become = true
become_method = sudo
become_user = root
```

> **Note**: Do NOT use `hash_behaviour = merge` globally. The overlay merge is handled explicitly by the `catalog_loader` role using `combine(recursive=True)`. This avoids surprises with Ansible's variable precedence.

### 2.2 Group Variables

#### `group_vars/all/oracle_defaults.yml`

```yaml
---
# Standard paths - mirrors collection's default_vars_only role
oracle_base: /oracle/u01
oracle_install_dir_root: "{{ oracle_base }}"
oracle_install_dir_temp: "{{ oracle_base }}/tmp"
oracle_install_dir_base: "{{ oracle_base }}/base"
oracle_install_dir_prod: "{{ oracle_base }}/product"
oracle_inventory_location: "{{ oracle_base }}/oraInventory"

# OS user/group - matches collection conventions
oracle_os_user: oracle
oracle_os_uid: 800
oracle_os_group: oinstall
oracle_os_groups:
  - { group: oinstall, gid: 800 }
  - { group: dba, gid: 801 }
  - { group: oper, gid: 802 }
  - { group: asmadmin, gid: 803 }
  - { group: asmdba, gid: 804 }
  - { group: asmoper, gid: 805 }

# Oracle Home definitions — global registry for the entire estate.
# Catalog files reference these by name (oracle_home: db_19_23).
# NOT every home is installed on every host. The FPP patching role
# discovers at runtime which homes are present (stat on path) and
# classifies them into _fpp_homes_ready / _fpp_homes_to_prepare.
# Adding a new patched home = add an entry here, no catalog files change.
oracle_homes:
  db_19_21:
    family: 19c
    version: "19.21.0.0"
    path: "{{ oracle_install_dir_prod }}/19.21.0.0"
    edition: EE
  db_19_23:
    family: 19c
    version: "19.23.0.0"
    path: "{{ oracle_install_dir_prod }}/19.23.0.0"
    edition: EE
  db_21_3:
    family: 21c
    version: "21.3.0.0"
    path: "{{ oracle_install_dir_prod }}/21.3.0.0"
    edition: EE
  db_26ai_1:
    family: 26ai
    version: "26.1.0.0"
    path: "{{ oracle_install_dir_prod }}/26.1.0.0"
    edition: EE

# Default database options (minimalist baseline)
oracle_db_options_default:
  JSERVER: false
  ORACLE_TEXT: false
  IMEDIA: false
  CWMLITE: false
  SPATIAL: false
  OMS: false
  APEX: false
  DV: false

# Global defaults
oracle_default_characterset: AL32UTF8
oracle_default_national_characterset: AL16UTF16
oracle_default_storage_type: ASM
oracle_default_diskgroup: XDATA
```

#### `group_vars/all/profiles.yml`

```yaml
---
# Standard Oracle profiles - referenced by name in database catalogs
# Catalog-level profile definitions are merged ON TOP of these
oracle_standard_profiles:
  APP_PROFILE:
    PASSWORD_LIFE_TIME: "180"
    PASSWORD_REUSE_TIME: "365"
    PASSWORD_REUSE_MAX: "12"
    FAILED_LOGIN_ATTEMPTS: "5"
    PASSWORD_LOCK_TIME: "0.0208"    # ~30 minutes
    SESSIONS_PER_USER: UNLIMITED
    IDLE_TIME: "60"

  SVC_PROFILE:
    PASSWORD_LIFE_TIME: UNLIMITED
    PASSWORD_REUSE_TIME: UNLIMITED
    PASSWORD_REUSE_MAX: UNLIMITED
    FAILED_LOGIN_ATTEMPTS: UNLIMITED
    SESSIONS_PER_USER: UNLIMITED
    IDLE_TIME: UNLIMITED

  BATCH_PROFILE:
    PASSWORD_LIFE_TIME: UNLIMITED
    SESSIONS_PER_USER: "20"
    IDLE_TIME: "720"

  MONITORING_PROFILE:
    PASSWORD_LIFE_TIME: UNLIMITED
    FAILED_LOGIN_ATTEMPTS: UNLIMITED
    SESSIONS_PER_USER: "5"
    IDLE_TIME: UNLIMITED
```

#### `group_vars/all/parameters.yml`

```yaml
---
# Baseline init parameters enforced on ALL databases
# These are security/audit parameters that should never be overridden
oracle_baseline_parameters:
  audit_trail: DB
  audit_sys_operations: TRUE
  sec_case_sensitive_logon: TRUE
  remote_login_passwordfile: EXCLUSIVE
  os_authent_prefix: '""'
  recyclebin: "OFF"
  deferred_segment_creation: FALSE
```

#### `group_vars/all/vault_lookups.yml`

```yaml
---
# HashiCorp Vault configuration - NO secrets stored here
# Secrets are injected by AAP credential type or environment variables
vault_addr: "{{ lookup('env', 'VAULT_ADDR') | default('https://vault.corp.example.com:8200', true) }}"
vault_engine: oracle-secrets
vault_auth_method: approle

# Vault path convention mirrors catalog hierarchy:
#   <engine>/data/<db_name>/<pdb_name>/<username>
# Example: oracle-secrets/data/APPDB1/APPPDB1/APP_OWNER
#
# Each secret contains:
#   password: <the password>
#
# SYS/SYSTEM passwords:
#   oracle-secrets/data/<db_name>/sys
#   oracle-secrets/data/<db_name>/system
```

#### `group_vars/dev.yml`

```yaml
---
oracle_environment: dev

oracle_env_db_defaults:
  archivelog: false
  force_logging: false
  flashback: false
  supplemental_logging: false

oracle_env_parameters:
  db_recovery_file_dest_size: 50G

# Scale factor for memory parameters in dev (applied by catalog_loader)
oracle_dev_memory_scale: 0.25
```

#### `group_vars/staging.yml`

```yaml
---
oracle_environment: staging

oracle_env_db_defaults:
  archivelog: true
  force_logging: false
  flashback: false
  supplemental_logging: false

oracle_env_parameters:
  db_recovery_file_dest_size: 100G

oracle_dev_memory_scale: 0.5
```

#### `group_vars/prod.yml`

```yaml
---
oracle_environment: prod

oracle_env_db_defaults:
  archivelog: true
  force_logging: true
  flashback: true
  supplemental_logging: true

oracle_env_parameters:
  db_recovery_file_dest_size: 500G
  db_flashback_retention_target: 4320    # 3 days in minutes
```

#### `group_vars/rac.yml`

```yaml
---
oracle_install_type: rac
oracle_dbconfig_type: RAC
```

#### `group_vars/standalone.yml`

```yaml
---
oracle_install_type: standalone
oracle_dbconfig_type: SI
```

#### `group_vars/dataguard.yml`

```yaml
---
oracle_dg_defaults:
  archivelog: true
  force_logging: true
  supplemental_logging: true
  flashback: true
  standby_file_management: AUTO
```

---

### 2.3 Database Catalog Schema

Each database gets one YAML file in `databases/` declaring its full desired state. All collections (PDBs, users, tablespaces, services, etc.) use **dicts keyed by name**, not lists. This is critical because `combine(recursive=True)` merges dicts recursively but replaces lists entirely.

#### Complete Catalog Schema Reference

```yaml
# ============================================================
# DATABASE CATALOG FILE - databases/<DB_NAME>.yml
# ============================================================

# --- Identity ---
db_name: APPDB1                      # REQUIRED - Database name
db_unique_name: APPDB1               # REQUIRED - DB_UNIQUE_NAME (for DG, can differ from db_name)
sid_prefix: APPDB1                   # REQUIRED - SID or SID prefix (RAC appends instance number)
oracle_home: db_19_21                # REQUIRED - Key into oracle_homes dict in group_vars

# --- Database type ---
cdb: true                            # true = Container Database, false = non-CDB
characterset: AL32UTF8               # Default: oracle_default_characterset
national_characterset: AL16UTF16     # Default: oracle_default_national_characterset
db_type: MULTIPURPOSE                # MULTIPURPOSE | DATA_WAREHOUSING | OLTP

# --- Host mapping (per environment) ---
host_mapping:
  prod:
    hosts: [oraprd-node-1, oraprd-node-2]   # List of hostnames running this DB
    topology: rac                             # rac | standalone
    first_node: oraprd-node-1                 # RAC: node that runs DDL operations
  staging:
    hosts: [orastg-node-1]
    topology: standalone
  dev:
    hosts: [oradev-node-1]
    topology: standalone

# --- Storage ---
storage_type: ASM                    # ASM | FS
datafile_dest: +DATA                 # ASM diskgroup or filesystem path
recoveryfile_dest: +FRA              # ASM diskgroup or filesystem path
default_tablespace_type: bigfile     # bigfile | smallfile

# --- Database-level flags ---
# These can be overridden by oracle_env_db_defaults (group_vars per env)
# and by overlays. Merge priority: catalog base < env defaults < overlay
archivelog: true
force_logging: true
supplemental_logging: true
flashback: true

# --- Database options ---
# Merged on top of oracle_db_options_default from group_vars
db_options:
  JSERVER: true                      # Only specify options that differ from default

# --- Init parameters ---
# Merged: oracle_baseline_parameters + oracle_env_parameters + this dict
# Baseline (security/audit) params cannot be overridden here
init_parameters:
  sga_target: 8G
  sga_max_size: 8G
  pga_aggregate_target: 2G
  pga_aggregate_limit: 4G
  memory_target: 0
  memory_max_target: 0
  open_cursors: 500
  processes: 1500
  db_files: 2000
  db_create_file_dest: +DATA
  db_recovery_file_dest: +FRA
  db_recovery_file_dest_size: 200G

# --- Redo log configuration ---
redo:
  groups_per_thread: 4               # Number of redo groups per thread (RAC: per instance)
  size: 512M                         # Size per redo log member

# --- CDB-level profiles ---
# Keys reference oracle_standard_profiles from group_vars
# Values override specific attributes of the standard profile
profiles:
  APP_PROFILE:                       # Will merge with oracle_standard_profiles.APP_PROFILE
    PASSWORD_LIFE_TIME: "180"
  SVC_PROFILE: {}                    # Use standard definition as-is
  BATCH_PROFILE: {}

# --- CDB-level directories ---
directories:
  DATA_PUMP_DIR:
    path: /oracle/u01/admin/APPDB1/dpdump
  AUDIT_DIR:
    path: /oracle/u01/admin/APPDB1/audit

# --- Data Guard (optional) ---
dataguard:
  role: primary                      # primary | physical_standby
  protection_mode: MAXIMUM_AVAILABILITY
  standby_targets:
    - db_unique_name: APPDB1_DR
      connect_identifier: "oradr-node-1:1521/APPDB1_DR"
      apply_lag_threshold: "30 MINUTES"
  log_archive_dest:
    2:
      service: APPDB1_DR
      async: true
      valid_for: "online_logfiles,primary_role"
      db_unique_name: APPDB1_DR
  standby_file_management: AUTO
  fal_server: APPDB1_DR

# --- PDB definitions ---
pdbs:

  APPPDB1:
    state: open                      # open | closed | read_only | absent
    pdb_admin_username: pdb_admin    # PDB local admin (created during PDB creation)
    datafile_dest: +DATA             # Override CDB-level datafile_dest if needed

    tablespaces:
      APP_DATA:
        size: 50G
        autoextend: true
        nextsize: 1G                 # Optional: autoextend increment
        maxsize: 200G
        bigfile: true
        content: permanent           # permanent | temp | undo
        state: present               # present | absent | online | offline | read_only
      APP_INDEX:
        size: 20G
        autoextend: true
        maxsize: 100G
        bigfile: true
        content: permanent
      APP_LOB:
        size: 10G
        autoextend: true
        maxsize: 500G
        bigfile: true
        content: permanent

    profiles:
      APP_USER_PROFILE:
        PASSWORD_LIFE_TIME: "90"
        FAILED_LOGIN_ATTEMPTS: "3"
        PASSWORD_LOCK_TIME: "0.0208"

    roles:
      APP_READONLY:
        state: present
      APP_READWRITE:
        state: present
      APP_ADMIN:
        state: present

    users:
      APP_OWNER:
        state: present
        default_tablespace: APP_DATA
        default_temp_tablespace: TEMP
        profile: SVC_PROFILE
        authentication_type: password    # password | external | global | none
        locked: false
        expired: false
        grants:
          roles: [CONNECT, RESOURCE, APP_ADMIN]
          system_privileges:
            - CREATE VIEW
            - CREATE MATERIALIZED VIEW
            - CREATE SYNONYM
            - CREATE DATABASE LINK
          object_privileges:
            - "EXECUTE:SYS.DBMS_LOCK"
            - "EXECUTE:SYS.DBMS_CRYPTO"
          grant_mode: exact              # exact = revoke unlisted privileges; append = only add

      APP_READONLY_USER:
        state: present
        default_tablespace: APP_DATA
        profile: APP_USER_PROFILE
        authentication_type: password
        locked: false
        grants:
          roles: [CONNECT, APP_READONLY]
          grant_mode: exact

      APP_BATCH:
        state: present
        default_tablespace: APP_DATA
        profile: BATCH_PROFILE
        authentication_type: password
        locked: false
        grants:
          roles: [CONNECT, APP_READWRITE]

    directories:
      APP_EXPORT_DIR:
        path: /oracle/u01/admin/APPDB1/APPPDB1/export
        state: present
      APP_IMPORT_DIR:
        path: /oracle/u01/admin/APPDB1/APPPDB1/import
        state: present

    services:
      APPPDB1_APP:
        state: started                   # started | stopped | absent
        role: PRIMARY                    # PRIMARY | PHYSICAL_STANDBY | SNAPSHOT_STANDBY
        policy: AUTOMATIC                # AUTOMATIC | MANUAL
        failovertype: AUTO               # NONE | SESSION | SELECT | TRANSACTION | AUTO
        failover_restore: LEVEL1
        failoverretry: 30
        failoverdelay: 10
        preferred_instances: "APPDB11,APPDB12"
        available_instances: ""
        clb_goal: SHORT                  # SHORT | LONG
        rlb_goal: SERVICE_TIME           # SERVICE_TIME | THROUGHPUT
      APPPDB1_BATCH:
        state: started
        role: PRIMARY
        policy: AUTOMATIC
        preferred_instances: "APPDB12"
        available_instances: "APPDB11"
      APPPDB1_RO:
        state: started
        role: PHYSICAL_STANDBY           # Active only on standby
        policy: AUTOMATIC

    scheduler:
      job_classes:
        BATCH_CLASS:
          resource_consumer_group: BATCH_GROUP
          logging_level: FULL
          state: present
      job_schedules:
        NIGHTLY_SCHEDULE:
          repeat_interval: "FREQ=DAILY;BYHOUR=2;BYMINUTE=0"
          state: present
      job_windows:
        NIGHTLY_WINDOW:
          resource_plan: BATCH_PLAN
          schedule_name: NIGHTLY_SCHEDULE
          duration: "+000 04:00:00"
          state: present
      jobs:
        PURGE_OLD_RECORDS:
          job_type: plsql_block
          job_action: "BEGIN APP_OWNER.PKG_MAINTENANCE.PURGE_OLD_RECORDS; END;"
          schedule_name: NIGHTLY_WINDOW
          job_class: BATCH_CLASS
          enabled: true
          state: present

  REPORTPDB:
    state: open
    datafile_dest: +DATA
    tablespaces:
      RPT_DATA:
        size: 30G
        autoextend: true
        maxsize: 100G
        bigfile: true
        content: permanent
    users:
      RPT_OWNER:
        state: present
        default_tablespace: RPT_DATA
        profile: SVC_PROFILE
        grants:
          roles: [CONNECT, RESOURCE]
          system_privileges: [CREATE VIEW, CREATE SYNONYM]
          grant_mode: exact
    services:
      REPORTPDB_SVC:
        state: started
        role: PRIMARY
        preferred_instances: "APPDB11"
        available_instances: "APPDB12"

  TOOLSPDB:
    state: open
    datafile_dest: +DATA
    tablespaces:
      TOOLS_DATA:
        size: 5G
        autoextend: true
        maxsize: 20G
        bigfile: true
        content: permanent
    users:
      TOOLS_ADMIN:
        state: present
        default_tablespace: TOOLS_DATA
        profile: SVC_PROFILE
        grants:
          roles: [CONNECT, RESOURCE, DBA]
          grant_mode: exact
    services:
      TOOLSPDB_SVC:
        state: started
        role: PRIMARY
        preferred_instances: "APPDB11,APPDB12"
```

### 2.4 Overlay Mechanism

#### Example overlay: `databases/overlays/prod/APPDB1.yml`

Only specify values that differ from the base:

```yaml
---
init_parameters:
  sga_target: 16G
  sga_max_size: 16G
  pga_aggregate_target: 4G
  pga_aggregate_limit: 8G
  processes: 3000
  db_recovery_file_dest_size: 500G

redo:
  groups_per_thread: 6
  size: 1G

dataguard:
  standby_targets:
    - db_unique_name: APPDB1_DR
      connect_identifier: "oradr-prod-1:1521/APPDB1_DR"
      apply_lag_threshold: "15 MINUTES"

pdbs:
  APPPDB1:
    tablespaces:
      APP_DATA: { size: 200G, maxsize: 2T }
      APP_INDEX: { size: 100G, maxsize: 500G }
      APP_LOB: { size: 50G, maxsize: 2T }
    services:
      APPPDB1_APP:
        failoverretry: 60
        failoverdelay: 5
  REPORTPDB:
    tablespaces:
      RPT_DATA: { size: 100G, maxsize: 500G }
```

#### How the merge works (in `catalog_loader` role)

```yaml
- name: Load base catalog for {{ _db_file | basename }}
  include_vars:
    file: "{{ playbook_dir }}/databases/{{ _db_file }}"
    name: _base_config

- name: Check for overlay
  stat:
    path: "{{ playbook_dir }}/databases/overlays/{{ oracle_environment }}/{{ _db_file }}"
  register: _overlay_stat

- name: Load overlay if present
  include_vars:
    file: "{{ playbook_dir }}/databases/overlays/{{ oracle_environment }}/{{ _db_file }}"
    name: _overlay_config
  when: _overlay_stat.stat.exists

- name: Merge base + overlay
  set_fact:
    _merged_db: >-
      {{ _base_config
         | combine(_overlay_config | default({}), recursive=True)
      }}
```

**Merge rules**:
- **Scalars**: overlay replaces base
- **Dicts**: recursive merge (overlay keys add to or replace base keys)
- **Lists**: overlay replaces the entire list (Ansible `combine` behavior) - this is why all named collections use dicts, not lists
- **Key absent in overlay**: base value preserved

---

## 3. Role Design

### 3.1 Conventions (all roles)

- Every role receives `oracle_databases` (list of merged catalog dicts for this host) from `catalog_loader`
- Roles loop over databases with `loop: "{{ oracle_databases }}"` and `loop_control: { loop_var: db, label: "{{ db.db_name }}" }`
- Connection to the database uses local sysdba bequeath (`become_user: "{{ oracle_os_user }}"`, `mode: sysdba`, `hostname: localhost`)
- PDB-level operations connect to the PDB service directly or use `session_container`
- All roles support check mode (all collection modules support `supports_check_mode=True`)
- RAC guard: `when: ansible_hostname == db._first_node` for DDL operations
- Data Guard guard: `when: db._db_role != 'physical_standby'` for operations that replicate from primary
- Passwords fetched via vault lookup: `"{{ lookup('community.hashi_vault.hashi_vault', vault_engine ~ '/data/' ~ db.db_name ~ '/' ~ pdb_name ~ '/' ~ user_name, url=vault_addr) }}"`

### 3.2 Role: `catalog_loader`

**Purpose**: Load database catalog files, merge overlays, resolve oracle_home paths, filter to databases mapped to the current host, and set the `oracle_databases` fact.

**Tasks** (`tasks/main.yml`):

1. Find all `databases/*.yml` files (exclude `overlays/` directory)
2. For each file:
   a. Load base config (`include_vars`)
   b. Load matching overlay from `databases/overlays/{{ oracle_environment }}/` if present
   c. Merge with `combine(recursive=True)`
   d. Resolve `oracle_home` name to path from `oracle_homes` dict
   e. Determine `_first_node`, `_topology`, `_db_role` from `host_mapping[oracle_environment]` and `dataguard.role`
3. Filter: keep only databases whose `host_mapping[oracle_environment].hosts` includes `inventory_hostname`
4. If `target_db` extra_var is set, filter further to just that database
5. If `target_pdb` extra_var is set, annotate the matching database
6. Set fact `oracle_databases` with the filtered, enriched list

**Output fact structure** (per database):

```yaml
oracle_databases:
  - db_name: APPDB1
    db_unique_name: APPDB1
    # ... all merged catalog fields ...
    _oracle_home_path: /oracle/u01/product/19.21.0.0   # Resolved from oracle_homes dict
    _first_node: oraprd-node-1                          # From host_mapping
    _topology: rac                                      # From host_mapping
    _db_role: primary                                   # From dataguard.role (default: primary)
    _is_first_node: true                                # ansible_hostname == _first_node
```

### 3.3 Role: `oracle_database`

**Purpose**: Create the database via DBCA if absent, configure database-level properties.

**Module used**: `oracle_db` (from `plugins/modules/oracle_db.py`)

**Tasks**:

1. **Create database** (`oracle_db`, `state: present`):
   - Maps catalog fields to module params: `oracle_home`, `db_name`, `db_unique_name`, `sid`, `sys_password` (vault lookup), `cdb`, `storage_type`, `datafile_dest`, `recoveryfile_dest`, `characterset`, `db_options`, `initparams`, `dbconfig_type` (from `_topology`), `archivelog`, `force_logging`, `supplemental_logging`, `flashback`
   - Guard: `when: db._is_first_node`
   - `become_user: "{{ oracle_os_user }}"`

2. **Register in CRS** (`oracle_crs_db`):
   - Only when `_topology in ['rac', 'restart']`
   - Sets `oraclehome`, `spfile`, `diskgroup`, `startoptions`, `policy`
   - Guard: `when: db._is_first_node`

3. **Configure redo logs** (`oracle_redo`):
   - Sets `groups` and `size` from `db.redo`
   - Guard: `when: db._is_first_node`

4. **Ensure archivelog/flashback** (`oracle_sql`):
   - If `db.archivelog` and not already in archivelog mode
   - `ALTER DATABASE ARCHIVELOG`, `ALTER DATABASE FLASHBACK ON`
   - Guard: `when: db._is_first_node and db._db_role != 'physical_standby'`

### 3.4 Role: `oracle_pdb_management`

**Purpose**: Create, open, close, or drop PDBs per the catalog.

**Module used**: `oracle_pdb` (from `plugins/modules/oracle_pdb.py`)

**Tasks**:

1. Loop over `db.pdbs | dict2items` for each database
2. Map catalog `state: open` to module `state: read_write`; `state: closed` to `state: closed`; `state: read_only` to `state: read_only`; `state: absent` to `state: absent`
3. For creation: pass `pdb_admin_username`, `pdb_admin_password` (vault lookup), `datafile_dest`
4. Guard: `when: db._is_first_node and db._db_role != 'physical_standby'`

### 3.5 Role: `oracle_tablespace_management`

**Purpose**: Create/modify tablespaces per PDB.

**Module used**: `oracle_tablespace` (from `plugins/modules/oracle_tablespace.py`)

**Tasks**:

1. For each database, loop over PDBs
2. For each PDB, loop over `pdb.tablespaces | dict2items`
3. Connect to PDB service (build service_name from PDB name)
4. Call `oracle_tablespace` with: `tablespace` (name), `size`, `bigfile`, `autoextend`, `nextsize`, `maxsize`, `content`, `state`
5. Guard: `when: db._is_first_node and db._db_role != 'physical_standby'`

### 3.6 Role: `oracle_schema_management`

**Purpose**: Manage profiles, roles, users, and grants per PDB. Executed in strict dependency order.

**Modules used**: `oracle_profile`, `oracle_role`, `oracle_user`, `oracle_grant`, `oracle_privs`

**Tasks** (executed in this order to satisfy dependencies):

1. **Profiles** (`oracle_profile`):
   - Merge: `oracle_standard_profiles[profile_name] | combine(catalog_profile_attrs, recursive=True)`
   - Create/update profile with merged attributes
   - Connect to CDB for CDB-level profiles, PDB service for PDB-level profiles
   - Guard: `when: db._is_first_node and db._db_role != 'physical_standby'`

2. **Roles** (`oracle_role`):
   - For each PDB, loop over `pdb.roles | dict2items`
   - Call `oracle_role` with `role` (name), `state`
   - Guard: `when: db._is_first_node and db._db_role != 'physical_standby'`

3. **Users** (`oracle_user`):
   - For each PDB, loop over `pdb.users | dict2items`
   - Password from vault: `lookup('community.hashi_vault.hashi_vault', vault_engine ~ '/data/' ~ db.db_name ~ '/' ~ pdb_name ~ '/' ~ user_name)`
   - Call `oracle_user` with: `schema`, `schema_password`, `default_tablespace`, `default_temp_tablespace`, `profile`, `authentication_type`, `expired`, `locked`, `state`
   - Guard: `when: db._is_first_node and db._db_role != 'physical_standby'`

4. **Grants** (`oracle_grant` / `oracle_privs`):
   - For each user, process `grants.roles`, `grants.system_privileges`, `grants.object_privileges`
   - `oracle_grant` for role grants with `grant_mode: exact` to revoke unlisted roles
   - `oracle_privs` for system and object privileges
   - Object privileges format: `"PRIVILEGE:SCHEMA.OBJECT"` (e.g., `"EXECUTE:SYS.DBMS_LOCK"`)
   - Guard: `when: db._is_first_node and db._db_role != 'physical_standby'`

### 3.7 Role: `oracle_service_management`

**Purpose**: Manage database services. Automatically selects the right module based on topology.

**Modules used**: `oracle_crs_service` (RAC/Restart) or `oracle_services` (standalone)

**Tasks**:

1. For each database, loop over PDBs
2. For each PDB, loop over `pdb.services | dict2items`
3. **RAC/Restart path** (`when: oracle_install_type in ['rac', 'restart']`):
   - Call `oracle_crs_service` with: `name`, `db` (db_unique_name), `pdb`, `state`, `role`, `policy`, `failovertype`, `failover_restore`, `failoverretry`, `failoverdelay`, `preferred` (instances), `available` (instances), `clb_goal`, `rlb_goal`
   - Guard: `when: db._is_first_node`
4. **Standalone path** (`when: oracle_install_type == 'standalone'`):
   - Call `oracle_services` with: `name`, `database_name`, `pdb`, `state`
   - No instance placement parameters

### 3.8 Role: `oracle_parameter_management`

**Purpose**: Manage init parameters with 3-layer merge.

**Module used**: `oracle_parameter` (from `plugins/modules/oracle_parameter.py`)

**Tasks**:

1. Build merged parameter dict:
   ```yaml
   _merged_params: >-
     {{ oracle_baseline_parameters
        | combine(oracle_env_parameters | default({}), recursive=True)
        | combine(db.init_parameters | default({}), recursive=True)
     }}
   ```
2. For each parameter in `_merged_params`:
   - Call `oracle_parameter` with: `name`, `value`, `scope: both` (for dynamic params) or `scope: spfile` (for static params)
   - For RAC: set `sid: '*'` for cluster-wide scope
   - Guard: `when: db._is_first_node`

**Note**: Static vs dynamic parameter detection could use `oracle_facts` to query `v$parameter.issys_modifiable`.

### 3.9 Role: `oracle_directory_management`

**Purpose**: Manage Oracle DIRECTORY objects per PDB.

**Module used**: `oracle_directory` (from `plugins/modules/oracle_directory.py`)

**Tasks**:

1. Handle CDB-level directories from `db.directories`
2. For each PDB, handle PDB-level directories from `pdb.directories`
3. Call `oracle_directory` with: `directory_name`, `directory_path`, `state`
4. Guard: `when: db._is_first_node and db._db_role != 'physical_standby'`

### 3.10 Role: `oracle_scheduler_management`

**Purpose**: Manage DBMS_SCHEDULER objects per PDB. Executed in dependency order.

**Modules used**: `oracle_jobclass`, `oracle_jobschedule`, `oracle_jobwindow`, `oracle_job`

**Tasks** (in this order):

1. **Job classes** (`oracle_jobclass`): resource_consumer_group, logging_level
2. **Job schedules** (`oracle_jobschedule`): repeat_interval
3. **Job windows** (`oracle_jobwindow`): resource_plan, schedule_name, duration
4. **Jobs** (`oracle_job`): job_type, job_action, schedule_name/repeat_interval, job_class, enabled

Guard: `when: db._is_first_node and db._db_role != 'physical_standby'`

### 3.11 Role: `oracle_patching`

**Purpose**: Apply Oracle patches (OPatch) and run datapatch. Supports rolling patches for RAC.

**Modules used**: `oracle_opatch`, `oracle_datapatch`

**Extra vars** (not from catalog):
- `oracle_home_name`: key into `oracle_homes` dict
- `patch_id`: OPatch patch number
- `patch_base`: path to unzipped patch directory
- `opatchauto`: true/false (GI patches use opatchauto)
- `rolling`: true/false

**Tasks**:

1. **Resolve oracle_home** from `oracle_homes[oracle_home_name]`
2. **Preflight**: `oracle_opatch` with `conflict_check: true`
3. **Stop processes** if needed (non-rolling or `stop_processes: true`)
4. **Apply patch**: `oracle_opatch` with `state: present`, `patch_id`, `patch_base`, `oracle_home`, `opatchauto`
5. **Start processes** if they were stopped
6. **Run datapatch**: For each database running in this oracle_home (found via `catalog_loader`):
   - `oracle_datapatch` with `oracle_home`, `db_name`
   - Guard: `when: db._is_first_node`
7. **Verify**: `oracle_facts` with `patch_level: true`

**Playbook-level**: `serial: 1` ensures rolling execution across RAC nodes.

### 3.12 Role: `oracle_fpp_patching`

**Purpose**: Converge each database to its declared `oracle_home` using FPP (Fleet Patching and Provisioning). Discovers which homes are installed on the target host at runtime; provisions missing ones via `rhpctl` before the maintenance window.

**Two-phase model** (controlled by `fpp_mode: prepare | patch`):

| Phase | When | What it does |
|---|---|---|
| `prepare` | Days before the window | Provisions missing working copies from gold images, runs pre-checks |
| `patch` | During the window | Moves databases to working copies, runs datapatch, verifies |

**Desired-state logic** (`build_plan.yml`):

1. Query `oratab` for each database's current Oracle Home path
2. Compare with `_oracle_home_path` from catalog → builds `_fpp_patch_plan` (actual ≠ desired)
3. `stat` each desired home path → classifies target homes:
   - `_fpp_homes_ready` — path exists, working copy already provisioned
   - `_fpp_homes_to_prepare` — path absent, FPP prepare required
4. Validate prerequisite environments (dev must be patched before staging, etc.)

**Key constraint**: version is a **per-database** property, not per-environment. The progression constraint (dev → staging → prod) applies per database independently — prod APPDB1 can be at 19.29 while prod HRDB stays at 19.28.

**Prepare phase** (`prepare.yml`):
- Iterates over `_fpp_homes_to_prepare` only
- Logs `_fpp_homes_ready` as already installed (skipped)
- For each home to prepare: validates gold image → provisions working copy → verifies binaries
- Outputs `_fpp_working_copies` dict for use by patch phase

**Patch phase** (`patch.yml`):
- Fails immediately if `_fpp_homes_to_prepare` is non-empty → clear error message to run prepare first
- Moves each database via `rhpctl move database`
- Runs datapatch if `fpp_run_datapatch: true`
- Verifies databases are open on the new home

**Role defaults** (`defaults/main.yml`):

```yaml
fpp_mode: ""                    # prepare | patch
fpp_server_home: ""             # Where rhpctl lives
fpp_gold_image_suffix: "_gold"  # db_19_23 → gold image db_19_23_gold
fpp_working_copy_name: ""       # Single-target shorthand
# fpp_working_copies:           # Per-home dict (multi-target)
#   db_19_23: wc_19_23_node1
fpp_run_datapatch: true
fpp_env_progression: [dev, staging, prod]
fpp_provision_timeout: 3600
fpp_move_timeout: 7200
```

**Typical patching cycle** (example: 19c quarterly RU → 19.30):

```bash
# 1. Add db_19_30 to oracle_homes in oracle_defaults.yml
# 2. Set oracle_home: db_19_30 in databases/overlays/dev/APPDB1.yml
# 3. Prepare dev (before window)
ansible-playbook playbooks/fpp_patch.yml -l oradev-node-1 -e fpp_mode=prepare -e oracle_environment=dev
# 4. Patch dev (during window)
ansible-playbook playbooks/fpp_patch.yml -l oradev-node-1 -e fpp_mode=patch -e oracle_environment=dev \
  -e '{"fpp_working_copies": {"db_19_30": "wc_19_30_oradev-node-1"}}'
# 5. Repeat for staging, then prod
```

### 3.13 Role: `oracle_dataguard`

**Purpose**: Configure Data Guard, handle switchover and failover.

**Modules used**: `oracle_sql`, `oracle_sqldba`, `oracle_parameter`

**Note**: No dedicated DG module exists in the collection. This role uses `oracle_sqldba` with `sqltype: dgmgrl` for DGMGRL commands and `oracle_parameter` for DG-related parameters.

**Tasks** (setup):

1. **Configure DG parameters** via `oracle_parameter`:
   - `log_archive_dest_2` (from `db.dataguard.log_archive_dest`)
   - `fal_server`
   - `standby_file_management`
   - `log_archive_config` with `dg_config` listing all db_unique_names

2. **Create standby redo logs** via `oracle_redo`:
   - `log_type: standby`
   - Size matching online redo log size
   - One more group than online redo log groups per thread

3. **DGMGRL configuration** via `oracle_sqldba`:
   - `CREATE CONFIGURATION ...`
   - `ADD DATABASE ...`
   - `ENABLE CONFIGURATION`

**Tasks** (switchover - triggered by `dg_operation: switchover` extra_var):

1. **Preflight**: Verify current role is PRIMARY via `oracle_facts`
2. **Verify standby is synchronized**: `oracle_sql` query on `v$dataguard_stats`
3. **Execute switchover**: `oracle_sqldba` with `sqltype: dgmgrl`, command: `SWITCHOVER TO <target>`
4. **Verify new roles**: `oracle_facts` on both old primary and new primary

**Tasks** (failover - triggered by `dg_operation: failover` extra_var):

1. **Execute failover**: `oracle_sqldba` with `sqltype: dgmgrl`, command: `FAILOVER TO <target>`
2. **Reinstate old primary** (if possible): `REINSTATE DATABASE <old_primary>`

---

## 4. Playbook Design

### 4.1 Converge Playbook (`playbooks/converge.yml`)

```yaml
---
- name: Oracle Database Converge
  hosts: all
  collections:
    - ibre5041.ansible_oracle_modules
  become: true
  any_errors_fatal: true

  pre_tasks:
    - name: Validate required variables
      assert:
        that:
          - oracle_environment is defined
          - oracle_environment in ['dev', 'staging', 'prod']
        fail_msg: "oracle_environment must be set to dev, staging, or prod"
      tags: [always]

  roles:
    - role: catalog_loader
      tags: [always]

    - role: oracle_database
      tags: [database]

    - role: oracle_pdb_management
      tags: [database, pdbs]

    - role: oracle_parameter_management
      tags: [parameters]

    - role: oracle_tablespace_management
      tags: [tablespaces]

    - role: oracle_schema_management
      tags: [schemas]

    - role: oracle_directory_management
      tags: [directories]

    - role: oracle_service_management
      tags: [services]

    - role: oracle_scheduler_management
      tags: [scheduler]

  post_tasks:
    - name: Gather final database facts
      oracle_facts:
        mode: sysdba
        hostname: localhost
        service_name: "{{ item.db_name }}"
        oracle_home: "{{ item._oracle_home_path }}"
        database: true
        instance: true
        patch_level: true
      loop: "{{ oracle_databases }}"
      loop_control:
        label: "{{ item.db_name }}"
      register: _converge_facts
      become_user: "{{ oracle_os_user }}"
      tags: [always, facts]
```

**Usage patterns**:
```bash
# Full converge on all prod hosts
ansible-playbook playbooks/converge.yml -e oracle_environment=prod

# Schemas only for a specific database
ansible-playbook playbooks/converge.yml -e oracle_environment=prod -e target_db=APPDB1 --tags schemas

# Single host
ansible-playbook playbooks/converge.yml --limit oraprd-node-1 -e oracle_environment=prod

# Check mode (dry run)
ansible-playbook playbooks/converge.yml -e oracle_environment=prod --check --diff
```

### 4.2 Discrete Playbook: `playbooks/manage_schemas.yml`

```yaml
---
- name: Manage Oracle Schemas
  hosts: all
  collections:
    - ibre5041.ansible_oracle_modules
  become: true
  any_errors_fatal: true

  pre_tasks:
    - name: Validate inputs
      assert:
        that:
          - oracle_environment is defined
          - oracle_environment in ['dev', 'staging', 'prod']

  roles:
    - role: catalog_loader
    - role: oracle_tablespace_management    # Tablespaces must exist before users reference them
    - role: oracle_schema_management
```

**Extra vars**: `oracle_environment` (required), `target_db` (optional), `target_pdb` (optional)

### 4.3 Patching Playbook: `playbooks/apply_patch.yml`

```yaml
---
- name: Apply Oracle Patch
  hosts: all
  collections:
    - ibre5041.ansible_oracle_modules
  become: true
  serial: 1                          # Rolling: one node at a time for RAC
  any_errors_fatal: true

  pre_tasks:
    - name: Validate inputs
      assert:
        that:
          - oracle_home_name is defined
          - patch_id is defined
          - patch_base is defined
          - oracle_environment is defined
        fail_msg: >
          Required extra vars: oracle_home_name, patch_id, patch_base, oracle_environment

  roles:
    - role: catalog_loader             # Needed to find databases in the target oracle_home
    - role: oracle_patching
```

### 4.4 Data Guard Switchover: `playbooks/dataguard_switchover.yml`

```yaml
---
- name: Data Guard Switchover
  hosts: all
  collections:
    - ibre5041.ansible_oracle_modules
  become: true
  any_errors_fatal: true

  pre_tasks:
    - name: Validate inputs
      assert:
        that:
          - target_db is defined
          - switchover_target is defined
          - oracle_environment is defined

    - name: Verify this host is the current primary
      oracle_facts:
        mode: sysdba
        hostname: localhost
        service_name: "{{ target_db }}"
        database: true
      register: _dg_preflight
      become_user: "{{ oracle_os_user }}"

    - name: Fail if not primary
      assert:
        that:
          - _dg_preflight.ansible_facts.oracle_facts.database.database_role == 'PRIMARY'
        fail_msg: "This host is not the primary for {{ target_db }}"

  roles:
    - role: catalog_loader
    - role: oracle_dataguard
      vars:
        dg_operation: switchover
```

### 4.5 Database Facts / Reporting: `playbooks/database_facts.yml`

```yaml
---
- name: Gather Oracle Database Facts
  hosts: all
  collections:
    - ibre5041.ansible_oracle_modules
  become: true

  roles:
    - role: catalog_loader

  tasks:
    - name: Gather comprehensive facts
      oracle_facts:
        mode: sysdba
        hostname: localhost
        service_name: "{{ item.db_name }}"
        oracle_home: "{{ item._oracle_home_path }}"
        database: true
        instance: true
        parameter: true
        tablespaces: true
        redo: true
        patch_level: true
        userenv: true
      loop: "{{ oracle_databases }}"
      loop_control:
        label: "{{ item.db_name }}"
      register: database_facts
      become_user: "{{ oracle_os_user }}"

    - name: Display facts summary
      debug:
        msg: |
          Database: {{ item.item.db_name }}
          Version: {{ item.ansible_facts.oracle_facts.instance.version | default('N/A') }}
          Status: {{ item.ansible_facts.oracle_facts.instance.status | default('N/A') }}
          Role: {{ item.ansible_facts.oracle_facts.database.database_role | default('N/A') }}
      loop: "{{ database_facts.results }}"
      loop_control:
        label: "{{ item.item.db_name }}"
```

### 4.6 Infrastructure Install: `playbooks/install_infrastructure.yml`

This wraps the existing collection roles for OS prep, Grid Infrastructure, and DB home installation.

```yaml
---
- name: Install Oracle Infrastructure
  hosts: all
  collections:
    - ibre5041.ansible_oracle_modules
  become: true
  become_user: root
  any_errors_fatal: true

  roles:
    # Shared defaults from the collection
    - role: ibre5041.ansible_oracle_modules.default_vars_only
      tags: [always]

    # OS preparation (select based on platform)
    - role: ibre5041.ansible_oracle_modules.base_oracle_vmware
      tags: [base]
      when: is_vmware_environment | default(false)

    - role: ibre5041.ansible_oracle_modules.base_oracle_ec2
      tags: [base]
      when: is_ec2_environment | default(false)

    # Grid Infrastructure (RAC or Restart)
    - role: ibre5041.ansible_oracle_modules.oracle_crs_19c
      tags: [grid]
      when: oracle_install_type == 'rac'

    - role: ibre5041.ansible_oracle_modules.oracle_restart_19c
      tags: [grid]
      when: oracle_install_type == 'restart'

    # Database Home
    - role: ibre5041.ansible_oracle_modules.oracle_db_home
      tags: [dbhome]

    # Post-install
    - role: ibre5041.ansible_oracle_modules.oracle_post_install
      tags: [post]

    - role: ibre5041.ansible_oracle_modules.oracle_systemd
      tags: [post, systemd]
```

---

## 5. Custom Filter Plugin

### `plugins/filter/catalog_filters.py`

```python
"""Custom Jinja2 filters for database catalog operations."""


class FilterModule:
    """Ansible filter plugin for catalog operations."""

    def filters(self):
        return {
            'catalog_merge': self.catalog_merge,
            'catalog_for_host': self.catalog_for_host,
            'resolve_oracle_home': self.resolve_oracle_home,
        }

    @staticmethod
    def catalog_merge(base, overlay):
        """Deep merge overlay onto base dict (recursive)."""
        # Equivalent to combine(recursive=True) but available as a filter
        # for complex pipeline expressions
        import copy
        result = copy.deepcopy(base)
        for key, value in overlay.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = FilterModule.catalog_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    @staticmethod
    def catalog_for_host(databases, hostname, environment):
        """Filter database list to those mapped to a specific host+environment."""
        result = []
        for db in databases:
            mapping = db.get('host_mapping', {}).get(environment, {})
            if hostname in mapping.get('hosts', []):
                result.append(db)
        return result

    @staticmethod
    def resolve_oracle_home(home_name, oracle_homes):
        """Resolve oracle_home name to path from oracle_homes dict."""
        if home_name not in oracle_homes:
            raise ValueError(f"Oracle home '{home_name}' not found in oracle_homes dict. "
                           f"Available: {list(oracle_homes.keys())}")
        return oracle_homes[home_name]['path']
```

---

## 6. AAP Integration

### 6.1 Job Template Mapping

| Job Template Name | Playbook | Required Survey Vars | Optional Survey Vars |
|---|---|---|---|
| Oracle - Full Converge | `converge.yml` | `oracle_environment` | `target_db` |
| Oracle - Create Database | `create_database.yml` | `oracle_environment`, `target_db` | |
| Oracle - Manage PDBs | `manage_pdbs.yml` | `oracle_environment` | `target_db` |
| Oracle - Manage Schemas | `manage_schemas.yml` | `oracle_environment` | `target_db`, `target_pdb` |
| Oracle - Manage Tablespaces | `manage_tablespaces.yml` | `oracle_environment` | `target_db`, `target_pdb` |
| Oracle - Manage Parameters | `manage_parameters.yml` | `oracle_environment` | `target_db` |
| Oracle - Manage Services | `manage_services.yml` | `oracle_environment` | `target_db` |
| Oracle - Manage Directories | `manage_directories.yml` | `oracle_environment` | `target_db` |
| Oracle - Manage Scheduler | `manage_scheduler.yml` | `oracle_environment` | `target_db` |
| Oracle - Apply Patch | `apply_patch.yml` | `oracle_environment`, `oracle_home_name`, `patch_id`, `patch_base` | `rolling`, `opatchauto` |
| Oracle - DG Switchover | `dataguard_switchover.yml` | `oracle_environment`, `target_db`, `switchover_target` | |
| Oracle - DG Failover | `dataguard_failover.yml` | `oracle_environment`, `target_db`, `failover_target` | |
| Oracle - Database Facts | `database_facts.yml` | `oracle_environment` | `target_db` |
| Oracle - Install Infra | `install_infrastructure.yml` | `oracle_environment` | |

### 6.2 Custom Credential Type: Oracle Vault Lookup

```yaml
# --- Input configuration ---
fields:
  - id: vault_addr
    type: string
    label: Vault Address
  - id: vault_role_id
    type: string
    label: Vault AppRole Role ID
  - id: vault_secret_id
    type: string
    label: Vault AppRole Secret ID
    secret: true
  - id: vault_engine
    type: string
    label: Vault Secret Engine Path
    default: oracle-secrets

# --- Injector configuration ---
env:
  VAULT_ADDR: "{{ vault_addr }}"
  VAULT_ROLE_ID: "{{ vault_role_id }}"
  VAULT_SECRET_ID: "{{ vault_secret_id }}"
extra_vars:
  vault_addr: "{{ vault_addr }}"
  vault_engine: "{{ vault_engine }}"
  vault_auth_method: approle
```

### 6.3 Inventory Requirements from AAP

AAP inventory must provide:

- **Environment groups**: `dev`, `staging`, `prod` - each host belongs to exactly one
- **Topology groups**: `rac`, `standalone`, `dataguard` - each host belongs to exactly one
- **Host variables**:
  - `oracle_install_type` (if not derived from group membership)
  - `first_rac_node` (for RAC clusters - the node that runs DDL operations)
  - `oracle_release` (optional, for filtering)

These can come from AAP constructed inventories sourcing from a CMDB, VMware vCenter, or AWS dynamic inventory.

### 6.4 Workflow Templates

**Full Provisioning Workflow**:
```
Install Infra → Create Database → Manage PDBs → Manage Tablespaces
    → Manage Schemas → Manage Services → Manage Directories
    → Manage Scheduler → Database Facts
```

**Quarterly Patching Workflow**:
```
Database Facts (pre-patch) → Apply Patch → Database Facts (post-patch)
```

**Environment Refresh Workflow**:
```
Database Facts (source) → Database Refresh → Manage Parameters (target)
    → Manage Schemas (target) → Database Facts (target)
```

---

## 7. Topology Handling

### 7.1 RAC

- Database creation runs only on `_first_node` (matches existing pattern in `install_oracle_crs_19c.yml:71`)
- PDB, schema, tablespace, directory, scheduler operations run only on `_first_node` (shared data dictionary)
- Services use `oracle_crs_service` with `preferred_instances` and `available_instances`
- Parameters use `sid: '*'` for cluster-wide scope
- Patching playbook uses `serial: 1` for rolling updates across nodes

### 7.2 Standalone

- All operations run on the single host (no `_first_node` guard needed, but harmless)
- Services use `oracle_services` (DBMS_SERVICE backend)
- No instance placement for services
- No rolling patch needed

### 7.3 Data Guard

- **Primary**: full convergence (all roles execute)
- **Physical standby**: skip these roles (they replicate from primary):
  - `oracle_schema_management`
  - `oracle_tablespace_management`
  - `oracle_pdb_management`
  - `oracle_directory_management`
  - `oracle_scheduler_management`
- **Standby-only operations**:
  - `oracle_service_management` - for standby-role services (e.g., Active Data Guard read-only services)
  - `oracle_parameter_management` - for standby-specific params (`fal_server`, `log_archive_dest_*`)
  - `oracle_patching` - patches must be applied to standby independently

The `catalog_loader` role sets `_db_role` from `db.dataguard.role`, and subsequent roles check `when: db._db_role != 'physical_standby'`.

---

## 8. Secrets Management

### 8.1 Vault Path Convention

Vault paths mirror the catalog hierarchy for intuitive navigation:

```
<engine>/data/<db_name>/sys                          → SYS password
<engine>/data/<db_name>/system                       → SYSTEM password
<engine>/data/<db_name>/<pdb_name>/pdb_admin          → PDB admin password
<engine>/data/<db_name>/<pdb_name>/<username>          → Schema password
```

Examples:
```
oracle-secrets/data/APPDB1/sys
oracle-secrets/data/APPDB1/APPPDB1/APP_OWNER
oracle-secrets/data/APPDB1/APPPDB1/APP_READONLY_USER
oracle-secrets/data/APPDB1/REPORTPDB/RPT_OWNER
```

### 8.2 Lookup Pattern in Roles

```yaml
- name: Create user {{ _user_name }}
  oracle_user:
    service_name: "{{ _pdb_name }}"
    mode: sysdba
    schema: "{{ _user_name }}"
    schema_password: >-
      {{ lookup('community.hashi_vault.hashi_vault',
                vault_engine ~ '/data/' ~ db.db_name ~ '/' ~ _pdb_name ~ '/' ~ _user_name,
                url=vault_addr,
                auth_method=vault_auth_method)['password'] }}
    # ... other params
```

### 8.3 Security Rules

- No passwords stored in catalog files, group_vars, or the repository at all
- Vault credentials injected by AAP credential type (env vars)
- `no_log: true` on all tasks that handle passwords
- All password parameters in collection modules already have `no_log=True` in their argument specs

---

## 9. Catalog Validation

### `tests/validate_catalog.py`

A validation script that checks catalog files against the schema:

- Required fields present (`db_name`, `db_unique_name`, `sid_prefix`, `oracle_home`)
- `oracle_home` references a valid key in `oracle_homes`
- `host_mapping` structure is valid
- PDB names are valid Oracle identifiers
- User names are valid Oracle identifiers
- Tablespace `content` is one of: `permanent`, `temp`, `undo`
- Service `state` is one of: `started`, `stopped`, `absent`
- Service `role` is one of: `PRIMARY`, `PHYSICAL_STANDBY`, `SNAPSHOT_STANDBY`
- Grant `grant_mode` is one of: `exact`, `append`
- No duplicate object names within a PDB
- Profile attributes are valid Oracle profile limit names
- Overlay files reference databases that exist in the base catalog

Run as a pre-commit hook or CI check:
```bash
python tests/validate_catalog.py databases/ group_vars/all/oracle_defaults.yml
```

---

## 10. Key Design Decisions Summary

| Decision | Rationale |
|---|---|
| **Separate repo** consuming collection via `requirements.yml` | Decouples configuration from module code. Collection upgrades are independent |
| **Catalog directory (1 file/DB)** not one big dict | Scales for large estates, reduces git merge conflicts, teams can own their DB files |
| **Dict-keyed collections** (users, services, etc.) not lists | `combine(recursive=True)` merges dicts recursively but replaces lists entirely |
| **`oracle_homes` dict** in group_vars, catalog references by name | Patching adds a new home version without touching any catalog file |
| **Runtime home discovery** via `stat` in FPP patching | Version is a per-database property — not all homes exist on all hosts. Stat is authoritative: no per-group or per-host declarations to maintain |
| **`_fpp_homes_ready` / `_fpp_homes_to_prepare`** split | Prepare phase skips already-provisioned homes; patch phase fails fast if any home is missing — prevents patching before prepare runs |
| **3-layer parameter merge**: baseline + env + catalog | Security/audit params enforced globally, sizing per environment, app-specific in catalog |
| **Schema management in strict order**: profiles -> roles -> users -> grants | Eliminates dependency failures |
| **RAC guard**: `when: db._is_first_node` | Schema/PDB/tablespace ops run once (shared data dictionary) |
| **Data Guard guard**: skip schema/tablespace/PDB on standby | These replicate from primary |
| **Services**: `oracle_crs_service` for RAC/Restart, `oracle_services` for standalone | Different backends, role chooses automatically |
| **Secrets via vault lookup** with path mirroring catalog hierarchy | Intuitive path structure, no secrets in repo |
| **`serial: 1`** for patching playbook | Rolling patches across RAC nodes |
| **Local sysdba bequeath** connection model | Most reliable for privileged operations, matches collection patterns |

---

## 11. Implementation Order

| Phase | Step | Deliverable | Dependencies |
|---|---|---|---|
| **1 - Foundation** | 1.1 | Repo scaffolding: `ansible.cfg`, `requirements.yml`, directory structure | None |
| | 1.2 | `group_vars/all/` - oracle_defaults, profiles, parameters, vault_lookups | None |
| | 1.3 | `group_vars/` per-env and per-topology files | None |
| | 1.4 | `plugins/filter/catalog_filters.py` | None |
| **2 - Catalog** | 2.1 | `catalog_loader` role | 1.2, 1.4 |
| | 2.2 | Sample catalog file `databases/APPDB1.yml` + overlay | 2.1 |
| | 2.3 | `tests/validate_catalog.py` | 2.2 |
| **3 - Core Roles** | 3.1 | `oracle_database` role (DBCA, CRS registration, redo) | 2.1 |
| | 3.2 | `oracle_pdb_management` role | 2.1 |
| | 3.3 | `oracle_parameter_management` role | 2.1 |
| | 3.4 | `oracle_tablespace_management` role | 2.1 |
| | 3.5 | `oracle_schema_management` role (profiles, roles, users, grants) | 2.1, 3.4 |
| **4 - Support Roles** | 4.1 | `oracle_service_management` role | 2.1 |
| | 4.2 | `oracle_directory_management` role | 2.1 |
| | 4.3 | `oracle_scheduler_management` role | 2.1 |
| **5 - Operations** | 5.1 | `oracle_patching` role | 2.1 |
| | 5.2 | `oracle_dataguard` role | 2.1, 3.3 |
| **6 - Playbooks** | 6.1 | `converge.yml` | 3.x, 4.x |
| | 6.2 | Discrete playbooks (manage_schemas, manage_tablespaces, etc.) | 3.x, 4.x |
| | 6.3 | `apply_patch.yml` | 5.1 |
| | 6.4 | `dataguard_switchover.yml`, `dataguard_failover.yml` | 5.2 |
| | 6.5 | `install_infrastructure.yml`, `database_facts.yml` | 2.1 |
| **7 - Integration** | 7.1 | AAP credential type documentation | 6.x |
| | 7.2 | AAP job template definitions | 6.x |
| | 7.3 | AAP workflow template definitions | 7.2 |
| **8 - Docs** | 8.1 | `docs/catalog_schema.md` - catalog YAML reference | 2.2 |
| | 8.2 | `docs/aap_integration.md` - AAP setup guide | 7.x |
| | 8.3 | `docs/runbook.md` - operational procedures | All |
