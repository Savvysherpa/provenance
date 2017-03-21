import contextlib
import os
import os.path
import joblib as jl
import toolz as t
import shutil
from s3fs import S3FileSystem
import tempfile
from joblib.disk import mkdirp, rm_subdirs
from .serializers import DEFAULT_VALUE_SERIALIZER, DEFAULT_INPUT_SERIALIZER
from . import _commonstore as cs


class BaseBlobStore(object):
    def __init__(self, read=True, write=True, read_through_write=True,
                 delete=False, on_duplicate_key='skip'):
        self._read = read
        self._write = write
        self._read_through_write = read_through_write
        self._delete = delete
        self._on_duplicate_key = on_duplicate_key

        valid_on_duplicate_keys = {'skip', 'overwrite', 'check_collision', 'raise'}
        if self._on_duplicate_key not in valid_on_duplicate_keys:
            msg = "on_duplicate_key must be one of {}".format(valid_on_duplicate_keys)
            raise RuntimeError(msg)

    def __getitem__(self, id, *args, **kargs):
        return self.get(id, *args, **kargs)

    def put(self, id, value, serializer=DEFAULT_VALUE_SERIALIZER, read_through=False):
        method = getattr(self, '_put_' + self._on_duplicate_key)
        return method(id, value, serializer, read_through)

    def _put_raise(self, id, value, serializer, read_through):
        cs.ensure_put(self, id, read_through)
        self._put_overwrite(id, value, serializer, read_through)

    def _put_skip(self, id, value, serializer, read_through):
        if id not in self:
            self._put_overwrite(id, value, serializer, read_through)

    def _put_check_collision(self, id, value, serializer, read_through):
        cs.ensure_put(self, id, read_through, check_contains=False)
        if id not in self:
            self._put_overwrite(id, value, serializer, read_through)
        else:
            self._check_collision(id, value, serializer)

    # TODO: Right now our only thought is that this can be
    # checked by using an alternate hash, this will require
    # deserializing the old value and running the hash algorithm
    # with an alternate hash
    def _check_collision(self, id, value, serializer):
        raise NotImplemented()


class MemoryStore(BaseBlobStore):
    def __init__(self, values=None, read=True, write=True, read_through_write=True,
                 delete=True, on_duplicate_key='skip'):
        super(MemoryStore, self).__init__(
            read=read, write=write, read_through_write=read_through_write,
            delete=delete, on_duplicate_key=on_duplicate_key)
        if values is None:
            self.values = {}
        else:
            self.values = values

    def __contains__(self, id):
        cs.ensure_contains(self)
        return id in self.values

    def _put_overwrite(self, id, value, serializer, read_through):
        cs.ensure_put(self, id, read_through, check_contains=False)
        self.values[id] = value

    def get(self, id, serialzier=None, **_kargs):
        cs.ensure_read(self)
        cs.ensure_present(self, id)
        return self.values[id]

    def delete(self, id):
        cs.ensure_delete(self, id)
        del self.values[id]


@contextlib.contextmanager
def _temp_filename():
    try:
        temp = tempfile.NamedTemporaryFile('wb', delete=False)
        temp.close()
        yield temp.name
    finally:
        if os.path.isfile(temp.name):
            os.remove(temp.name)


@contextlib.contextmanager
def _atomic_write(filename):
    with _temp_filename() as temp:
        yield temp
        shutil.move(temp, filename)


def _abspath(path):
    return os.path.abspath(os.path.expanduser(path))


class DiskStore(BaseBlobStore):
    def __init__(self, cachedir, read=True, write=True, read_through_write=True,
                 delete=False, on_duplicate_key='skip'):
        super(DiskStore, self).__init__(
            read=read, write=write, read_through_write=read_through_write,
            delete=delete, on_duplicate_key=on_duplicate_key)
        self.cachedir = _abspath(cachedir)
        mkdirp(self.cachedir)

    def _filename(self, id):
        return os.path.join(self.cachedir, id)

    def __contains__(self, id):
        cs.ensure_contains(self)
        return os.path.isfile(self._filename(id))

    def _put_overwrite(self, id, value, serializer, read_through):
        cs.ensure_put(self, id, read_through, check_contains=False)
        with _atomic_write(self._filename(id)) as temp:
            serializer.dump(value, temp)

    def get(self, id, serializer=DEFAULT_VALUE_SERIALIZER, **_kargs):
        cs.ensure_read(self)
        cs.ensure_present(self, id)
        return serializer.load(self._filename(id))

    def delete(self, id):
        cs.ensure_delete(self, id)
        os.remove(self._filename(id))


class S3Store(BaseBlobStore):
    def __init__(self, cachedir, basepath, s3_config=None, s3fs=None,
                 read=True, write=True, read_through_write=True,
                 delete=False, on_duplicate_key='skip', cleanup_cachedir=False):
        super(S3Store, self).__init__(
            read=read, write=write, read_through_write=read_through_write,
            delete=delete, on_duplicate_key=on_duplicate_key)
        if s3fs:
            self.s3fs = s3fs
        elif s3_config is not None:
            self.s3fs = S3FileSystem(**s3_config)
        else:
            raise ValueError("You must provide either s3_config or s3fs for a S3Store")

        self.cachedir = _abspath(cachedir)
        self.basepath = basepath
        self.cleanup_cachedir = cleanup_cachedir
        mkdirp(self.cachedir)

    def __del__(self):
        if self.cleanup_cachedir:
            shutil.rmtree(self.cachedir)

    def _filename(self, id):
        return os.path.join(self.cachedir, id)

    def _path(self, id):
        return os.path.join(self.basepath, id)

    def __contains__(self, id):
        cs.ensure_contains(self)
        # maybe cache bucket listing if not too big?
        return self.s3fs.exists(self._path(id))

    def _put_overwrite(self, id, value, serializer, read_through):
        cs.ensure_put(self, id, read_through, check_contains=False)
        filename = self._filename(id)
        # not already saved by DiskStore?
        if not os.path.isfile(filename):
            with _atomic_write(filename) as temp:
                serializer.dump(value, temp)
        self.s3fs.put(filename, self._path(id))

    def get(self, id, serializer=DEFAULT_VALUE_SERIALIZER, **_kargs):
        cs.ensure_read(self)
        cs.ensure_present(self, id)
        filename = self._filename(id)
        if not os.path.exists(filename):
            with _atomic_write(filename) as temp:
                self.s3fs.get(self._path(id), temp)
        return serializer.load(filename)

    def delete(self, id):
        cs.ensure_delete(self, id)
        self.s3fs.rm(self._path(id))


class ChainedStore(object):
    def __init__(self, stores):
        self.stores = stores

    def __contains__(self, id):
        return cs.chained_contains(self, id)

    def _filename(self, id):
        if id in self:
            stores = [s for s in self.stores
                      if s._read and hasattr(s, '_filename')]
            if stores:
                return stores[0]._filename(id)
            else:
                raise Exception("You do not have a disk-based store setup.")

    def put(self, id, value, serializer=DEFAULT_VALUE_SERIALIZER):
        return cs.chained_put(self, id, value,
                              serializer=serializer)

    def get(self, id, serializer=DEFAULT_VALUE_SERIALIZER, **kargs):
        def get(store, id):
            return store.get(id, serializer=serializer, **kargs)
        return cs.chained_get(self, get, id)

    def __getitem__(self, id, **kargs):
        return self.get(id, **kargs)

    def delete(self, id):
        return cs.chained_delete(self, id)
