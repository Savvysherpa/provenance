import os
import contextlib
import pytest
import toolz as t
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, event
import sqlalchemy_utils.functions as sql_utils
from provenance.models import Base
import provenance as p
import provenance.core as pc
import hypothesis.strategies as st
import tempfile
import shutil
import provenance.blobstores as bs
import provenance.repos as r



@pytest.fixture(scope='session')
def s3fs():
    import moto
    m = moto.mock_s3()
    m.start()
    import s3fs
    import boto3
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

    @event.listens_for(session, "after_transaction_end")
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


@pytest.fixture(scope='function',
               # params=['memoryrepo'])
                params=['memoryrepo', 'dbrepo-diskstore', 'dbrepo-memorystore', 'chained-memmem'
                ])
def repo(request, db_session):
    disk_store_gen = None
    if request.param == 'memoryrepo':
        repo = r.MemoryRepo(read=True, write=True, delete=True)
    elif request.param == 'dbrepo-diskstore':
        disk_store_gen = disk_store()
        repo = r.DbRepo(db_session, next(disk_store_gen),
                        read=True, write=True, delete=True)
    elif request.param == 'chained-memmem':
        repo = r.ChainedRepo([r.MemoryRepo(read=True, write=True, delete=True),
                              r.MemoryRepo(read=True, write=True, delete=True)])
    else:
        repo = r.DbRepo(db_session,
                        memory_store(),
                        read=True, write=True, delete=True)

    p.set_default_repo(repo)
    yield repo
    p.set_default_repo(None)
    if disk_store_gen:
        next(disk_store_gen, 'ignore')


@pytest.fixture(scope='function', params=['dbrepo-diskstore'])
def dbdiskrepo(request, db_session):
    repo_gen = repo(request, db_session)
    yield next(repo_gen)
    next(repo_gen, 'ignore')

@pytest.fixture(scope='function',
                params=['memoryrepo' 'dbrepo-diskstore', 'dbrepo-memorystore'])
def atomic_repo(request, db_session):
    repo_gen = repo(request, db_session)
    yield next(repo_gen)
    next(repo_gen, 'ignore')


md5 = st.text('0123456789abcdef', min_size=32, max_size=32)
_artifact_record_st = st.fixed_dictionaries({'id': md5, 'value_id': md5})

def artifact_record(**kargs):
    artifact_props = t.merge({k: None for k in  pc.artifact_properties},
                             _artifact_record_st.example(),
                             {'inputs': {'varargs':[1,2,3], 'kargs': {}},
                              'fn_module': 'foo', 'fn_name': 'bar',
                              'value': 55, 'name': 'bar',
                              'version': 0,
                              'serializer': 'joblib',
                              'run_info': pc.run_info()},
                             kargs)
    return pc.ArtifactRecord(**artifact_props)


@pytest.fixture()
def with_check_mutations():
    p.set_check_mutations(True)
    yield True
    p.set_check_mutations(False)
