import os
import provenance.core as core
import provenance.blobstores as bs
import provenance.repos as r
import copy
import toolz as t

try:
    import provenance.sftp as sftp
except ImportError:
    print('To use the sftp blobstore install Paramiko')


import logging
logger = logging.getLogger(__name__)


@t.curry
def full_config(configs, base_config):
    if 'type' in base_config:
        return base_config
    prototype = full_config(configs, configs[base_config['prototype']])
    return t.thread_first(prototype,
                          (t.merge, base_config),
                          (t.dissoc, 'prototype'))


def merge_prototypes(config):
    return t.valmap(full_config(config), config)

@t.curry
def atomic_item_from_config(config, type_dict, item_plural, name=None):
    stype = config['type']
    if stype not in type_dict:
        raise Exception("{} may only be created of types: {}, you had {}".
                        format(item_plural, tuple(type_dict.keys()), stype))
    cls = type_dict[stype]
    kargs = t.dissoc(config, 'type')
    return cls(**kargs)


BLOBSTORE_TYPES = {'disk': bs.DiskStore, 's3': bs.S3Store, 'memory':
                   bs.MemoryStore, 'chained': bs.ChainedStore}


try:
    import provenance.sftp as sftp
    BLOBSTORE_TYPES['sftp'] = sftp.SFTPStore

except ImportError as e:
    class SFTPStore(object):
        def __init__(*args, **kargs):
            raise(e)

    BLOBSTORE_TYPES['sftp'] = SFTPStore


blobstore_from_config = atomic_item_from_config(type_dict=BLOBSTORE_TYPES,
                                           item_plural='Blobstores')

REPO_TYPES= {'postgres': r.PostgresRepo, 'memory': r.MemoryRepo,
             'chained': r.ChainedRepo}

repo_from_config = atomic_item_from_config(type_dict=REPO_TYPES,
                                           item_plural='Artifact Repos')

def items_from_config(config, atomic_from_config, items_name,
                      item_type, silence_warnings):
    config = merge_prototypes(copy.deepcopy(config))

    atomic_stores = {}
    for k, c in config.items():
        try:
            if c['type'] != 'chained':
                store = atomic_from_config(c, name=k)
                if store:
                    atomic_stores[k] = store
        except Exception as e:
            if not silence_warnings:
                logger.warning("Error creating %s %s from config - Skipping",
                               item_type, k, exc_info=True)

    def create_chained(name, config):
        # resolve the stores
        chained = {n for n in config[items_name] if n in atomic_stores}
        if len(chained) != len(config[items_name]):
            missing_configs = set(config[items_name]) - chained
            if not silence_warnings:
                logger.warning("Skipping chained %s %s due to missing %s: %s",
                               item_type, name, items_name, missing_configs)
            return None

        config[items_name] = [atomic_stores[n] for n in config[items_name]]
        return atomic_from_config(config, name=name)

    chained_stores = {}
    for k, c in config.items():
        try:
            if c['type'] == 'chained':
                store = create_chained(k, c)
                if store:
                    chained_stores[k] = store
        except Exception as e:
            if not silence_warnings:
                logger.warning("Error creating %s %s from config - Skipping",
                               item_type, k, exc_info=True)

    return t.merge(chained_stores, atomic_stores)


def blobstores_from_config(config, silence_warnings=False):
    return items_from_config(config, blobstore_from_config, 'stores', 'blobstore', silence_warnings)


def repos_from_config(config, blobstores, silence_warnings=False):
    def from_config(atomic_config, name):
        if 'store' in atomic_config:
            if not atomic_config['store'] in blobstores:
                if not silence_warnings:
                    logger.warning("Skipping %s repo due to missing store: %s",
                                   name, atomic_config['store'])
                return None

            atomic_config['store'] = blobstores[atomic_config['store']]
        return repo_from_config(atomic_config)
    return items_from_config(config, from_config, 'repos', 'repo', silence_warnings)


def from_config(config):
    silence_warnings = config.get('silence_warnings', False)
    blobstores = blobstores_from_config(config['blobstores'],
                                        silence_warnings)
    repos = repos_from_config(config['artifact_repos'], blobstores,
                              silence_warnings)
    return {'blobstores': blobstores, 'repos': repos}

def load_config(config):
    objs = from_config(config)
    pconfig = r.Config(objs['blobstores'], objs['repos'], config['default_repo'])
    r.Config.set_current(pconfig)
    return pconfig


def load_yaml_config(filename):
    import yaml
    with open(filename, 'r') as f:
        return load_config(yaml.load(f))
