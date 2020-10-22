import contextlib
import os
import shutil
import tempfile

import hypothesis.strategies as st
import pytest
import sqlalchemy_utils.functions as sql_utils
import toolz as t
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import provenance as p
import provenance.blobstores as bs
import provenance.core as pc
import provenance.repos as r
from provenance.models import Base


@pytest.fixture(scope='session')
def s3fs():
    import moto

    m = moto.mock_s3()
    m.start()
    import boto3
    import s3fs

    client = boto3.client('s3')
    client.create_bucket(Bucket='bucket')
    fs = s3fs.S3FileSystem(anon=False)
    return fs


@pytest.fixture(scope='session')
def db_conn_str():
    env_conn_str = os.environ.get('DB', None)
    return env_conn_str or 'postgresql://localhost/test_provenance'


### This should be the SQLAlchemy db_conn
@pytest.fixture(scope='session')
def db_engine(db_conn_str):
    if sql_utils.database_exists(db_conn_str):
        sql_utils.drop_database(db_conn_str)

    sql_utils.create_database(db_conn_str)
    engine = create_engine(db_conn_str, json_serializer=r.Encoder().encode)
    Base.metadata.create_all(engine)

    return engine


@pytest.fixture()
def db_session(db_engine):
    connection = db_engine.connect()
    transaction = connection.begin()
    session = sessionmaker()(bind=connection)

    session.begin_nested()

    @event.listens_for(session, 'after_transaction_end')
    def restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.expire_all()
            sess.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@contextlib.contextmanager
def cd(newdir, cleanup=lambda: True):
    prevdir = os.getcwd()
    os.chdir(os.path.expanduser(newdir))
    try:
        yield
    finally:
        os.chdir(prevdir)
        cleanup()


@contextlib.contextmanager
def tempdir():
    dirpath = tempfile.mkdtemp()

    def cleanup():
        shutil.rmtree(dirpath)

    with cd(dirpath, cleanup):
        yield dirpath


@pytest.fixture(scope='function')
def disk_store():
    with tempdir() as dirname:
        yield bs.DiskStore(cachedir=dirname, delete=True)


@pytest.fixture(scope='function')
def memory_store():
    return bs.MemoryStore()


@pytest.fixture(scope='function')
def memory_repo():
    repo = r.MemoryRepo(read=True, write=True, delete=True)
    p.set_default_repo(repo)
    yield repo
    p.set_default_repo(None)


@pytest.fixture(scope='function', params=['memory_store', 'disk_store'])
def blobstore(request, memory_store, disk_store):
    if request.param == 'memory_store':
        store = memory_store
    else:
        store = disk_store
    return store


# there must be a better way, but this is so I can get get two db_session fixtures
db_session_ = db_session


@pytest.fixture(
    scope='function',
    # params=['memoryrepo'])
    params=[
        'memoryrepo',
        'dbrepo-diskstore',
        'dbrepo-memorystore',
        'chained-memmem',
    # 'chained-repo'
    ],
)
def repo(request, db_session):
    # clean old config settings
    r.Config.set_current(r.Config({}, {}, None))
    disk_store_gen = None
    disk_store_gen2 = None
    repo2 = None
    prevdir = os.getcwd()
    if request.param == 'memoryrepo':
        repo = r.MemoryRepo(read=True, write=True, delete=True)
    elif request.param == 'dbrepo-diskstore':
        disk_store_gen = disk_store()
        repo = r.DbRepo(db_session, next(disk_store_gen), read=True, write=True, delete=True)
    elif request.param == 'chained-memmem':
        repo = r.ChainedRepo(
            [
                r.MemoryRepo(read=True, write=True, delete=True),
                r.MemoryRepo(read=True, write=True, delete=True),
            ]
        )
    elif request.param == 'chained-repo':
        disk_store_gen = disk_store()
        disk_store_gen2 = disk_store()
        repo1 = r.DbRepo(db_session, next(disk_store_gen), read=True, write=True, delete=True)
        os.chdir(prevdir)
        repo2 = r.DbRepo(
            'postgresql://localhost/test_provenance',
            next(disk_store_gen2),
            read=True,
            write=True,
            delete=True,
            schema='second_repo',
        )
        repo = r.ChainedRepo([repo1, repo2])
    else:
        repo = r.DbRepo(db_session, memory_store(), read=True, write=True, delete=True)

    p.set_default_repo(repo)
    yield repo
    p.set_default_repo(None)
    if repo2 is not None:
        repo2._db_engine.execute('drop schema second_repo cascade;')

    if disk_store_gen:
        next(disk_store_gen, 'ignore')
    if disk_store_gen2:
        next(disk_store_gen2, 'ignore')


@pytest.fixture(scope='function', params=['dbrepo-diskstore'])
def dbdiskrepo(request, db_session):
    repo_gen = repo(request, db_session)
    yield next(repo_gen)
    next(repo_gen, 'ignore')


another_dbdiskrepo = dbdiskrepo


@pytest.fixture(scope='function', params=['memoryrepo' 'dbrepo-diskstore', 'dbrepo-memorystore'])
def atomic_repo(request, db_session):
    repo_gen = repo(request, db_session)
    yield next(repo_gen)
    next(repo_gen, 'ignore')


md5 = st.text('0123456789abcdef', min_size=32, max_size=32)
_artifact_record_st = st.fixed_dictionaries({'id': md5, 'value_id': md5})


def artifact_record(**kargs):
    artifact_props = t.merge(
        {k: None for k in pc.artifact_properties},
        _artifact_record_st.example(),
        {
            'inputs': {
                'varargs': [1, 2, 3],
                'kargs': {}
            },
            'fn_module': 'foo',
            'fn_name': 'bar',
            'value': 55,
            'name': 'bar',
            'version': 0,
            'serializer': 'joblib',
            'run_info': pc.run_info(),
        },
        kargs,
    )
    return pc.ArtifactRecord(**artifact_props)


@pytest.fixture()
def with_check_mutations():
    p.set_check_mutations(True)
    yield True
    p.set_check_mutations(False)
