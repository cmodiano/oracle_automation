# AAP Integration Guide

## Architecture

```
+------------------+     +---------------------+     +---------------------------+
| Inventory Repo   |     | oracle_automation   |     | Execution Environment     |
| (AAP Inventory   |     | (AAP Project)       |     | (Container Image)         |
|  Source)          |     |                     |     |                           |
| - hosts/groups   |     | - playbooks/        |     | - ansible-core            |
| - group_vars/    |     | - roles/            |     | - ibre5041.ansible_oracle |
| - host_vars/     |     | - databases/        |     |   _modules collection     |
| - inventory.py   |     | - plugins/filter/   |     | - community.hashi_vault   |
+------------------+     +---------------------+     | - community.general       |
        |                         |                   | - python oracledb         |
        v                         v                   +---------------------------+
   AAP Inventory            AAP Project                          |
        |                         |                              v
        +------------+------------+                    AAP Execution Environment
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

### 1. Create Inventory

1. Create a new Inventory in AAP
2. Add an Inventory Source pointing to your inventory repo
3. The Python inventory builder provides hosts, groups, and variables
4. Verify groups exist: `dev`, `staging`, `prod`, `rac`, `standalone`, `dataguard`

### 2. Create Project

1. Create a new Project in AAP
2. Source: Git
3. URL: `https://github.com/cmodiano/oracle_automation.git`
4. Branch: `main`
5. Update on launch: Yes

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
| Oracle - Full Converge | `playbooks/converge.yml` | `oracle_environment` | `target_db` |
| Oracle - Create Database | `playbooks/create_database.yml` | `oracle_environment`, `target_db` | |
| Oracle - Manage PDBs | `playbooks/manage_pdbs.yml` | `oracle_environment` | `target_db` |
| Oracle - Manage Schemas | `playbooks/manage_schemas.yml` | `oracle_environment` | `target_db`, `target_pdb` |
| Oracle - Manage Tablespaces | `playbooks/manage_tablespaces.yml` | `oracle_environment` | `target_db`, `target_pdb` |
| Oracle - Manage Parameters | `playbooks/manage_parameters.yml` | `oracle_environment` | `target_db` |
| Oracle - Manage Services | `playbooks/manage_services.yml` | `oracle_environment` | `target_db` |
| Oracle - Manage Directories | `playbooks/manage_directories.yml` | `oracle_environment` | `target_db` |
| Oracle - Manage Scheduler | `playbooks/manage_scheduler.yml` | `oracle_environment` | `target_db` |
| Oracle - Apply Patch | `playbooks/apply_patch.yml` | `oracle_environment`, `oracle_home_name`, `patch_id`, `patch_base` | `rolling`, `opatchauto` |
| Oracle - DG Switchover | `playbooks/dataguard_switchover.yml` | `oracle_environment`, `target_db`, `switchover_target` | |
| Oracle - DG Failover | `playbooks/dataguard_failover.yml` | `oracle_environment`, `target_db`, `failover_target` | |
| Oracle - Database Facts | `playbooks/database_facts.yml` | `oracle_environment` | `target_db` |
| Oracle - Install Infra | `playbooks/install_infrastructure.yml` | `oracle_environment` | |

All templates should use:
- **Inventory**: Your Oracle inventory
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

**Quarterly Patching:**
```
Database Facts (pre-patch) -> Apply Patch -> Database Facts (post-patch)
```

**Environment Refresh:**
```
Database Facts (source) -> Database Refresh -> Manage Parameters (target)
    -> Manage Schemas (target) -> Database Facts (target)
```

## Variable Flow

```
Inventory repo group_vars          oracle_automation databases/
(loaded by AAP Inventory)          (loaded by catalog_loader role)
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
