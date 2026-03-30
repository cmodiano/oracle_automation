#!/usr/bin/env python3
"""Tests for the merge_default_users catalog filter."""

import sys
import os
import copy

# Add the plugins directory to the path so we can import the filter module.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'plugins', 'filter'))
from catalog_filters import FilterModule  # noqa: E402


merge = FilterModule.merge_default_users


def test_no_defaults_is_noop():
    """When both default dicts are empty, catalog is unchanged."""
    catalog = {'db_name': 'DB1', 'pdbs': {'PDB1': {'users': {'APP': {'state': 'present'}}}}}
    result = merge(catalog, {}, {})
    assert result == catalog


def test_default_cdb_users_injected():
    """Default CDB users appear at the CDB level."""
    catalog = {'db_name': 'DB1'}
    default_cdb = {
        'C##MONITOR': {'state': 'present', 'profile': 'SVC_PROFILE'},
    }
    result = merge(catalog, default_cdb, {})
    assert 'C##MONITOR' in result['users']
    assert result['users']['C##MONITOR']['profile'] == 'SVC_PROFILE'


def test_catalog_overrides_default_cdb_user():
    """Catalog-specific CDB user attrs override the default."""
    catalog = {
        'db_name': 'DB1',
        'users': {'C##MONITOR': {'profile': 'CUSTOM_PROFILE'}},
    }
    default_cdb = {
        'C##MONITOR': {'state': 'present', 'profile': 'SVC_PROFILE', 'locked': False},
    }
    result = merge(catalog, default_cdb, {})
    assert result['users']['C##MONITOR']['profile'] == 'CUSTOM_PROFILE'
    assert result['users']['C##MONITOR']['state'] == 'present'  # from default
    assert result['users']['C##MONITOR']['locked'] is False       # from default


def test_suppress_default_cdb_user():
    """A catalog can suppress a default CDB user with state: absent."""
    catalog = {
        'db_name': 'DB1',
        'users': {'C##MONITOR': {'state': 'absent'}},
    }
    default_cdb = {
        'C##MONITOR': {'state': 'present', 'profile': 'SVC_PROFILE'},
    }
    result = merge(catalog, default_cdb, {})
    assert result['users']['C##MONITOR']['state'] == 'absent'


def test_default_pdb_users_injected():
    """Default PDB users appear in every PDB."""
    catalog = {
        'db_name': 'DB1',
        'pdbs': {
            'PDB1': {'state': 'open', 'users': {}},
            'PDB2': {'state': 'open'},
        },
    }
    default_pdb = {
        'MONITORING': {'state': 'present', 'profile': 'SVC_PROFILE'},
    }
    result = merge(catalog, {}, default_pdb)
    assert 'MONITORING' in result['pdbs']['PDB1']['users']
    assert 'MONITORING' in result['pdbs']['PDB2']['users']


def test_catalog_overrides_default_pdb_user():
    """PDB-level user attrs in the catalog override the default."""
    catalog = {
        'db_name': 'DB1',
        'pdbs': {
            'PDB1': {
                'state': 'open',
                'users': {'MONITORING': {'default_tablespace': 'CUSTOM_TS'}},
            },
        },
    }
    default_pdb = {
        'MONITORING': {'state': 'present', 'profile': 'SVC_PROFILE', 'default_tablespace': 'SYSAUX'},
    }
    result = merge(catalog, {}, default_pdb)
    mon = result['pdbs']['PDB1']['users']['MONITORING']
    assert mon['default_tablespace'] == 'CUSTOM_TS'
    assert mon['profile'] == 'SVC_PROFILE'  # inherited from default


def test_suppress_default_pdb_user_per_pdb():
    """A specific PDB can suppress a default PDB user."""
    catalog = {
        'db_name': 'DB1',
        'pdbs': {
            'PDB1': {'state': 'open', 'users': {'MONITORING': {'state': 'absent'}}},
            'PDB2': {'state': 'open'},
        },
    }
    default_pdb = {'MONITORING': {'state': 'present'}}
    result = merge(catalog, {}, default_pdb)
    assert result['pdbs']['PDB1']['users']['MONITORING']['state'] == 'absent'
    assert result['pdbs']['PDB2']['users']['MONITORING']['state'] == 'present'


def test_catalog_not_mutated():
    """The original catalog dict is not modified."""
    catalog = {'db_name': 'DB1', 'pdbs': {'PDB1': {'state': 'open'}}}
    original = copy.deepcopy(catalog)
    merge(catalog, {'C##X': {'state': 'present'}}, {'Y': {'state': 'present'}})
    assert catalog == original


def test_both_cdb_and_pdb_defaults():
    """CDB and PDB defaults are both applied in one call."""
    catalog = {'db_name': 'DB1', 'pdbs': {'PDB1': {'state': 'open'}}}
    default_cdb = {'C##AUDIT': {'state': 'present'}}
    default_pdb = {'AUDIT_LOCAL': {'state': 'present'}}
    result = merge(catalog, default_cdb, default_pdb)
    assert 'C##AUDIT' in result['users']
    assert 'AUDIT_LOCAL' in result['pdbs']['PDB1']['users']


if __name__ == '__main__':
    test_no_defaults_is_noop()
    test_default_cdb_users_injected()
    test_catalog_overrides_default_cdb_user()
    test_suppress_default_cdb_user()
    test_default_pdb_users_injected()
    test_catalog_overrides_default_pdb_user()
    test_suppress_default_pdb_user_per_pdb()
    test_catalog_not_mutated()
    test_both_cdb_and_pdb_defaults()
    print("All tests passed.")
