import datetime
import os
import shutil
import time
from collections import namedtuple

import toolz as t
from boltons import funcutils as bfu

from . import artifact_id_hasher as ah
from . import repos as repos
from . import serializers as s
from . import utils
from ._dependencies import dependencies
from .hashing import file_hash, hash
from .serializers import DEFAULT_VALUE_SERIALIZER


class ImpureFunctionError(Exception):
    pass

def get_metadata(f):
    if hasattr(f, '_provenance_metadata'):
        return f._provenance_metadata
    if hasattr(f, 'func'):
        return get_metadata(f.func)
    else:
        return {}


artifact_properties = ['id', 'value_id', 'inputs', 'fn_module', 'fn_name', 'value',
                       'name', 'version', 'composite', 'value_id_duration',
                       'serializer', 'load_kwargs', 'dump_kwargs',
                       'compute_duration', 'hash_duration', 'computed_at',
                       'custom_fields', 'input_artifact_ids', 'run_info']

ArtifactRecord = namedtuple('ArtifactRecord', artifact_properties)



def fn_info(f):
    info = utils.fn_info(f)
    metadata = get_metadata(f)
    info['identifiers'] = {'name': metadata['name'],
                           'version': metadata['version'],
                           'input_hash_fn': metadata['input_hash_fn']}
    info['input_process_fn'] = metadata['input_process_fn']
    info['composite'] = metadata['returns_composite']
    info['archive_file'] = metadata['archive_file']
    info['custom_fields'] = metadata['custom_fields']
    info['preserve_file_ext'] = metadata['preserve_file_ext']

    if info['composite']:
        if info['archive_file']:
            raise NotImplementedError("Using 'composite' and 'archive_file' is not supported.")
        info['serializer'] = metadata['serializer'] or {}
        info['load_kwargs'] = metadata['load_kwargs'] or {}
        info['dump_kwargs'] = metadata['dump_kwargs'] or {}
        valid_serializer = isinstance(info['serializer'], dict)
        for serializer in info['serializer'].values():
            valid_serializer = valid_serializer and serializer in s.serializers
            if not valid_serializer:
                break
    elif info['archive_file']:
        serializer = metadata['serializer'] or 'file'
        if serializer != 'file':
            raise ValueError("With 'archive_file' set True the only valid 'serializer' is 'file'")
        if metadata.get('dump_kwargs') is not None:
            raise ValueError("With 'archive_file' set True you may not specify any dump_kwargs.")
        if metadata.get('load_kwargs') is not None:
            raise ValueError("With 'archive_file' set True you may not specify any load_kwargs.")
        info['serializer'] = 'file'
        info['load_kwargs'] = metadata['load_kwargs'] or {}
        info['dump_kwargs'] = (metadata['dump_kwargs']
                               or {'delete_original': metadata['delete_original_file']})
        info['delete_original_file'] = metadata['delete_original_file']
        valid_serializer = True
    else:
        info['serializer'] = metadata.get('serializer', DEFAULT_VALUE_SERIALIZER.name) or DEFAULT_VALUE_SERIALIZER.name
        info['load_kwargs'] = metadata.get('load_kwargs', None)
        info['dump_kwargs'] = metadata.get('dump_kwargs', None)
        valid_serializer = info['serializer'] in s.serializers

    if not valid_serializer:
        msg = 'Invalid serializer option "{}" for artifact "{}", available serialziers: {} '.\
              format(info['serializer'], info['name'], tuple(s.serializers.keys()))
        raise ValueError(msg)

    return info


def hash_inputs(inputs):
    kargs = {}
    varargs = []
    all_ids = frozenset()

    for k, v in inputs['kargs'].items():
        h, ids = hash(v, hasher=ah.artifact_id_hasher())
        kargs[k] = h
        all_ids |= ids

    for v in inputs['varargs']:
        h, ids = hash(v, hasher=ah.artifact_id_hasher())
        varargs.append(h)
        all_ids |= ids

    input_hashes = {'kargs': kargs, 'varargs': tuple(varargs)}
    return (input_hashes, all_ids)


def create_id(input_hashes, input_hash_fn, name, version):
    return t.thread_first(input_hashes,
                          input_hash_fn,
                          (t.merge, {'name': name, 'version': version}),
                          hash)


