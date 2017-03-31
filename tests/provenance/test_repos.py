from datetime import datetime

import pandas as pd
import pytest
import sqlalchemy_utils.functions as sql_utils

import provenance as p
import provenance._commonstore as cs
import provenance.blobstores as bs
import provenance.repos as r
from conftest import artifact_record


def test_inputs_json(db_session):
    repo = r.DbRepo(db_session, bs.MemoryStore())
    @p.provenance(version=0, name='initial_data', repo=repo)
    def load_data(filename, timestamp):
        return {'data': [1,2,3], 'timestamp': timestamp}

    @p.provenance(repo=repo)
    def process_data_X(data, process_x_inc, timestamp):
        _data = [i + process_x_inc for i in data['data']]
        return {'data': _data, 'timestamp': timestamp}

    @p.provenance(repo=repo)
    def process_data_Y(data, process_y_inc, timestamp):
        _data = [i + process_y_inc for i in data['data']]
        return {'data': _data, 'timestamp': timestamp}

    @p.provenance(repo=repo)
    def combine_processed_data(filename, inc_x, inc_y, timestamp):
        _data = [a + b for a, b in zip(inc_x['data'], inc_y['data'])]
        return {'data': _data, 'timestamp': timestamp}

    def pipeline(filename, timestamp, process_x_inc, process_y_inc):
        data = load_data(filename, timestamp)
        inc_x = process_data_X(data, process_x_inc, timestamp)
        inc_y = process_data_Y(data, process_y_inc, timestamp)
        res = combine_processed_data(filename, inc_x, inc_y, timestamp)
        return {'data': data, 'inc_x': inc_x, 'inc_y': inc_y, 'res': res}

    now = datetime(2016, 9, 27, 7, 51, 11, 613544)

    expected_inputs_json = {
        "__varargs": [],
        "filename": "foo-bar",
        "timestamp": now,
        "inc_x": {
            "id": "2c33a362ebd51f830d0b245473ab6c1269674259",
            "name": "test_repos.process_data_X",
            "type": "ArtifactProxy"
        },
        "inc_y": {
            "id": "f9b1bb7a8aaf435fbf60b92cd88bf6c46604f702",
            "name": "test_repos.process_data_Y",
            "type": "ArtifactProxy"
        }
    }

    results = pipeline(filename='foo-bar', process_x_inc=5, process_y_inc=10, timestamp=now)
    res = results['res'].artifact
    inputs_json = r._inputs_json(res.inputs)
    assert inputs_json == expected_inputs_json

    results = pipeline(filename='foo-bar', process_x_inc=5, process_y_inc=10, timestamp=now)
    res = results['res'].artifact
    inputs_json = r._inputs_json(res.inputs)
    assert inputs_json == expected_inputs_json


def test_basic_repo_ops(repo):
    artifact = artifact_record()

    assert artifact.id not in repo
    repo.put(artifact)

    assert artifact.id in repo
    assert artifact in repo

    with pytest.raises(cs.KeyExistsError) as e:
        repo.put(artifact)

    assert repo.get_by_id(artifact.id).id == artifact.id
    assert repo[artifact.id].id == artifact.id
    assert repo.get_by_value_id(artifact.value_id).id == artifact.id

    repo.delete(artifact.id)
    assert artifact.id not in repo
    if hasattr(repo, 'blobstore'):
        assert artifact.id not in repo.blobstore
        assert artifact.value_id not in repo.blobstore

    with pytest.raises(KeyError) as e:
        repo.delete(artifact.id)

    with pytest.raises(KeyError) as e:
        repo.get_by_id(artifact.id)

    with pytest.raises(KeyError) as e:
        repo.get_by_value_id(artifact.id)


def test_repo_set_put_and_finding(repo):
    artifact = artifact_record(id='123')
    repo.put(artifact)
    artifact_set = r.ArtifactSet([artifact.id], 'foo')
    repo.put_set(artifact_set)

    assert repo.get_set_by_id(artifact_set.id) == artifact_set
    found_set = repo.get_set_by_name('foo')
    assert found_set.name == 'foo'
    assert found_set.artifact_ids == {'123'}


def test_repo_raises_key_error_when_set_id_not_found(repo):
    with pytest.raises(KeyError) as e:
        repo.get_set_by_id('foo')


def test_repo_raises_key_error_when_set_name_not_found(repo):
    with pytest.raises(KeyError) as e:
        repo.get_set_by_name('foo')


def test_repo_contains_set(repo):
    assert not repo.contains_set('foo')

    artifact = artifact_record(id='123')
    repo.put(artifact)
    artifact_set = r.ArtifactSet([artifact.id], 'foo')

    repo.put_set(artifact_set)
    assert repo.contains_set(artifact_set.id)


def test_repo_delete_set(repo):
    artifact = artifact_record(id='123')
    repo.put(artifact)
    artifact_set = r.ArtifactSet(['123'], 'foo')
    repo.put_set(artifact_set)

    repo.delete_set(artifact_set.id)

    with pytest.raises(KeyError) as e:
        repo.get_set_by_id(artifact_set.id)


