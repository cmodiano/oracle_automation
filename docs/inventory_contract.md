# Inventory & Configuration Reference

This document describes the inventory and configuration structure embedded in this repository. All configuration — inventory, group variables, database catalogs, and overlays — lives in a single repo for simplicity, testability, and DBA accessibility.

## Repository Layout

```
oracle_automation/
├── inventory/
│   ├── hosts.yml                    # Host inventory (or dynamic inventory plugin)
│   └── group_vars/
│       ├── all/
│       │   ├── oracle_defaults.yml  # Paths, OS users, oracle_homes dict
│       │   ├── profiles.yml         # Standard Oracle profiles
│       │   ├── parameters.yml       # Baseline init parameters (security/audit)
│       │   └── vault_lookups.yml    # Vault configuration (no secrets)
│       ├── dev.yml                  # Development environment overrides
│       ├── staging.yml              # Staging environment overrides
│       ├── prod.yml                 # Production environment overrides
│       ├── rac.yml                  # RAC topology settings
│       ├── standalone.yml           # Standalone topology settings
│       └── dataguard.yml            # Data Guard topology settings
│
├── databases/                       # Database catalog (one YAML per database)
│   ├── EXAMPLE.yml
│   └── overlays/                    # Per-environment overrides
│       ├── dev/
│       ├── staging/
│       └── prod/
```

## Required Groups

The inventory must define these groups and assign hosts to exactly one group per category.

### Environment groups (mutually exclusive)

| Group | Purpose |
|---|---|
| `dev` | Development environment |
| `staging` | Staging / pre-production environment |
| `prod` | Production environment |

Each host must belong to exactly one environment group. The group name is used to:
- Load environment-specific Oracle settings from `inventory/group_vars/{env}.yml`
- Select the correct `host_mapping` entry in database catalog files
- Select the correct overlay from `databases/overlays/{env}/`

### Topology groups (mutually exclusive)

| Group | Purpose |
|---|---|
| `rac` | Oracle Real Application Clusters |
| `standalone` | Standalone (single-instance) database server |
| `dataguard` | Data Guard standby server |

Each host must belong to exactly one topology group. The group name drives:
- `oracle_install_type` and `oracle_dbconfig_type` via `inventory/group_vars/{topology}.yml`
- Service management module selection (CRS vs DBMS_SERVICE)
- Guard clauses for DDL operations (RAC first_node, DG primary)

## Required Host Variables

| Variable | Required | Description |
|---|---|---|
| `first_rac_node` | RAC only | Hostname of the node that runs DDL operations. Must match `inventory_hostname` of one host in the RAC cluster. If not set, catalog_loader uses the first host in `host_mapping.hosts`. |

## Group Variables Reference

### `inventory/group_vars/all/oracle_defaults.yml`
- `oracle_base`, `oracle_install_dir_*` paths
- `oracle_os_user`, `oracle_os_uid`, `oracle_os_groups`
- `oracle_homes` dict (keyed by name, each entry has `version`, `path`, `edition`, `media`)
- `oracle_db_options_default` dict
- `oracle_default_characterset`, `oracle_default_national_characterset`
- `oracle_default_storage_type`, `oracle_default_diskgroup`

### `inventory/group_vars/all/profiles.yml`
- `oracle_standard_profiles` dict (keyed by profile name: APP_PROFILE, SVC_PROFILE, etc.)

### `inventory/group_vars/all/parameters.yml`
- `oracle_baseline_parameters` dict (security/audit params enforced on all databases)

### `inventory/group_vars/all/vault_lookups.yml`
- `vault_addr`, `vault_engine`, `vault_auth_method`

### `inventory/group_vars/{env}.yml` (dev, staging, prod)
- `oracle_environment` — environment identifier
- `oracle_env_db_defaults` dict (archivelog, force_logging, etc.)
- `oracle_env_parameters` dict (env-specific init params)
- `oracle_dev_memory_scale` (optional memory scaling factor)

### `inventory/group_vars/{topology}.yml` (rac, standalone, dataguard)
- `oracle_install_type` and `oracle_dbconfig_type` (rac, standalone)
- `oracle_dg_defaults` dict (dataguard)

## Naming Convention

Group names are case-sensitive and must match exactly. Renaming a group (e.g., `dev` to `development`) will break:
- `inventory/group_vars/{env}.yml`
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

## AAP Integration

When using AAP, there are two options:

1. **Single source** (recommended): Point both the AAP Project **and** Inventory Source to this repository. AAP will use `inventory/hosts.yml` (or a dynamic inventory script placed in `inventory/`) and automatically load the `group_vars/` from the same directory.

2. **Dynamic inventory override**: Replace `inventory/hosts.yml` with a dynamic inventory plugin or script. The `group_vars/` files will still be loaded automatically by Ansible as long as they are in the same `inventory/` directory.

## Automatic Catalog Updates (FPP Patching)

When Oracle FPP (Fleet Patching and Provisioning) patches a database and moves it to a new Oracle Home, the `catalog_updater` role automatically updates this repository:

1. **New Oracle Home registration**: If the new home doesn't exist in `inventory/group_vars/all/oracle_defaults.yml`, it is added automatically.

2. **Overlay update**: The database's environment overlay (`databases/overlays/{env}/{DB}.yml`) is created or updated with the new `oracle_home` reference. This preserves the base catalog file and allows per-environment progression (dev patched first, then staging, then prod).

3. **Git commit and push**: Changes are committed and pushed automatically.

### Data flow after FPP patching

```
FPP patches APPDB1 in dev (db_19_21 → db_19_23)
    │
    ├─► oracle_defaults.yml: db_19_23 entry added (if absent)
    ├─► databases/overlays/dev/APPDB1.yml: oracle_home: db_19_23
    └─► git commit + push
    
Result:
  dev:  catalog_loader merges base + overlay → oracle_home = db_19_23 ✓
  prod: catalog_loader merges base (no overlay) → oracle_home = db_19_21 (unchanged)
```

### Promoting patches across environments

When all environments are patched to the same version, the `oracle_home` change can be promoted from the overlay to the base catalog file and the overlays cleaned up. This is a manual step to maintain explicit control over the promotion.