@t.curry
def composite_artifact(repo, _run_info, inputs, input_hashes, input_artifact_ids,
                       input_hash_fn, artifact_info, compute_duration,
                       computed_at, key, value):
    start_hash_time = time.time()
    info = artifact_info.copy()
    info['composite'] = False
    info['name'] = '{}_{}'.format(info['name'], key)
    info['serializer'] = info['serializer'].get(key, DEFAULT_VALUE_SERIALIZER.name)
    info['load_kwargs'] = info['load_kwargs'].get(key, None)
    info['dump_kwargs'] = info['dump_kwargs'].get(key, None)

    id = create_id(input_hashes, input_hash_fn, info['name'], info['version'])
    hash_duration = time.time() - start_hash_time

    start_value_id_time = time.time()
    value_id = hash(value)
    value_id_duration = time.time() - start_hash_time

    record = ArtifactRecord(id=id, value_id=value_id, value=value,
                            input_artifact_ids=input_artifact_ids,
                            value_id_duration=value_id_duration,
                            compute_duration=compute_duration,
                            hash_duration=hash_duration, computed_at=computed_at,
                            inputs=inputs, run_info=_run_info, **info)
    return repo.put(record)

def _base_fn(f):
    if utils.is_curry_func(f):
        return utils.inner_function(f)
    else:
        return f


_EXT_MAPPINGS = {'mpeg': 'mpg',
                 'jpeg': 'jpg'}
def _extract_extension(filename):
    ext = os.path.splitext(filename)[1]
    if len(ext) > 0:
        ext = ext.lower().strip()[1:]
        return '.' + _EXT_MAPPINGS.get(ext, ext)
    else:
        return ext


def _archive_file_hash(filename, preserve_file_ext):
    if hasattr(filename, '__fspath__'):
        filename = filename.__fspath__()
    else:
        filename = str(filename)
    if not os.path.exists(filename):
        raise FileNotFoundError("Unable to archive file, {}, because it doesn't exist!".format(filename))
    # TODO: figure out best place to put the hash_name config and use in both cases
    value_id = file_hash(filename)
    if preserve_file_ext:
        extension = _extract_extension(filename)
        value_id += extension
    return value_id


def run_info():
    return repos.Config.current().run_info()