def test_permissions(atomic_repo):
    repo = atomic_repo
    artifact = artifact_record()

    repo._write = False
    assert not repo._write

    with pytest.raises(cs.PermissionError) as e:
        repo.put(artifact)
    assert artifact not in repo
    
    repo._write = True
    repo.put(artifact)

    repo._read = False

    with pytest.raises(cs.PermissionError) as e:
        repo.get_by_id(artifact.id)

    with pytest.raises(cs.PermissionError) as e:
        repo.get_by_value_id(artifact.value_id)

    with pytest.raises(cs.PermissionError) as e:
        repo.get_value(artifact.id)

    with pytest.raises(cs.PermissionError) as e:
        repo.get_inputs(artifact)

    with pytest.raises(cs.PermissionError) as e:
        artifact.id in repo


    repo._read = True
    assert repo.get_by_id(artifact.id)
    assert artifact.id in repo

    repo._delete = False
    with pytest.raises(cs.PermissionError) as e:
        repo.delete(artifact.id)


    repo._delete = True
    repo.delete(artifact.id)
    assert artifact.id not in repo


def test_chained_with_readonly():
    read_repo = r.MemoryRepo([artifact_record(id='foo')],
                             read=True, write=False, delete=False)
    write_repo = r.MemoryRepo(read=True, write=True, delete=False)
    repos = [read_repo, write_repo]
    chained = r.ChainedRepo(repos)

    # verify we read from the read-only store
    assert 'foo' in chained

    # but that it is not written to
    record = artifact_record(id='bar', value_id='baz')
    chained.put(record)
    assert 'bar' in chained
    assert 'bar' in write_repo
    assert 'bar' not in read_repo
    assert chained.get_by_value_id(record.value_id).id == record.id
    assert chained.get_by_id(record.id).id == record.id
    assert chained.get_value(record) == record.value


def test_chained_read_through_write():
    foo = artifact_record(id='foo')
    read_repo = r.MemoryRepo([foo], read=True, write=False)
    repo_ahead = r.MemoryRepo(read=True, write=True, read_through_write=True)
    read_through_write_repo = r.MemoryRepo(read=True, write=True, read_through_write=True)
    no_read_through_write_repo = r.MemoryRepo(read=True, write=True, read_through_write=False)
    repos = [no_read_through_write_repo, read_through_write_repo, read_repo, repo_ahead]
    chained_repo = r.ChainedRepo(repos)

    assert 'foo' not in read_through_write_repo
    assert 'foo' not in no_read_through_write_repo
    assert 'foo' not in repo_ahead
    # verify we read from the read-only store
    assert chained_repo['foo'].id == foo.id

    assert 'foo' in read_through_write_repo
    assert 'foo' not in repo_ahead
    assert 'foo' not in no_read_through_write_repo


def test_chained_writes_may_be_allowed_on_read_throughs_only():
    foo = artifact_record(id='foo')
    read_repo = r.MemoryRepo([foo], read=True, write=False)
    read_through_write_only_repo = r.MemoryRepo(read=True, write=False, read_through_write=True)
    write_repo = r.MemoryRepo(read=True, write=True, read_through_write=False)
    repos = [write_repo, read_through_write_only_repo, read_repo]
    chained_repo = r.ChainedRepo(repos)

    # verify we read from the read-only repo
    assert chained_repo['foo'].id == foo.id

    assert 'foo' in read_through_write_only_repo
    assert 'foo' not in write_repo

    bar = artifact_record(id='bar')
    chained_repo.put(bar)
    assert 'bar' in chained_repo
    assert 'bar' not in read_through_write_only_repo
    assert 'bar' in write_repo


def test_db_is_automatically_created_and_migrated(disk_store):
    db_conn_str = 'postgresql://localhost/test_provenance_autocreate'
    if sql_utils.database_exists(db_conn_str):
        sql_utils.drop_database(db_conn_str)


    repo = r.PostgresRepo(db_conn_str, disk_store,
                          read=True, write=True, delete=True,
                          create_db=True)
    p.set_default_repo(repo)

    @p.provenance()
    def calculate(a, b):
        return a + b

    assert sql_utils.database_exists(db_conn_str)

    # make sure it all works
    assert calculate(1, 2) == 3

    p.set_default_repo(None)
    sql_utils.drop_database(db_conn_str)



def test_db_is_automatically_created_and_migrated_with_the_right_schema(disk_store):
    db_conn_str = 'postgresql://localhost/test_provenance_autocreate_schema'
    if sql_utils.database_exists(db_conn_str):
        sql_utils.drop_database(db_conn_str)


    repo = r.PostgresRepo(db_conn_str, disk_store,
                          read=True, write=True, delete=True,
                          create_db=True, schema='foobar')
    p.set_default_repo(repo)

    @p.provenance()
    def calculate(a, b):
        return a + b

    assert calculate(1, 2) == 3

    with repo.session() as s:
        res = pd.read_sql("select * from foobar.artifacts", s.connection())


    repo2 = r.PostgresRepo(db_conn_str, disk_store,
                          read=True, write=True, delete=True,
                           create_db=True, schema='baz')

    p.set_default_repo(repo2)

    assert calculate(5, 5) == 10

    with repo2.session() as s:
        res = pd.read_sql("select * from baz.artifacts", s.connection())

    assert res.iloc[0]['inputs_json'] == {'b': 5 , 'a': 5, '__varargs': []}

    p.set_default_repo(None)
    sql_utils.drop_database(db_conn_str)


def xtest_db_is_automatically_migrated(disk_store):
    db_conn_str = 'postgresql://localhost/test_provenance_automigrate'
    if sql_utils.database_exists(db_conn_str):
        sql_utils.drop_database(db_conn_str)

    sql_utils.create_database(db_conn_str)


    repo = r.PostgresRepo(db_conn_str, disk_store,
                          read=True, write=True, delete=True,
                          create_db=False, upgrade_db=True)
    p.set_default_repo(repo)

    @p.provenance()
    def calculate(a, b):
        return a + b

    # make sure it all works
    assert calculate(1, 2) == 3

    p.set_default_repo(None)
    sql_utils.drop_database(db_conn_str)
