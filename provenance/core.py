import datetime
import os
import shutil
import time
from collections import namedtuple
from copy import copy

import toolz as t
from boltons import funcutils as bfu

from . import artifact_hasher as ah
from . import repos as repos
from . import serializers as s
from . import utils
from ._dependencies import dependencies
from .hashing import file_hash, hash


class ImpureFunctionError(Exception):
    pass


class MutatedArtifactValueError(Exception):
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
    name = metadata['name'] or '.'.join([info['module'], info['name']])
    info['identifiers'] = {'name': name,
                           'version': metadata['version'],
                           'input_hash_fn': metadata['input_hash_fn']}
    info['input_process_fn'] = metadata['input_process_fn']
    info['composite'] = metadata['returns_composite']
    info['archive_file'] = metadata['archive_file']
    info['custom_fields'] = metadata['custom_fields']
    info['preserve_file_ext'] = metadata['preserve_file_ext']
    info['use_cache'] = metadata['use_cache']
    info['read_only'] = metadata['read_only']
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
        info['serializer'] = metadata.get('serializer', 'auto') or 'auto'
        info['load_kwargs'] = metadata.get('load_kwargs', None)
        info['dump_kwargs'] = metadata.get('dump_kwargs', None)
        valid_serializer = (info['serializer'] == 'auto'
                            or info['serializer'] in s.serializers)

    if not valid_serializer:
        msg = 'Invalid serializer option "{}" for artifact "{}", available serialziers: {} '.\
              format(info['serializer'], info['identifiers']['name'], tuple(s.serializers.keys()))
        raise ValueError(msg)

    return info


def hash_inputs(inputs, check_mutations=False, func_info=None):
    kargs = {}
    varargs = []
    all_artifacts = {}
    if func_info is None:
        func_info = {}

    for k, v in inputs['kargs'].items():
        h, artifacts = hash(v, hasher=ah.artifact_hasher())
        kargs[k] = h
        for a in artifacts:
            comp = all_artifacts.get(a.id, (a, []))
            comp[1].append(k)
            all_artifacts[a.id] = comp

    for i, v in enumerate(inputs['varargs']):
        h, artifacts = hash(v, hasher=ah.artifact_hasher())
        varargs.append(h)
        for a in artifacts:
            comp = all_artifacts.get(a.id, (a, []))
            comp[1].append("varargs[{}]".format(i))
            all_artifacts[a.id] = comp

    if check_mutations:
        for comp in all_artifacts.values():
            a, arg_names = comp
            if a.value_id != hash(a.value):
                msg = "Artifact {}, of type {} was mutated before being passed to {}.{} as arguments ({})"
                msg = msg.format(a.id, type(a.value), func_info.get('module'),
                                 func_info.get('name'), ",".join(arg_names))
                raise MutatedArtifactValueError(msg)

    input_hashes = {'kargs': kargs, 'varargs': tuple(varargs)}
    return (input_hashes, frozenset(all_artifacts.keys()))


def create_id(input_hashes, input_hash_fn, name, version):
    return t.thread_first(input_hashes,
                          input_hash_fn,
                          (t.merge, {'name': name, 'version': version}),
                          hash)


