# Inventory Contract

This document defines the contract between the **inventory repo** (AAP Inventory Source) and the **oracle_automation repo** (AAP Project). Both repos must agree on group names and host variables for the automation to work correctly.

## Required Groups

The inventory must define these groups and assign hosts to exactly one group per category.

### Environment groups (mutually exclusive)

| Group | Purpose |
|---|---|
| `dev` | Development environment |
| `staging` | Staging / pre-production environment |
| `prod` | Production environment |

Each host must belong to exactly one environment group. The group name is used to:
- Load environment-specific Oracle settings from `group_vars/{env}.yml` in the inventory repo
- Select the correct `host_mapping` entry in database catalog files
- Select the correct overlay from `databases/overlays/{env}/`

### Topology groups (mutually exclusive)

| Group | Purpose |
|---|---|
| `rac` | Oracle Real Application Clusters |
| `standalone` | Standalone (single-instance) database server |
| `dataguard` | Data Guard standby server |

Each host must belong to exactly one topology group. The group name drives:
- `oracle_install_type` and `oracle_dbconfig_type` via `group_vars/{topology}.yml`
- Service management module selection (CRS vs DBMS_SERVICE)
- Guard clauses for DDL operations (RAC first_node, DG primary)

## Required Host Variables

These variables must be set as host_vars in the inventory (by the Python inventory builder or as static host_vars).

| Variable | Required | Description |
|---|---|---|
| `first_rac_node` | RAC only | Hostname of the node that runs DDL operations. Must match `inventory_hostname` of one host in the RAC cluster. If not set, catalog_loader uses the first host in `host_mapping.hosts`. |

## Required Group Variables (in inventory repo)

These group_vars files must exist in the inventory repo and are loaded automatically by AAP.

### `group_vars/all/oracle_defaults.yml`
- `oracle_base`, `oracle_install_dir_*` paths
- `oracle_os_user`, `oracle_os_uid`, `oracle_os_groups`
- `oracle_homes` dict (keyed by name, each entry has `version`, `path`, `edition`, `media`)
- `oracle_db_options_default` dict
- `oracle_default_characterset`, `oracle_default_national_characterset`
- `oracle_default_storage_type`, `oracle_default_diskgroup`

### `group_vars/all/default_users.yml`
- `oracle_default_cdb_users` dict (keyed by username) - users created in every CDB
- `oracle_default_pdb_users` dict (keyed by username) - users created in every PDB
- Both are merged as a base layer; per-catalog definitions override via recursive merge
- Set `state: absent` in a catalog to suppress a default user for that database/PDB

### `group_vars/all/profiles.yml`
- `oracle_standard_profiles` dict (keyed by profile name: APP_PROFILE, SVC_PROFILE, etc.)

### `group_vars/all/parameters.yml`
- `oracle_baseline_parameters` dict (security/audit params enforced on all databases)

### `group_vars/all/vault_lookups.yml`
- `vault_addr`, `vault_engine`, `vault_auth_method`

### `group_vars/dev.yml`
- `oracle_environment: dev`
- `oracle_env_db_defaults` dict (archivelog, force_logging, etc.)
- `oracle_env_parameters` dict (env-specific init params)
- `oracle_dev_memory_scale` (optional memory scaling factor)

### `group_vars/staging.yml` and `group_vars/prod.yml`
Same structure as dev.yml with environment-appropriate values.

### `group_vars/rac.yml`
- `oracle_install_type: rac`
- `oracle_dbconfig_type: RAC`

### `group_vars/standalone.yml`
- `oracle_install_type: standalone`
- `oracle_dbconfig_type: SI`

### `group_vars/dataguard.yml`
- `oracle_dg_defaults` dict

## Naming Convention

Group names are case-sensitive and must match exactly. If the inventory repo renames a group (e.g., `dev` to `development`), the following will break:
- `group_vars/{env}.yml` in the inventory repo
- `host_mapping` keys in `databases/*.yml`
- Overlay directory names in `databases/overlays/{env}/`
- Assertions in playbooks checking `oracle_environment in ['dev', 'staging', 'prod']`

## Validation

The `catalog_loader` role validates at runtime:
- `oracle_environment is defined`
- `oracle_environment in ['dev', 'staging', 'prod']`
- `oracle_homes is defined`

The `tests/validate_catalog.py` script validates:
- All `host_mapping` keys match expected environment names
- All `oracle_home` references exist in a reference oracle_homes dict
