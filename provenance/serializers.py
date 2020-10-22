from collections import namedtuple
from functools import singledispatch

import cloudpickle
import joblib
import toolz as t

from .hashing import hash


def cloudpickle_dump(obj, filename, **kwargs):
    with open(filename, 'wb') as f:
        return cloudpickle.dump(obj, f, **kwargs)


def cloudpickle_load(filename, **kwargs):
    with open(filename, 'rb') as f:
        return cloudpickle.load(f, **kwargs)


Serializer = namedtuple(
    'Serializer',
    'name, dump, load, content_type, content_encoding, content_disposition',
)


def joblib_dump(obj, filename, compress=2, **kwargs):
    joblib.dump(obj, filename, compress=compress, **kwargs)


serializers = {}


@singledispatch
def object_serializer(obj):
    """
    Takes an object and returns the appropirate serializer name, dump, and load arguments.

    Parameters
    ----------
    obj : any python object or primitive

    Returns
    -------
    tuple of serializer name (str), dump args (dictionary), load args (dictionary)
    """
    return DEFAULT_VALUE_SERIALIZER.name


def register_serializer(
    name,
    dump,
    load,
    content_type=None,
    content_encoding=None,
    content_disposition=None,
    classes=None,
):
    serializers[name] = Serializer(
        name, dump, load, content_type, content_encoding, content_disposition
    )
    if classes is None:
        return
    for cls in classes:
        object_serializer.register(cls, lambda _: name)


register_serializer('joblib', joblib_dump, joblib.load)
register_serializer('cloudpickle', cloudpickle_dump, cloudpickle_load)


def _pandas_and_parquet_present():
    try:
        import pandas
    except ImportError:
        return False
    try:
        import pyarrow
    except:
        try:
            import fastparquet
        except ImportError:
            return False
    return True


if _pandas_and_parquet_present():
    import pandas as pd

    def pd_df_parquet_dump(df, filename, **kwargs):
        return df.to_parquet(filename, **kwargs)

    def pd_df_parquet_load(filename, **kwargs):
        return pd.read_parquet(filename, **kwargs)

    register_serializer(
        'pd_df_parquet', pd_df_parquet_dump, pd_df_parquet_load, classes=[pd.DataFrame]
    )

    def pd_series_parquet_dump(series, filename, **kwargs):
        if series.name is None:
            # pyarrow requires the column names be strings
            series = pd.Series(series, name='_series')
        return pd.DataFrame(series).to_parquet(filename, **kwargs)

    def pd_series_parquet_load(filename, **kwargs):
        series = pd.read_parquet(filename, **kwargs).ix[:, 0]
        if series.name == '_series':
            series.name = None
        return series

    register_serializer(
        'pd_series_parquet',
        pd_series_parquet_dump,
        pd_series_parquet_load,
        classes=[pd.Series],
    )


def _pytorch_present():
    try:
        import torch
    except:
        return False
    return True


if _pytorch_present():
    import torch

    def pytorch_model_dump(model, filename, **kwargs):
        return torch.save(model, filename)

    def pytorch_model_load(filename, **kwargs):
        return torch.load(filename)

    register_serializer(
        'pytorch_model',
        pytorch_model_dump,
        pytorch_model_load,
        classes=[torch.nn.Module],
    )


@t.memoize(key=lambda *args: hash(args))
def partial_serializer(serializer_name, dump_kwargs, load_kwargs):
    s = serializers[serializer_name]
    return Serializer(
        s.name,
        t.partial(s.dump, **dump_kwargs) if dump_kwargs else s.dump,
        t.partial(s.load, **load_kwargs) if load_kwargs else s.load,
        s.content_type,
        s.content_encoding,
        s.content_disposition,
    )


def serializer(artifact):
    return partial_serializer(artifact.serializer, artifact.dump_kwargs, artifact.load_kwargs)


DEFAULT_VALUE_SERIALIZER = serializers['joblib']
DEFAULT_INPUT_SERIALIZER = serializers['joblib']
