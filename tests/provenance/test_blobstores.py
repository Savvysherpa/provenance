import shutil

import hypothesis.strategies as st
import pytest
from hypothesis import given
from strategies import builtin_data

import provenance._commonstore as cs
import provenance.blobstores as bs


def assert_store_basic_ops(store, key, data):
    assert key not in store
    store.put(key, data)
    assert key in store

    if store._on_duplicate_key == 'raise':
        with pytest.raises(cs.KeyExistsError):
            store.put(key, 'new value')

    assert store.get(key) == data
    assert store[key] == data

    store.delete(key)
    assert key not in store

    with pytest.raises(KeyError):
        store.delete(key)

    with pytest.raises(KeyError):
        store.get(key)


hex_alphabet = tuple(map(str, range(0, 10))) + tuple('abcdefABCDEF')
sha1 = st.text(alphabet=hex_alphabet, min_size=40, max_size=40)


@given(sha1, builtin_data)
def test_memory_blobstore(key, obj):
    store = bs.MemoryStore(read=True, write=True, delete=True)
    assert_store_basic_ops(store, key, obj)


@given(sha1, builtin_data)
def test_memory_blobstore_raises(key, obj):
    store = bs.MemoryStore(read=True, write=True, delete=True, on_duplicate_key='raise')
    assert_store_basic_ops(store, key, obj)


@given(sha1, builtin_data)
def test_disk_blobstore(key, obj):
    tmp_dir = '/tmp/prov_diskstore'
    shutil.rmtree(tmp_dir, ignore_errors=True)
    store = bs.DiskStore(tmp_dir, read=True, write=True, delete=True)
    assert_store_basic_ops(store, key, obj)


def test_permissions():
    store = bs.MemoryStore(read=True, write=True, delete=True)
    store.put('a', 1)
    assert store.get('a') == 1
    store.delete('a')

    store = bs.MemoryStore(read=False, write=False, delete=False)
    with pytest.raises(cs.PermissionError):
        store.put('a', 1)

    with pytest.raises(cs.PermissionError):
        store.get('a')

    with pytest.raises(cs.PermissionError):
        store.delete('a')


def test_s3store(s3fs):
    tmp_dir = '/tmp/prov_s3store'
    shutil.rmtree(tmp_dir, ignore_errors=True)
    basepath = 'bucket/prov_test'
    store = bs.S3Store(tmp_dir, basepath, s3fs=s3fs, delete=True)
    key = sha1.example()
    obj = builtin_data.example()

    assert_store_basic_ops(store, key, obj)


def test_sftpstore_import():
    import provenance._config as c

    try:
        import paramiko

        _paramiko = True
    except ImportError:
        _paramiko = False
    try:
        _ = c.BLOBSTORE_TYPES['sftp'](cachedir=None, basepath=None)
        assert _paramiko is True
    except ImportError:
        assert _paramiko is False


def test_chained_storage_with_disk_and_s3_sharing_cachedir(s3fs):
    tmp_dir = '/tmp/prov_shared_store'
    shutil.rmtree(tmp_dir, ignore_errors=True)
    mem_store = bs.MemoryStore(read=True, write=True, delete=True)
    disk_store = bs.DiskStore(tmp_dir, read=True, write=True, delete=True)
    s3_store = bs.S3Store(
        tmp_dir,
        s3fs=s3fs,
        basepath='bucket/prov_test',
        read=True,
        write=True,
        delete=True,
        always_check_remote=True,
    )
    stores = [mem_store, disk_store, s3_store]

    chained_store = bs.ChainedStore(stores)

    key = 'foobar'
    data = {'a': 1, 'b': 2}

    for store in stores:
        assert key not in store
    assert key not in store

    chained_store.put(key, data)
    assert key in store
    for store in stores:
        assert key in store

    assert store.get(key) == data
    assert store[key] == data

    store.delete(key)
    assert key not in store

    with pytest.raises(KeyError):
        store.delete(key)

    with pytest.raises(KeyError):
        store.get(key)


def test_chained_with_readonly():
    read_store = bs.MemoryStore({'foo': 42}, read=True, write=False, delete=False)
    write_store = bs.MemoryStore(read=True, write=True, delete=False)
    stores = [read_store, write_store]
    chained_store = bs.ChainedStore(stores)

    # verify we read from the read-only store
    assert chained_store['foo'] == 42

    # but that it is not written to
    chained_store.put('bar', 55)
    assert 'bar' in chained_store
    assert 'bar' in write_store
    assert 'bar' not in read_store


def test_chained_read_through_write():
    read_store = bs.MemoryStore({'foo': 42}, read=True, write=False)
    store_ahead = bs.MemoryStore(read=True, write=True, read_through_write=True)
    read_through_write_store = bs.MemoryStore(read=True, write=True, read_through_write=True)
    no_read_through_write_store = bs.MemoryStore(read=True, write=True, read_through_write=False)
    stores = [
        no_read_through_write_store,
        read_through_write_store,
        read_store,
        store_ahead,
    ]
    chained_store = bs.ChainedStore(stores)

    assert 'foo' not in read_through_write_store
    assert 'foo' not in no_read_through_write_store
    assert 'foo' not in store_ahead
    # verify we read from the read-only store
    assert chained_store['foo'] == 42

    assert 'foo' in read_through_write_store
    assert 'foo' not in store_ahead
    assert 'foo' not in no_read_through_write_store


def test_chained_writes_may_be_allowed_on_read_throughs_only():
    read_store = bs.MemoryStore({'foo': 42}, read=True, write=False)
    read_through_write_only_store = bs.MemoryStore(read=True, write=False, read_through_write=True)
    write_store = bs.MemoryStore(read=True, write=True, read_through_write=False)
    stores = [write_store, read_through_write_only_store, read_store]
    chained_store = bs.ChainedStore(stores)

    # verify we read from the read-only store
    assert chained_store['foo'] == 42

    assert 'foo' in read_through_write_only_store
    assert 'foo' not in write_store

    chained_store.put('bar', 55)
    assert 'bar' in chained_store
    assert 'bar' not in read_through_write_only_store
    assert 'bar' in write_store
