"""Custom Jinja2 filters for database catalog operations."""

import copy


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
        """Deep merge overlay onto base dict (recursive).

        Equivalent to combine(recursive=True) but available as a filter
        for complex pipeline expressions.

        Rules:
          - Scalars: overlay replaces base
          - Dicts: recursive merge
          - Lists: overlay replaces entire list
          - Key absent in overlay: base value preserved
        """
        result = copy.deepcopy(base)
        for key, value in overlay.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = FilterModule.catalog_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    @staticmethod
    def catalog_for_host(databases, hostname, environment):
        """Filter database list to those mapped to a specific host+environment.

        Each database catalog has a host_mapping dict:
          host_mapping:
            prod:
              hosts: [node1, node2]
            dev:
              hosts: [devnode1]

        Returns only databases where hostname appears in
        host_mapping[environment].hosts.
        """
        result = []
        for db in databases:
            mapping = db.get('host_mapping', {}).get(environment, {})
            if hostname in mapping.get('hosts', []):
                result.append(db)
        return result

    @staticmethod
    def resolve_oracle_home(home_name, oracle_homes):
        """Resolve oracle_home name to path from oracle_homes dict.

        The oracle_homes dict is defined in group_vars/all/oracle_defaults.yml:
          oracle_homes:
            db_19_21:
              version: "19.21.0.0"
              path: /oracle/u01/product/19.21.0.0

        This filter takes a key (e.g., 'db_19_21') and returns the path.
        """
        if home_name not in oracle_homes:
            raise ValueError(
                f"Oracle home '{home_name}' not found in oracle_homes dict. "
                f"Available: {list(oracle_homes.keys())}"
            )
        return oracle_homes[home_name]['path']