@t.curry
def composite_artifact(repo, _run_info, inputs, input_hashes, input_artifact_ids,
                       input_hash_fn, artifact_info, compute_duration,
                       computed_at, use_cache, read_only, key, value):
    start_hash_time = time.time()
    info = copy(artifact_info)
    info['composite'] = False
    info['name'] = '{}_{}'.format(info['name'], key)
    info['serializer'] = info['serializer'].get(key, 'auto')
    info['load_kwargs'] = info['load_kwargs'].get(key, None)
    info['dump_kwargs'] = info['dump_kwargs'].get(key, None)



    if info['serializer'] == 'auto':
        info['serializer'] =  s.object_serializer(value)

    id = create_id(input_hashes, input_hash_fn, info['name'], info['version'])
    hash_duration = time.time() - start_hash_time

    start_value_id_time = time.time()
    value_id = hash(value)
    value_id_duration = time.time() - start_hash_time


    if not use_cache:
        id = hash(id + value_id)
    try:
        artifact = repo.get_by_id(id)
    except KeyError:
        record = ArtifactRecord(id=id, value_id=value_id, value=value,
                                input_artifact_ids=input_artifact_ids,
                                value_id_duration=value_id_duration,
                                compute_duration=compute_duration,
                                hash_duration=hash_duration, computed_at=computed_at,
                                inputs=inputs, run_info=_run_info, **info)
        if read_only:
            artifact = repos._artifact_from_record(repo, record)
        else:
            artifact = repo.put(record)

    return artifact


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
        artifact_info_ = copy(artifact_info)
        r = repo
        if repo is None:
            r = repos.get_default_repo()
        elif isinstance(repo, str):
            r = repos.get_repo_by_name(repo)

        _run_info = run_info()
        archive_file = func_info['archive_file']

        if func_info['use_cache'] is None:
            use_cache = repos.get_use_cache()
        else:
            use_cache = func_info['use_cache']
        if func_info['read_only'] is None:
            read_only = repos.get_read_only()
        else:
            read_only = func_info['read_only']

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

        input_hashes, input_artifact_ids = hash_inputs(inputs, repos.get_check_mutations(), func_info)

        id = create_id(input_hashes, **func_info['identifiers'])
        hash_duration = time.time() - start_hash_time

        if use_cache:
            try:
                artifact = r.get_by_id(id)
            except KeyError:
                artifact = None
        else:
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

            if artifact_info_['composite']:
                input_hash_fn = func_info['identifiers']['input_hash_fn']
                ca = composite_artifact(r, _run_info, inputs, input_hashes,
                                        input_artifact_ids, input_hash_fn,
                                        artifact_info, compute_duration,
                                        computed_at, use_cache, read_only)
                value = {k: ca(k, v) for k, v in value.items()}
                artifact_info_['serializer'] = 'auto'
                artifact_info_['load_kwargs'] = None
                artifact_info_['dump_kwargs'] = None


            if artifact_info_['serializer'] == 'auto':
                artifact_info_['serializer'] =  s.object_serializer(value)



            start_value_id_time = time.time()
            if archive_file:
                if not archive_file_helper:
                    filename = value
                    value_id = _archive_file_hash(filename, func_info['preserve_file_ext'])
                value = ArchivedFile(value_id, filename, in_repo=False)
            else:
                value_id = hash(value)
            value_id_duration = time.time() - start_value_id_time

            if not use_cache:
                id = hash(id + value_id)
                try:
                    artifact = r.get_by_id(id)
                except KeyError:
                    artifact = None

            if artifact is None:
                record = ArtifactRecord(id=id, value_id=value_id, value=value,
                                        input_artifact_ids=input_artifact_ids,
                                        value_id_duration=value_id_duration,
                                        compute_duration=compute_duration,
                                        hash_duration=hash_duration,
                                        computed_at=computed_at, run_info=_run_info,
                                        inputs=inputs, **artifact_info_)
                if read_only:
                    artifact = repos._artifact_from_record(r, record)
                else:
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


def ensure_proxies(*parameters):
    """Decorator that ensures that the provided parameters are always arguments of type ArtifactProxy.

    When no parameters are passed then all arguments will be checked.

    This is useful to use on functions where you want to make sure artifacts
    are being passed in so lineage can be tracked.
    """

    def decorator(func):
        base_fn = _base_fn(func)
        extract_args = utils.args_extractor(base_fn, merge_defaults=True)

        @bfu.wraps(func)
        def check_args(*args, **kargs):
            _varargs, argsd = extract_args(args, kargs)
            not_valid = None
            if len(parameters) == 0:
                not_valid = [p for p, a in argsd.items() if not repos.is_proxy(a)]
            else:
                not_valid = [p for p in parameters if not repos.is_proxy(argsd[p])]
            if len(not_valid) > 0:
                msg = "Arguments must be `ArtifactProxy`s but were not: [{}]"\
                      .format(", ".join(not_valid))
                raise ValueError(msg)

            return func(*args, **kargs)

        return check_args

    return decorator