@t.curry
def provenance_wrapper(repo, f):
    base_fn = _base_fn(f)
    extract_args = utils.args_extractor(base_fn, merge_defaults=True)
    func_info = fn_info(f)
    input_process_fn = func_info['input_process_fn']

    artifact_info = {'name': func_info['identifiers']['name'],
                     'version': func_info['identifiers']['version'],
                     'fn_name': func_info['name'], 'fn_module': func_info['module'],
                     'custom_fields': func_info['custom_fields'],
                     'serializer': func_info['serializer'],
                     'load_kwargs': func_info['load_kwargs'],
                     'dump_kwargs': func_info['dump_kwargs'],
                     'composite': func_info['composite']}

    @bfu.wraps(f)
    def _provenance_wrapper(*args, **kargs):
        r = repos.get_default_repo() if repo is None else repo
        _run_info = run_info()
        archive_file = func_info['archive_file']

        start_hash_time = time.time()
        varargs, argsd = extract_args(args, kargs)
        raw_inputs = {'varargs': varargs + func_info['varargs'],
                      'kargs': t.merge(argsd, func_info['kargs'])}
        inputs = input_process_fn(raw_inputs)

        value_id = None
        filename = None
        archive_file_helper = archive_file and '_archive_file_filename' in raw_inputs['kargs']
        if archive_file_helper:
            filename = raw_inputs['kargs']['_archive_file_filename']
            value_id = _archive_file_hash(filename, func_info['preserve_file_ext'])
            inputs['filehash'] = value_id

        input_hashes, input_artifact_ids = hash_inputs(inputs)

        id = create_id(input_hashes, **func_info['identifiers'])
        hash_duration = time.time() - start_hash_time

        try:
            artifact = r.get_by_id(id)
        except KeyError:
            artifact = None

        if artifact is None:
            start_compute_time = time.time()
            computed_at = datetime.datetime.utcnow()
            value = f(*varargs, **argsd)
            compute_duration = time.time() - start_compute_time

            post_input_hashes, _ = hash_inputs(inputs)
            if id != create_id(post_input_hashes, **func_info['identifiers']):
                modified_inputs = []
                kargs = input_hashes['kargs']
                varargs = input_hashes['varargs']
                for name, _hash in post_input_hashes['kargs'].items():
                    if _hash != kargs[name]:
                        modified_inputs.append(name)
                for i, _hash in enumerate(post_input_hashes['varargs']):
                    if _hash != varargs[i]:
                        modified_inputs.append("varargs[{}]".format(i))
                msg = "The {}.{} function modified arguments: ({})".format(
                    func_info['module'], func_info['name'], ",".join(modified_inputs))
                raise ImpureFunctionError(msg)

            if artifact_info['composite']:
                input_hash_fn = func_info['identifiers']['input_hash_fn']
                ca = composite_artifact(r, _run_info, inputs, input_hashes,
                                        input_artifact_ids, input_hash_fn,
                                        artifact_info, compute_duration,
                                        computed_at)
                value = {k: ca(k, v) for k, v in value.items()}
                artifact_info['serializer'] = DEFAULT_VALUE_SERIALIZER.name
                artifact_info['load_kwargs'] = None
                artifact_info['dump_kwargs'] = None


            start_value_id_time = time.time()
            if archive_file:
                if not archive_file_helper:
                    filename = value
                    value_id = _archive_file_hash(filename, func_info['preserve_file_ext'])
                value = ArchivedFile(value_id, filename, in_repo=False)
            else:
                value_id = hash(value)
            value_id_duration = time.time() - start_value_id_time

            record = ArtifactRecord(id=id, value_id=value_id, value=value,
                                    input_artifact_ids=input_artifact_ids,
                                    value_id_duration=value_id_duration,
                                    compute_duration=compute_duration,
                                    hash_duration=hash_duration,
                                    computed_at=computed_at, run_info=_run_info,
                                    inputs=inputs, **artifact_info)
            artifact = r.put(record)

            if archive_file:
                # mark the file as in the repo (yucky, I know)
                artifact.value.in_repo = True

        elif archive_file_helper and func_info.get('delete_original_file', False):
        # if we hit an artifact with archive_file we may still need to clean up the
        # referenced file. This is normally taken care of when the file is 'serialzied'
        # (see file_dump), but in the case of an artifact hit this would never happen.
        # One potential downside of this approach is that this local file will be
        # deleted and if the artifact value (i.e. the existing file) is not local
        # yet it will download the file that we just deleted. Another approach would
        # be to do a a put_overwrite which would potentially upload files multiple times.
        # So for now, the cleanest way is to accept the potential re-downloading of data.
            os.remove(filename)


        return artifact.proxy()

    if utils.is_curry_func(f):
        fb = bfu.FunctionBuilder.from_func(utils.inner_function(f))
        for arg in f.args + tuple(f.keywords.keys()):
            fb.remove_arg(arg)
        param_info = utils.param_info(f)
        args = []
        defaults = []
        for arg, value in param_info.items():
            args.append(arg)
            if value != utils.UNSPECIFIED_ARG:
               defaults.append(value)
        arg_inv = ['{}={}'.format(arg,arg) for arg in args]
        fb.body = 'return _provenance_wrapper(%s)' % ", ".join(arg_inv)
        fb.args = args
        fb.defaults = tuple(defaults)
        execdict = {'_provenance_wrapper': _provenance_wrapper}
        ret = fb.get_func(execdict, with_dict=True)
        return ret
    return _provenance_wrapper


def remove_inputs_fn(to_remove):
    def remove_inputs(inputs):
        kargs = t.keyfilter(lambda k: k not in to_remove, inputs['kargs'])
        return {'kargs': kargs, 'varargs': inputs['varargs']}
    return remove_inputs


