# AAP Integration Guide

## Architecture

```
+---------------------------------------------+     +---------------------------+
| oracle_automation                            |     | Execution Environment     |
| (AAP Project + Inventory Source)             |     | (Container Image)         |
|                                              |     |                           |
| - inventory/hosts.yml (or dynamic plugin)    |     | - ansible-core            |
| - inventory/group_vars/                      |     | - ibre5041.ansible_oracle |
| - databases/                                 |     |   _modules collection     |
| - playbooks/                                 |     | - community.hashi_vault   |
| - roles/                                     |     | - community.general       |
| - plugins/filter/                            |     | - python oracledb         |
+---------------------------------------------+     +---------------------------+
        |                                                        |
        v                                                        v
   AAP Project + Inventory Source                    AAP Execution Environment
        |                                                        |
        +----------------------------+---------------------------+
                                     |
                                     v
                              AAP Job Template
                    (Inventory + Project + EE + Credentials)
```

## Execution Environment (EE)

The EE must include:

### Collections (in `requirements.yml` or built into the image)
```yaml
collections:
  - name: ibre5041.ansible_oracle_modules
    version: ">=3.3.0"
  - name: community.general
    version: ">=7.0.0"
  - name: community.hashi_vault
    version: ">=5.0.0"
```

### Python packages (in `requirements.txt`)
```
oracledb>=1.0.0
hvac>=1.0.0         # HashiCorp Vault client
```

### System packages (if using thick mode)
- Oracle Instant Client (required for `/ as sysdba` connections)

## AAP Configuration

### 1. Create Project

1. Create a new Project in AAP
2. Source: Git
3. URL: `https://github.com/cmodiano/oracle_automation.git`
4. Branch: `main`
5. Update on launch: Yes

### 2. Create Inventory

1. Create a new Inventory in AAP
2. Add an Inventory Source pointing to the **same** Project
3. Source path: `inventory/`
4. AAP will automatically discover `hosts.yml` and load `group_vars/`
5. Verify groups exist: `dev`, `staging`, `prod`, `rac`, `standalone`, `dataguard`

**Alternative: Dynamic inventory** — Replace `inventory/hosts.yml` with a dynamic
inventory plugin (e.g., `inventory/oracle_hosts.py`). The `group_vars/` directory
will still be loaded automatically.

### 3. Create Credential Type: Oracle Vault Lookup

**Input configuration:**
```yaml
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
```

**Injector configuration:**
```yaml
env:
  VAULT_ADDR: "{{ vault_addr }}"
  VAULT_ROLE_ID: "{{ vault_role_id }}"
  VAULT_SECRET_ID: "{{ vault_secret_id }}"
extra_vars:
  vault_addr: "{{ vault_addr }}"
  vault_engine: "{{ vault_engine }}"
  vault_auth_method: approle
```

### 4. Create Job Templates

| Job Template Name | Playbook | Required Survey Vars | Optional Survey Vars |
|---|---|---|---|
| Oracle - Full Converge | `playbooks/converge.yml` | _(none)_ | `target_db` |
| Oracle - Create Database | `playbooks/create_database.yml` | `target_db` | |
| Oracle - Manage PDBs | `playbooks/manage_pdbs.yml` | _(none)_ | `target_db` |
| Oracle - Manage Schemas | `playbooks/manage_schemas.yml` | _(none)_ | `target_db`, `target_pdb` |
| Oracle - Manage Tablespaces | `playbooks/manage_tablespaces.yml` | _(none)_ | `target_db`, `target_pdb` |
| Oracle - Manage Parameters | `playbooks/manage_parameters.yml` | _(none)_ | `target_db` |
| Oracle - Manage Services | `playbooks/manage_services.yml` | _(none)_ | `target_db` |
| Oracle - Manage Directories | `playbooks/manage_directories.yml` | _(none)_ | `target_db` |
| Oracle - Manage Scheduler | `playbooks/manage_scheduler.yml` | _(none)_ | `target_db` |
| Oracle - FPP Prepare | `playbooks/fpp_patch.yml` | `fpp_mode=prepare` | `target_db`, `fpp_working_copy_name` |
| Oracle - FPP Patch | `playbooks/fpp_patch.yml` | `fpp_mode=patch`, `fpp_working_copies` or `fpp_working_copy_name` | `target_db` |
| Oracle - Register Home | `playbooks/register_oracle_home.yml` | `home_name`, `home_version`, `home_path` | `home_edition` |
| Oracle - Apply Patch (OPatch) | `playbooks/apply_patch.yml` | `oracle_home_name`, `patch_id`, `patch_base` | `rolling`, `opatchauto` |
| Oracle - DG Switchover | `playbooks/dataguard_switchover.yml` | `target_db`, `switchover_target` | |
| Oracle - DG Failover | `playbooks/dataguard_failover.yml` | `target_db`, `failover_target` | |
| Oracle - Database Facts | `playbooks/database_facts.yml` | _(none)_ | `target_db` |
| Oracle - Database Refresh | `playbooks/database_refresh.yml` | `target_db` | |
| Oracle - Install Infra | `playbooks/install_infrastructure.yml` | _(none)_ | |

**Note**: `oracle_environment` is set automatically by inventory group_vars based on host group membership (dev/staging/prod). It should NOT be passed as an extra_var — the AAP job template uses `--limit` to target the right hosts.

All templates should use:
- **Inventory**: Your Oracle inventory (sourced from this project's `inventory/` directory)
- **Project**: oracle_automation
- **Execution Environment**: Your Oracle EE
- **Credentials**: Machine credential + Oracle Vault Lookup credential

### 5. Create Workflow Templates

**Full Provisioning:**
```
Install Infra -> Create Database -> Manage PDBs -> Manage Tablespaces
    -> Manage Schemas -> Manage Services -> Manage Directories
    -> Manage Scheduler -> Database Facts
```

**FPP Patching (recommended):**
```
FPP Patch (provisions new home, moves DBs, updates catalog, auto-commits)
```

**OPatch Patching (legacy):**
```
Database Facts (pre-patch) -> Apply Patch (OPatch) -> Database Facts (post-patch)
```

**Environment Refresh:**
```
Database Facts (source) -> Database Refresh -> Manage Parameters (target)
    -> Manage Schemas (target) -> Database Facts (target)
```

## Variable Flow

```
inventory/group_vars/               databases/
(loaded automatically by Ansible)   (loaded by catalog_loader role)
         |                                    |
         v                                    v
  oracle_homes                        databases/APPDB1.yml
  oracle_baseline_parameters          databases/overlays/prod/APPDB1.yml
  oracle_env_parameters                       |
  oracle_standard_profiles                    v
  oracle_environment               catalog_loader role merges:
         |                         base + overlay + enrichment
         |                                    |
         +------------------------------------+
                        |
                        v
               oracle_databases fact
          (list of merged, enriched dicts)
                        |
                        v
              Downstream roles use both:
              - Inventory vars (parameters, profiles)
              - Catalog data (PDBs, users, tablespaces)
```
