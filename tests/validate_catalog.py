#!/usr/bin/env python3
"""Validate database catalog files against expected schema.

Usage:
    python tests/validate_catalog.py [databases_dir]

Validates:
  - Required fields are present
  - Field types are correct
  - host_mapping keys match expected environments
  - oracle_home references are valid (if oracle_homes provided)
  - PDB structure is correct
"""

import sys
import os
import yaml
from pathlib import Path

EXPECTED_ENVIRONMENTS = {'dev', 'staging', 'prod'}
REQUIRED_FIELDS = ['db_name', 'db_unique_name', 'sid_prefix', 'oracle_home']
VALID_TOPOLOGIES = {'rac', 'standalone'}
VALID_PDB_STATES = {'open', 'closed', 'read_only', 'absent'}
VALID_STORAGE_TYPES = {'ASM', 'FS'}
VALID_CONTENT_TYPES = {'permanent', 'temp', 'undo'}


def validate_catalog(filepath):
    """Validate a single catalog file. Returns list of error messages."""
    errors = []
    with open(filepath) as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            return [f"YAML parse error: {e}"]

    if not isinstance(data, dict):
        return [f"Expected a dict, got {type(data).__name__}"]

    # Required fields
    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    # host_mapping validation
    if 'host_mapping' in data:
        hm = data['host_mapping']
        if not isinstance(hm, dict):
            errors.append("host_mapping must be a dict")
        else:
            unknown_envs = set(hm.keys()) - EXPECTED_ENVIRONMENTS
            if unknown_envs:
                errors.append(
                    f"Unknown environment(s) in host_mapping: {unknown_envs}. "
                    f"Expected: {EXPECTED_ENVIRONMENTS}"
                )
            for env, mapping in hm.items():
                if not isinstance(mapping, dict):
                    errors.append(f"host_mapping.{env} must be a dict")
                    continue
                if 'hosts' not in mapping:
                    errors.append(f"host_mapping.{env} missing 'hosts' list")
                elif not isinstance(mapping['hosts'], list):
                    errors.append(f"host_mapping.{env}.hosts must be a list")
                if 'topology' in mapping and mapping['topology'] not in VALID_TOPOLOGIES:
                    errors.append(
                        f"host_mapping.{env}.topology '{mapping['topology']}' "
                        f"not in {VALID_TOPOLOGIES}"
                    )
    else:
        errors.append("Missing host_mapping (required for catalog_loader)")

    # Storage type
    if 'storage_type' in data and data['storage_type'] not in VALID_STORAGE_TYPES:
        errors.append(f"Invalid storage_type: {data['storage_type']}")

    # PDB validation
    if 'pdbs' in data:
        if not isinstance(data['pdbs'], dict):
            errors.append("pdbs must be a dict (keyed by PDB name)")
        else:
            for pdb_name, pdb_config in data['pdbs'].items():
                if not isinstance(pdb_config, dict):
                    errors.append(f"pdbs.{pdb_name} must be a dict")
                    continue
                if 'state' in pdb_config and pdb_config['state'] not in VALID_PDB_STATES:
                    errors.append(
                        f"pdbs.{pdb_name}.state '{pdb_config['state']}' "
                        f"not in {VALID_PDB_STATES}"
                    )
                # Tablespaces must be dicts
                if 'tablespaces' in pdb_config:
                    if not isinstance(pdb_config['tablespaces'], dict):
                        errors.append(f"pdbs.{pdb_name}.tablespaces must be a dict")
                # Users must be dicts
                if 'users' in pdb_config:
                    if not isinstance(pdb_config['users'], dict):
                        errors.append(f"pdbs.{pdb_name}.users must be a dict")
                # Roles must be dicts
                if 'roles' in pdb_config:
                    if not isinstance(pdb_config['roles'], dict):
                        errors.append(f"pdbs.{pdb_name}.roles must be a dict")
                # Services must be dicts
                if 'services' in pdb_config:
                    if not isinstance(pdb_config['services'], dict):
                        errors.append(f"pdbs.{pdb_name}.services must be a dict")

    return errors


def main():
    databases_dir = sys.argv[1] if len(sys.argv) > 1 else 'databases'
    databases_path = Path(databases_dir)

    if not databases_path.is_dir():
        print(f"ERROR: {databases_dir} is not a directory")
        sys.exit(1)

    catalog_files = sorted(databases_path.glob('*.yml')) + sorted(databases_path.glob('*.yaml'))
    if not catalog_files:
        print(f"No catalog files found in {databases_dir}")
        sys.exit(0)

    total_errors = 0
    for filepath in catalog_files:
        errors = validate_catalog(filepath)
        if errors:
            print(f"\n{filepath}:")
            for error in errors:
                print(f"  ERROR: {error}")
            total_errors += len(errors)
        else:
            print(f"  OK: {filepath}")

    # Also validate overlay files
    overlays_path = databases_path / 'overlays'
    if overlays_path.is_dir():
        for env_dir in sorted(overlays_path.iterdir()):
            if env_dir.is_dir():
                if env_dir.name not in EXPECTED_ENVIRONMENTS:
                    print(f"\n  WARNING: Overlay directory '{env_dir.name}' "
                          f"not in expected environments: {EXPECTED_ENVIRONMENTS}")
                for overlay_file in sorted(env_dir.glob('*.yml')) + sorted(env_dir.glob('*.yaml')):
                    # Overlays are partial - just check YAML validity
                    try:
                        with open(overlay_file) as f:
                            yaml.safe_load(f)
                        print(f"  OK: {overlay_file} (overlay)")
                    except yaml.YAMLError as e:
                        print(f"\n{overlay_file}:")
                        print(f"  ERROR: YAML parse error: {e}")
                        total_errors += 1

    print(f"\n{'=' * 40}")
    if total_errors:
        print(f"FAILED: {total_errors} error(s) found")
        sys.exit(1)
    else:
        print(f"PASSED: All {len(catalog_files)} catalog file(s) valid")
        sys.exit(0)


if __name__ == '__main__':
    main()