def provenance(version=0, repo=None, name=None, merge_defaults=None,
               ignore=None, input_hash_fn=None, remove=None, input_process_fn=None,
               archive_file=False, delete_original_file=False, preserve_file_ext=False,
               returns_composite=False, custom_fields=None,
               serializer=None, load_kwargs=None, dump_kwargs=None,
               _provenance_wrapper=provenance_wrapper):
    if ignore and input_hash_fn:
        raise ValueError("You cannot provide both ignore and input_hash_fn")

    if ignore:
        ignore = frozenset(ignore)
        input_hash_fn = remove_inputs_fn(ignore)

    if not input_hash_fn:
        input_hash_fn = lambda inputs: inputs

    if remove and input_process_fn:
        raise ValueError("You cannot provide both remove and input_process_fn")

    if remove:
        remove = frozenset(remove)
        input_process_fn = remove_inputs_fn(remove)

    if not input_process_fn:
        input_process_fn = lambda inputs: inputs

    def wrapped(f):
        _name = name
        if _name is None:
            _name = f.__name__
        f._provenance_metadata = {'version': version,
                                  'name': _name,
                                  'archive_file': archive_file,
                                  'delete_original_file': delete_original_file,
                                  'input_hash_fn': input_hash_fn,
                                  'input_process_fn': input_process_fn,
                                  'archive_file': archive_file,
                                  'delete_original_file': delete_original_file,
                                  'preserve_file_ext': preserve_file_ext,
                                  'returns_composite': returns_composite,
                                  'archive_file': archive_file,
                                  'custom_fields': custom_fields or {},
                                  'serializer': serializer,
                                  'load_kwargs': load_kwargs,
                                  'dump_kwargs': dump_kwargs}
        f.__merge_defaults__ = merge_defaults
        return _provenance_wrapper(repo, f)

    return wrapped

class ArchivedFile(object):
    def __init__(self, id, original_filename=None, in_repo=True):
        self.blob_id = id
        self.original_filename = original_filename
        self.in_repo = in_repo

    def abspath(self):
        repo = repos.get_default_repo()
        path = repo.blobstore._filename(self.blob_id)
        return os.path.abspath(path)

    def __fspath__(self):
        return self.abspath() if self.in_repo else self.original_filename

    def __str__(self):
        return self.__fspath__()

    def __repr__(self):
        if self.original_filename:
            return "<ArchivedFile {}, {} >".format(self.blob_id, self.original_filename)
        else:
            return "<ArchivedFile {} >".format(self.blob_id)


def file_dump(archived_file, dest_filename, delete_original=False):
    op = shutil.move if delete_original else shutil.copy
    op(archived_file.original_filename, dest_filename)


def file_load(id):
    return ArchivedFile(id, in_repo=True)


s.register_serializer('file', file_dump, file_load)


def archive_file(filename, name=None, delete_original=False, custom_fields=None, preserve_ext=False):
    @provenance(archive_file=True, name=name or 'archive_file', preserve_file_ext=preserve_ext,
                delete_original_file=delete_original, remove=['_archive_file_filename'],
                custom_fields=custom_fields)

    # we want artifacts created by archive_file to be invariant to the
    # filename (see remove above) but # not the custom_fields.
    # filename is still passed in so the hash of the file on disk can be
    # computed as part of the id of the artifact.
    def _archive_file(_archive_file_filename, custom_fields):
        return filename
    return _archive_file(filename, custom_fields)


def provenance_set(set_name=None, initial_set=None, set_name_fn=None):
    if set_name and set_name_fn:
        raise ValueError("You cannot provide both set_name and set_name_fn.")

    def make_wrapper(f):
        if set_name_fn:
            base_fn = _base_fn(f)
            extract_args = utils.args_extractor(base_fn, merge_defaults=True)
            func_info = utils.fn_info(f)

        @bfu.wraps(f)
        def wrapper(*fargs, **fkargs):
            if set_name_fn:
                varargs, argsd = extract_args(fargs, fkargs)
                varargs += func_info['varargs']
                argsd.update(func_info['kargs'])
                name = set_name_fn(*varargs, **argsd)
            else:
                name = set_name

            with repos.capture_set(name=name, initial_set=initial_set) as result:
                f(*fargs, **fkargs)
            return result[0]

        return wrapper

    return make_wrapper


def promote(artifact_or_id, to_repo, from_repo=None):
    from_repo = from_repo if from_repo else repos.get_default_repo()
    artifact = repos.coerce_to_artifact(artifact_or_id, repo=from_repo)
    for a in dependencies(artifact):
        if a not in to_repo:
            to_repo.put(a)
