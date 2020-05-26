import inspect
from collections import OrderedDict, Sequence
import toolz as t
import toolz.curried as tc
from boltons import funcutils as bfu

from provenance.compatibility import getargspec


UNSPECIFIED_ARG = '::unspecified::'


def args_extractor(f, merge_defaults=False):
    """
    Takes a function, inspects it's parameter lists, and returns a
    function that will return all of the named and key arguments
    back as a dictionary. The varargs are also returned which don't
    have a names.

    """
    spec = inspect.getfullargspec(f)
    if spec.defaults:
        param_defaults = dict(zip(spec.args[-len(spec.defaults):],
                                   spec.defaults))
    else:
        param_defaults = {}
    named_param_defaults = spec.kwonlydefaults or {}
    default_dicts = {}
    num_named_args = len(spec.args)

    if merge_defaults is True and hasattr(f, '__merge_defaults__'):
        merge_defaults = f.__merge_defaults__


    if merge_defaults:
        default_dicts = t.pipe(t.merge(named_param_defaults, param_defaults),
                               tc.valfilter(lambda v: isinstance(v, dict)))

        if isinstance(merge_defaults, Sequence):
            default_dicts = {k:default_dicts[k] for k in merge_defaults}

        def _args_dict(args, kargs):
            unnamed_args = dict(zip(spec.args, args[0:num_named_args]))
            varargs = args[num_named_args:]
            kargs = t.merge(kargs, unnamed_args)
            for k, d in default_dicts.items():
                kargs[k] = t.merge(d, kargs.get(k) or {})
            return varargs, kargs
    else:
        def _args_dict(args, kargs):
            unnamed_args = dict(zip(spec.args, args[0:num_named_args]))
            varargs = args[num_named_args:]
            kargs = t.merge(kargs, unnamed_args)
            return varargs, kargs
    
    return _args_dict


def with_merged_defaults(*kwargs_to_default):
    """
    Introspects the argspec of the function being decorated to see what
    keyword arguments take dictionaries. If a dictionary is passed in when
    then function is called then it is merged with the dictionary defined
    in the parameter list.
    """
    merge_defaults = True
    if len(kwargs_to_default) > 0:
        merge_defaults = kwargs_to_default

    def _with_merged_defaults(f):
        extract_kargs = args_extractor(f, merge_defaults)

        @bfu.wraps(f)
        def _merge_defaults(*args, **kargs):
            vargs, kargs = extract_kargs(args, kargs)
            return f(*vargs, **kargs)
        _merge_defaults.__merge_defaults__ = merge_defaults

        return _merge_defaults
    return _with_merged_defaults


def is_curry_func(f):
    """
    Checks if f is a toolz or cytoolz function by inspecting the available attributes.
    Avoids explicit type checking to accommodate all versions of the curry fn.
    """
    return hasattr(f, 'func') and hasattr(f, 'args') and hasattr(f, 'keywords')


def _func_param_info(argspec):
    params = argspec.args
    defaults = argspec.defaults or []
    start_default_ix = -max(len(defaults), 1) - 1
    values = [UNSPECIFIED_ARG] * (len(params) - len(defaults)) + \
             list(defaults[start_default_ix:])
    return OrderedDict(zip(params, values))


def param_info(f):
    if is_curry_func(f):
        argspec = getargspec(f.func)
        num_args = len(f.args)
        args_to_remove = argspec.args[0:num_args] + list(f.keywords.keys())
        base = _func_param_info(argspec)
        return t.dissoc(base, *args_to_remove)
    return(_func_param_info(getargspec(f)))


def inner_function(partial_fn):
    """Returns the wrapped function of either a partial or curried function."""
    fn = partial_fn.func
    if '__module__' not in dir(fn):
        # for some reason the curry decorator nests the actual function
        # metadata one level deeper
        fn = fn.func
    return fn


def partial_fn_info(partial_fn):
    fn = inner_function(partial_fn)
    varargs, kargs = args_extractor(fn)(partial_fn.args, partial_fn.keywords)
    return {'varargs': varargs, 'kargs': kargs,
            'module': fn.__module__, 'name': fn.__name__}

# TODO: consider using the functions in joblib.func_inspect, e.g. for the fn name
def fn_info(fn):
    if 'func' in dir(fn):
        return partial_fn_info(fn)
    return {'name': fn.__name__, 'module': fn.__module__,
            'varargs': (), 'kargs': {}}

def when_type(type):
    def _decorator(f):
        @bfu.wraps(f)
        def _when_type(val):
            if isinstance(val, type):
                return f(val)
            else:
                return val
        return _when_type
    return _decorator
