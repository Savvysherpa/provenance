from __future__ import absolute_import, division, print_function

from ._config import from_config, load_config, load_yaml_config
from ._dependencies import dependencies
from ._version import get_versions
from .core import (archive_file, ensure_proxies, promote, provenance,
                   provenance_set)
from .hashing import hash, value_repr
from .repos import (capture_set, create_set, current_config,
                    get_check_mutations, get_default_repo, get_repo_by_name,
                    load_set_by_id, load_set_by_name, load_set_by_labels,
                    get_read_only, get_use_cache, is_proxy, lazy_dict,
                    lazy_proxy_dict, load_artifact, load_proxy,
                    set_check_mutations, set_default_repo, set_run_info_fn,
                    set_read_only, set_use_cache, using_repo)
from .serializers import register_serializer

__version__ = get_versions()['version']
del get_versions