def provenance(version=0, repo=None, name=None, merge_defaults=None,
               ignore=None, input_hash_fn=None, remove=None, input_process_fn=None,
               archive_file=False, delete_original_file=False, preserve_file_ext=False,
               returns_composite=False, custom_fields=None,
               serializer=None, load_kwargs=None, dump_kwargs=None, use_cache=None,
               read_only=None, tags=None, _provenance_wrapper=provenance_wrapper):
    """
    Decorates a function so that all inputs and outputs are cached. Wraps the return
    value in a proxy that has an artifact attached to it allowing for the provenance
    to be tracked.


    Parameters
    ----------
    version : int
        Version of the code that is computing the value. You should increment this
        number when anything that has changed to make a previous version of an artifact
        outdated. This could be the function itself changing, other functions or libraries
        that it calls has changed, or an underlying data source that is being queried has
        updated data.

    repo : Repository or str
        Which repo this artifact should be saved in. The default repo is used when
        none is provided and this is the recommended approach. When you pass in a string
        it should be the name of a repo in the currently registered config.

    name : str
       The name of the artifact of the function being wrapped. If not provided it
       defaults to the function name (without the module).

    returns_composite : bool
       When set to True the function should return a dictionary. Each value of the
       returned dict will be serialized as an independent artifact. When the composite
       artifact is returned as a cached value it will be a dict-like object that will
       lazily pull back the artifacts as requested. You should use this when you need
       multiple artifacts created atomically but you do not want to fetch all the them
       simultaneously. That way you can lazily load only the artifacts you need.

    serializer : str
       The name of the serializer you want to use for this artifact. The built-in
       ones are 'joblib' (the default) and 'cloudpickle'. 'joblib' is optimized for
       numpy while 'cloudpickle' can serialize functions and other objects the standard
       python (and joblib) pickler cannot. You can also register your own serializer
       via the provenance.register_serializer function.

    dump_kwargs : dict
       A dict of kwargs to be passed to the serializer when dumping artifacts
       associated with this function. This is rarely used.

    load_kwargs : dict
       A dict of kwargs to be passed to the serializer when loading artifacts
       associated with this function. This is rarely used.

    ignore : list, tuple, or set
       A list of parameters that should be ignored when computing the input hash.
       This way you can mark certain parameters as invariant to the computed result.
       An example of this would be a parameter indicating how many cores should be
       used to compute a result. If the result is invariant the number of cores you
       would want to ignore it so the value isn't recomputed when a different number
       of cores is used.

    remove : list, tuple, or set
       A list of parameters that should be removed prior to hashing and saving
       of the inputs. The distinction between this and the ignore parameter is
       that with the ignore the parameters the ignored parameters are still recorded.
       The motivation to not-record, i.e. remove, certain parameters usually driven
       by performance or storage considerations.

    input_hash_fn : function
        A function that takes a dict of all on the argument's hashes with the
        structure of {'kargs': {'param_a': '1234hash'}, 'varargs': ('deadbeef',..)}.
        It should return a dict of the same shape but is able to change this dict
        as needed.  The main use case for this function is overshadowed by the
        ignore parameter and so this parameter is hardly ever used.

    input_process_fn : function
        A function that pre-processes the function's inputs before they are hashed
        or saved. The function takes a dict of all on the functions arguments with the
        structure of {'kargs': {'param_a': 42}, 'varargs': (100,..)}.
        It should return a dict of the same shape but is able to change this dict
        as needed.  The main use case for this function is overshadowed by the
        remove parameter and the value_repr function.

    merge_defaults : bool or list of parameters to be merged
        When True then the wrapper introspects the argspec of the function being
        decorated to see what keyword arguments have default dictionary values. When
        a list of strings the list is taken to be the list of parameters you want to
        merge on.
        When a decorated function is called then the dictionary passed in as an
        argument is merged with the default dictionary. That way people only need
        to specify the keys they are overriding and don't have to specify all the
        default values in the default dictionary.

    use_cache : bool or None (default None)
        use_cache False turns off the caching effects of the provenance decorator,
        while still tracking the provenance of artifacts. This should only be used during
        quick local iterations of a function to avoid having to bump the version with
        each change. When set to None (the default) it defers to the global provenance
        use_cache setting.

    read_only: bool or None (default None)
        read_only True will prevent any artifacts from being persisted to the repo.
        This should be used when you want to load existing artifacts from provenance
        but you do not want to add artifacts if the one you're looking for does not
        exist. This is useful when consuming artifacts created elsewhere, or when
        you are doing quick iterations (as with use_cache False) but you still want
        to use the cache for existing artifacts. When set to None (the default) it
        defers to the global provenance read_only setting.

    custom_fields : dict
        A dict with types that serialize to json. These are saved for searching in
        the repository.

    tags : list, tuple or set
        Will be added to custom_fields as the value for the 'tags' key.

    archive_file : bool, defaults False
       When True then the return value of the wrapped function will be assumed to
       be a str or pathlike that represents a file that should be archived into
       the blobstore. This is a good option to use when the computation of a function
       can't easily be returned as an in-memory pickle-able python value.

    delete_original_file : bool, defaults False
       To be used in conjunction with archive_file=True, when delete_original_file
       is True then the returned file will be deleted after it has been archived.

    preserve_file_ext : bool, default False
       To be used in conjunction with archive_file=True, when preserve_file_ext is
       True then id of the artifact archived will be the hash of the file contents
       plus the file extension of the original file. The motivation of setting this to
       True would be if you wanted to be able to look at the contents of a blobstore
       on disk and being able to preview the contents of an artifact with your
       regular OS tools (e.g. viewing images or videos).

    Returns
    -------
    ArtifactProxy
        Returns the value of the decorated function as a proxy. The proxy
        will act exactly like the original object/value but will have an
        artifact method that returns the Artifact associated with the value.
        This wrapped value should be used with all other functions that are wrapped
        with the provenance decorator as it will help track the provenance and also
        reduce redundant storage of a given value.
    """
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
        _custom_fields = custom_fields or {}
        if tags:
            _custom_fields['tags'] = tags
        f._provenance_metadata = {'version': version,
                                  'name': name,
                                  'archive_file': archive_file,
                                  'delete_original_file': delete_original_file,
                                  'input_hash_fn': input_hash_fn,
                                  'input_process_fn': input_process_fn,
                                  'archive_file': archive_file,
                                  'delete_original_file': delete_original_file,
                                  'preserve_file_ext': preserve_file_ext,
                                  'returns_composite': returns_composite,
                                  'archive_file': archive_file,
                                  'custom_fields': _custom_fields,
                                  'serializer': serializer,
                                  'load_kwargs': load_kwargs,
                                  'dump_kwargs': dump_kwargs,
                                  'use_cache': use_cache,
                                  'read_only': read_only}
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
        path = repo._filename(self.blob_id)
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
    """(beta) Copies or moves the provided filename into the Artifact Repository so it can
    be used as an ``ArtifactProxy`` to inputs of other functions.


    Parameters
    ----------
    archive_file : bool, defaults False
       When True then the return value of the wrapped function will be assumed to
       be a str or pathlike that represents a file that should be archived into
       the blobstore. This is a good option to use when the computation of a function
       can't easily be returned as an in-memory pickle-able python value.

    delete_original : bool, defaults False
       When delete_original_file True the file will be deleted after it has been archived.

    preserve_file_ext : bool, default False
       When True then id of the artifact archived will be the hash of the file contents
       plus the file extension of the original file. The motivation of setting this to
       True would be if you wanted to be able to look at the contents of a blobstore
       on disk and being able to preview the contents of an artifact with your
       regular OS tools (e.g. viewing images or videos).
    """

    # we want artifacts created by archive_file to be invariant to the
    # filename (see remove) but not the custom_fields.
    # filename is still passed in so the hash of the file on disk can be
    # computed as part of the id of the artifact.
    @provenance(archive_file=True, name=name or 'archive_file', preserve_file_ext=preserve_ext,
                delete_original_file=delete_original, remove=['_archive_file_filename'],
                custom_fields=custom_fields)
    def _archive_file(_archive_file_filename, custom_fields):
        return filename
    return _archive_file(filename, custom_fields)


def provenance_set(set_labels=None, initial_set=None, set_labels_fn=None):
    if set_labels and set_labels_fn:
        raise ValueError("You cannot provide both set_labels and set_labels_fn.")

    def make_wrapper(f):
        if set_labels_fn:
            base_fn = _base_fn(f)
            extract_args = utils.args_extractor(base_fn, merge_defaults=True)
            func_info = utils.fn_info(f)

        @bfu.wraps(f)
        def wrapper(*fargs, **fkargs):
            if set_labels_fn:
                varargs, argsd = extract_args(fargs, fkargs)
                varargs += func_info['varargs']
                argsd.update(func_info['kargs'])
                labels = set_labels_fn(*varargs, **argsd)
            else:
                labels = set_labels

            with repos.capture_set(labels=labels, initial_set=initial_set) as result:
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
