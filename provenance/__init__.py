from __future__ import absolute_import, division, print_function

from ._config import from_config, load_config, load_yaml_config
from ._dependencies import dependencies
from .core import provenance, provenance_set, promote, archive_file
from .repos import (capture_set, create_set, get_default_repo, set_run_info_fn,
                    get_set_by_id, get_set_by_name, lazy_dict, lazy_proxy_dict,
                    load_artifact, load_proxy, set_default_repo, using_repo)

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions
