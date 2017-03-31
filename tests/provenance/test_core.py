import toolz as t
import pytest
import tempfile
import pandas as pd
import os
import shutil
import provenance as p
import provenance.core as pc
import provenance.repos as r
import provenance.utils as u
from provenance.hashing import hash
from conftest import artifact_record
import conftest as c


def spit(filename, content):
    with open(filename, "w") as f:
        f.write(content)

def test_integration_test(repo):
    @p.provenance(version=0, name='initial_data')
    def load_data(filename):
        return [1, 2]


    @p.provenance(repo=repo, remove=('to_remove',))
    def process_data_A(data, process_a_inc, to_remove):
        return [i + process_a_inc for i in data]

    times_called = 0

    @p.provenance(ignore=('to_ignore',))
    def process_data_B(data, process_b_inc, to_ignore):
        nonlocal times_called
        times_called += 1
        return [i + process_b_inc for i in data]

    @p.provenance(tags=['tag_a'])
    def combine_processed_data(inc_a, inc_b):
        return [a + b for a, b in zip(inc_a, inc_b)]


    def run_pipeline(filename, to_ignore, to_remove):
        data = load_data(filename) # [1, 2]
        inc_a = process_data_A(data, 1, to_remove) # [2, 3]
        inc_b = process_data_B(data, 5, to_ignore) # [6, 7]
        res = combine_processed_data(inc_a, inc_b) # [8, 10]
        return res

    result = run_pipeline('foo-bar.csv', 'something', 'removed')
    artifact = result.artifact
    inc_a_artifact = artifact.inputs['kargs']['inc_a'].artifact
    inc_b_artifact = artifact.inputs['kargs']['inc_b'].artifact

    assert result == [8, 10]

    #check initial wrapping
    assert artifact.value_id == hash([8,10])

    #check for custom_fields and tags in result
    assert artifact.custom_fields == {'tags': ['tag_a']}
    assert artifact.tags == ['tag_a']

    # check that inputs were removed
    assert inc_a_artifact.inputs == {'kargs': {'data': [1, 2], 'process_a_inc': 1},
                                     'varargs': ()}

    # check metadata
    data_artifact = inc_a_artifact.inputs['kargs']['data'].artifact
    assert data_artifact.name == 'initial_data'
    assert data_artifact.version == 0

    # Check caching
    assert times_called == 1
    new_res = run_pipeline('foo-bar.csv', 'something-different', 'removed-again')
    assert new_res == [8, 10]
    assert times_called == 1

    # Check that the dependencies can be returned
    dependencies = p.dependencies(result.artifact.id)
    assert dependencies == [data_artifact,
                            inc_a_artifact,
                            artifact.inputs['kargs']['inc_b'].artifact,
                            artifact]

    # Check that the input_artifact_ids were properly stored
    assert result.artifact.input_artifact_ids == \
            frozenset((inc_a_artifact.id, inc_b_artifact.id))


def test_archived_file_used_in_input(dbdiskrepo):
    repo = dbdiskrepo
    assert p.get_default_repo() is not None
    tmp_dir = tempfile.mkdtemp('prov_integration_archive_test')
    data_filename = os.path.join(tmp_dir, 'data.csv')
    pd.DataFrame({'a': [0, 1, 2], 'b': [10, 11, 12]}).\
        to_csv(data_filename, index=False)

    assert os.path.exists(data_filename)
    archived_file = p.archive_file(data_filename, delete_original=True,
                                   custom_fields={'foo': 'bar'})
    assert not os.path.exists(data_filename)
    assert archived_file.artifact.custom_fields == {'foo': 'bar'}

    @p.provenance()
    def add_col_c_ret_df(filename):
        df = pd.read_csv(str(filename))
        df['c'] = df['a'] + df['b']
        return df

    ret = add_col_c_ret_df(archived_file)
    assert list(ret['c'].values) == [10, 12, 14]

    assert ret.artifact.inputs['kargs']['filename'] == archived_file


def test_output_is_archived_as_file(dbdiskrepo):
    repo = dbdiskrepo
    tmp_dir = tempfile.mkdtemp('prov_integration_archive_test')
    data_filename = os.path.join(tmp_dir, 'data.csv')
    pd.DataFrame({'a': [0,1,2], 'b': [10,11,12]}).\
        to_csv(data_filename, index=False)
    archived_file = p.archive_file(data_filename, delete_original=True)

    @p.provenance(archive_file=True, delete_original_file=True)
    def add_col_c_ret_df(filename):
        df = pd.read_csv(str(filename))
        df['c'] = df['a'] + df['b']
        data_filename = os.path.join(tmp_dir, 'data2.csv')
        df.to_csv(data_filename, index=False)
        return data_filename

    ret_file = add_col_c_ret_df(archived_file)
    ret = pd.read_csv(str(ret_file))
    assert list(ret['c'].values) == [10, 12, 14]


def test_archived_file_becoming_loaded_value_while_persisting_artifact_info(dbdiskrepo):
    tmp_dir = tempfile.mkdtemp('prov_integration_archive_test')
    repo = dbdiskrepo
    data_filename = os.path.join(tmp_dir, 'data.csv')
    pd.DataFrame({'a': [0,1,2], 'b': [10,11,12]}).\
        to_csv(data_filename, index=False)
    archived_file = p.archive_file(data_filename, delete_original=True)

    @p.provenance(archive_file=True, delete_original_file=True)
    def add_col_c_ret_df(df):
        df['c'] = df['a'] + df['b']
        data_filename = os.path.join(tmp_dir, 'data2.csv')
        df.to_csv(data_filename, index=False)
        return data_filename

    read_csv = lambda af: pd.read_csv(str(af))

    df = archived_file.transform_value(read_csv)
    assert df.artifact.id == archived_file.artifact.id
    ret = add_col_c_ret_df(df).transform_value(read_csv)
    assert list(ret['c'].values) == [10, 12, 14]
    ar = ret.artifact
    assert ar.inputs['kargs']['df'].artifact.id == archived_file.artifact.id



def test_archived_file_allows_extensions_to_be_ignored(dbdiskrepo):
    repo = dbdiskrepo
    assert p.get_default_repo() is not None
    tmp_dir = tempfile.mkdtemp('prov_integration_archive_test')
    data_filename = os.path.join(tmp_dir, 'data.csv00')
    pd.DataFrame({'a': [0, 1, 2], 'b': [10, 11, 12]}).\
        to_csv(data_filename, index=False)

    archived_file = p.archive_file(data_filename, delete_original=True,
                                   preserve_ext=False)

    assert not archived_file.artifact.value_id.endswith('.csv')



def test_archived_file_canonicalizes_file_extenstions(dbdiskrepo):
    repo = dbdiskrepo
    assert p.get_default_repo() is not None
    tmp_dir = tempfile.mkdtemp('prov_integration_archive_test')
    data_filename = os.path.join(tmp_dir, 'foo.MPEG')
    spit(data_filename, "blah")

    archived_file = p.archive_file(data_filename, delete_original=True,
                                   preserve_ext=True)

    assert archived_file.artifact.value_id.endswith('.mpg')


def test_archived_file_cache_hits_when_filename_is_different(dbdiskrepo):
    repo = dbdiskrepo
    assert p.get_default_repo() is not None
    tmp_dir = tempfile.mkdtemp('prov_integration_archive_test')
    data_filename = os.path.join(tmp_dir, 'data.csv')
    pd.DataFrame({'a': [0, 1, 2], 'b': [10, 11, 12]}).\
        to_csv(data_filename, index=False)

    data_filename2 = os.path.join(tmp_dir, 'data2.csv')
    shutil.copyfile(data_filename, data_filename2)

    archived_file = p.archive_file(data_filename, delete_original=True)
    assert not os.path.exists(data_filename)
    archived_file2 = p.archive_file(data_filename2, delete_original=True)
    assert not os.path.exists(data_filename2)

    assert archived_file.artifact.id == archived_file2.artifact.id



def test_archived_file_creates_a_new_artifact_when_custom_fields_are_different(dbdiskrepo):
    repo = dbdiskrepo
    assert p.get_default_repo() is not None
    tmp_dir = tempfile.mkdtemp('prov_integration_archive_test')
    data_filename = os.path.join(tmp_dir, 'data.csv')
    pd.DataFrame({'a': [0, 1, 2], 'b': [10, 11, 12]}).\
        to_csv(data_filename, index=False)

    data_filename2 = os.path.join(tmp_dir, 'data2.csv')
    shutil.copyfile(data_filename, data_filename2)

    archived_file = p.archive_file(data_filename, delete_original=True,
                                   custom_fields={'data_source': 'provider one'})
    archived_file2 = p.archive_file(data_filename2, delete_original=True,
                                    custom_fields={'data_source': 'provider two'})

    assert archived_file.artifact.id != archived_file2.artifact.id
    assert archived_file.artifact.value_id == archived_file2.artifact.value_id
    assert archived_file.artifact.custom_fields == {'data_source': 'provider one'}
    assert archived_file2.artifact.custom_fields == {'data_source': 'provider two'}


def test_fn_with_merged_defaults_set_with_provenance_decorator(repo):

    @p.provenance(merge_defaults=True)
    def add(data, adders={'a': 1, 'b': 2}):
        return {k:[i + inc for i in data] for k,inc in adders.items()}


    assert add([1,2,3], adders={'c': 2, 'b': 0}) == \
        {'a': [2, 3, 4],
         'b': [1, 2, 3],
         'c': [3, 4, 5]}


def test_with_merged_defaults_used_on_fn(repo):

    @u.with_merged_defaults()
    def add(data, adders={'a': 1, 'b': 2}):
        return {k:[i + inc for i in data] for k,inc in adders.items()}

    fetch_add = p.provenance()(add)


    assert add([1,2,3], adders={'c': 2, 'b': 0}) == \
        {'a': [2, 3, 4],
         'b': [1, 2, 3],
         'c': [3, 4, 5]}


    assert fetch_add([1,2,3], adders={'c': 2, 'b': 0}) == \
        {'a': [2, 3, 4],
         'b': [1, 2, 3],
         'c': [3, 4, 5]}

def test_that_curried_values_are_preserved_in_the_inputs(repo):

    @t.curry
    @p.provenance()
    def three_sum(a, b=5, c=3):
        return a + b + c

    my_sum = three_sum(b=5, c=3)

    result = my_sum(2)
    assert result == 10
    assert result.artifact.inputs['kargs'] == {'a': 2, 'b': 5, 'c': 3}


def test_serialization_of_lambdas(repo):

    @p.provenance(serializer='cloudpickle')
    def magnifier(x):
        return lambda y: x * y

    doubler = magnifier(2)
    assert doubler(2) == 4

    doubler_fetched = repo.get_by_id(doubler.artifact.id).value
    assert doubler(5) == 10


def test_composite_artifacts(repo):
    @p.provenance(returns_composite=True,
                  serializer={'a': 'cloudpickle'},
                  load_kwargs={'b': {'memmap': True}})
    def load_data():
        return {'a': 1, 'b': 2, 'c': 3}

    results = load_data()

    assert results['a'] == 1
    assert results['b'] == 2
    assert results['c'] == 3

    # Check that individual artifacts where created
    a_artifact = results['a'].artifact
    assert a_artifact.name == 'test_core.load_data_a'
    assert repo.get_by_id(a_artifact.id) == a_artifact

    # Check that the correct serializers is used
    assert a_artifact.serializer == 'cloudpickle'

    # Check that the correct load_kwargs are set
    assert results['b'].artifact.load_kwargs == {'memmap': True}


def test_does_not_allow_argument_mutation(repo):
    @p.provenance()
    def append_3_inc(a):
        a.append(3)
        return [n + 1 for n in a]

    msg = "The test_core.append_3_inc function modified arguments: (a)"
    with pytest.raises(pc.ImpureFunctionError, message=msg):
        result = append_3_inc([1,2])
        assert False


def test_run_info_is_preserved_for_artifacts(repo):

    @p.provenance()
    def foo(a):
        return a + 10

    res = foo(5)
    expected_info = pc.run_info()
    assert res.artifact.run_info == expected_info

    reloaded = repo.get_by_id(res.artifact.id)
    reloaded.run_info == expected_info


def test_adding_custom_info_to_run_info(memory_repo):

    @p.provenance()
    def foo(a):
        return a + 10

    def moar_info(info):
        info['git_ref'] = 'deadbeef'
        return info

    p.set_run_info_fn(moar_info)

    res = foo(5)
    assert res.artifact.run_info['git_ref'] == 'deadbeef'

    p.set_run_info_fn(None)

    res = foo(10)
    assert 'git_ref' not in res.artifact.run_info


def test_set_creation(repo):
    a = artifact_record(id='foo')
    b = artifact_record(id='blah')
    repo.put(a)
    repo.put(b)
    artifact_set = r.create_set([a,b], name='myset')

    assert repo.get_set_by_name('myset') == artifact_set
    assert repo.get_set_by_id(artifact_set.id) == artifact_set


def test_set_renaming(repo):
    a = artifact_record(id='foo')
    b = artifact_record(id='blah')
    repo.put(a)
    repo.put(b)
    named_set = r.create_set([a,b], 'foobar')

    assert repo.get_set_by_name('foobar') == named_set
    renamed_set = r.name_set(named_set, 'baz')

    # we don't delete the old one
    assert repo.get_set_by_name('foobar') == named_set
    assert repo.get_set_by_name('baz') == renamed_set


def test_adding_new_artifact_to_set():
    a = artifact_record(id='foo')
    b = artifact_record(id='blah')
    artifact_set = r.ArtifactSet([a], name='myset')
    updated_set = artifact_set.add(b)
    assert updated_set.name is None

    updated_named_set = artifact_set.add(b, name='myset')
    assert updated_named_set.name == 'myset'


def test_removing_artifact_from_set():
    a = artifact_record(id='foo')
    b = artifact_record(id='blah')
    artifact_set = r.ArtifactSet([a.id, b.id], name='myset')
    updated_set = artifact_set.remove(b)

    assert b not in updated_set
    assert updated_set.name is None

    updated_named_set = artifact_set.remove(b, name='myset')
    assert b not in updated_named_set
    assert updated_named_set.name == 'myset'


def test_set_unions():
    a, b, c, d = [artifact_record(id=l) for l in 'abcd']
    set_one = r.ArtifactSet([a, b], name='set one')
    set_two = r.ArtifactSet([c, d], name='set two')
    set_three = set_one.union(set_two, name='set three')

    assert set_three.name == 'set three'

    assert set_three.artifact_ids == {'a', 'b', 'c', 'd'}
    assert (set_one | set_two).artifact_ids == {'a', 'b', 'c', 'd'}


def test_set_differences():
    a, b, c, d = [artifact_record(id=l) for l in 'abcd']
    set_one = r.ArtifactSet([a, b, c], name='set one')
    set_two = r.ArtifactSet([c, d], name='set two')
    set_three = set_one.difference(set_two, name='set three')


    assert set_three.name == 'set three'
    expected_set = {'a', 'b', 'c'} - {'c', 'd'}
    assert set_three.artifact_ids == expected_set
    # check that the operator works too
    assert (set_one - set_two).artifact_ids == expected_set


def test_set_intersections():
    a, b, c, d = [artifact_record(id=l) for l in 'abcd']
    set_one = r.ArtifactSet([a, b, c], name='set one')
    set_two = r.ArtifactSet([c, d], name='set two')
    set_three = set_one.intersection(set_two, name='set three')


    assert set_three.name == 'set three'

    assert set_three.artifact_ids == {'c'}
    # check that the operator works too
    assert (set_one & set_two).artifact_ids == {'c'}


def test_set_capture(repo):

    @p.provenance()
    def add(a, b):
       return a + b

    @p.provenance()
    def mult(x, y):
       return x * y

    with p.capture_set(name='first'):
        x1 = add(2, 2)
        z1 = mult(x1, 10)

    first_set = repo.get_set_by_name('first')
    assert first_set.artifact_ids == {x1.artifact.id, z1.artifact.id}


    with p.capture_set(name='second'):
        _x = add(2, 2)
        z2 = mult(_x, 20)

    second_set = repo.get_set_by_name('second')
    # note how we check to see if x1 is present!
    assert second_set.artifact_ids == {x1.artifact.id, z2.artifact.id}


def test_set_capture_on_loads(repo):

    @p.provenance()
    def add(a, b):
        return a + b

    @p.provenance()
    def mult(x, y):
        return x * y

    x = add(3, 33)

    with p.capture_set(name='first'):
        x = repo.get_by_id(x.artifact.id).proxy()
        z1 = mult(x, 10)

    first_set = repo.get_set_by_name('first')
    assert first_set.artifact_ids == {x.artifact.id, z1.artifact.id}


def test_set_capture_with_initial_artifacts(repo):

    @p.provenance()
    def add(a, b):
       return a + b

    @p.provenance()
    def mult(x, y):
       return x * y

    x = add(3, 33)

    with p.capture_set(initial_set={x.artifact.id}, name='first'):
        z1 = mult(x, 10)

    first_set = repo.get_set_by_name('first')
    assert first_set.artifact_ids == {x.artifact.id, z1.artifact.id}


def test_provenance_set_decorator(repo):

    @p.provenance()
    def add(a, b):
       return a + b

    @p.provenance()
    def mult(x, y):
       return x * y

    @p.provenance_set(set_name='foobar')
    def my_pipeline(a, b=5, y=10):
        x = add(a, b)
        z = mult(x, y)

    my_set = my_pipeline(2)
    assert my_set.name == 'foobar'
    assert repo.get_set_by_name('foobar').id == my_set.id



def test_provenance_set_decorator_with_provenance(repo):

    @p.provenance()
    def add(a, b):
       return a + b

    @p.provenance()
    def mult(x, y):
       return x * y

    @p.provenance()
    @p.provenance_set(set_name='foobar')
    def my_pipeline(a, b=5, y=10):
        x = add(a, b)
        z = mult(x, y)

    my_set = my_pipeline(2)
    assert my_set.name == 'foobar'
    assert (my_set.artifact.inputs['kargs'] ==
            {'a': 2, 'b':5, 'y': 10})

    assert repo.get_set_by_name('foobar').id == my_set.id


def test_provenance_set_decorator_being_named_with_fn(repo):

    @p.provenance()
    def add(a, b):
       return a + b

    @p.provenance()
    def mult(x, y):
       return x * y

    @p.provenance_set(set_name_fn=lambda a, b, y: 'pipeline_{}_{}'.format(a,b))
    def my_pipeline(a, b=5, y=10):
        x = add(a, b)
        z = mult(x, y)

    my_set = my_pipeline(2)
    assert my_set.name == 'pipeline_2_5'

def test_provenance_set_decorator_being_named_with_fn_used_with_curry(repo):

    @p.provenance()
    def add(a, b):
       return a + b

    @p.provenance()
    def mult(x, y):
       return x * y

    @t.curry
    @p.provenance()
    @p.provenance_set(set_name_fn=lambda a, b, y: 'pipeline_{}_{}_{}'.format(a,b,y))
    def my_pipeline(a, b, y=30):
        x = add(a, b)
        z = mult(x, y)

    pipeline_variant = my_pipeline(10)

    my_set = pipeline_variant(20)
    assert my_set.name == 'pipeline_10_20_30'


def test_lazy_dict():
    lazy_dict = p.lazy_dict({'foo': lambda: 'bar',
                             'baz': lambda: 'qux'})
    initial_hash = hash(lazy_dict)

    # check that keys can be fetched
    assert lazy_dict['foo'] == 'bar'
    assert lazy_dict['baz'] == 'qux'

    # check that the hash remains the same as values are realized
    assert hash(lazy_dict) == initial_hash

    # check that it raises correctly
    with pytest.raises(KeyError):
        lazy_dict['bar']

    del lazy_dict['foo']

    with pytest.raises(KeyError):
        lazy_dict['foo']


def test_lazy_proxy_dict_prevents_creation_with_artifacts_of_same_name(repo):
    foo = repo.put(artifact_record(name='foo', value=42))
    foo2 = repo.put(artifact_record(name='foo', value=100))

    msg = """Only artifacts with distinct names can be used in a lazy_proxy_dict.
Offending names: {'foo': 2}
"""
    with pytest.raises(ValueError, message=msg):
        p.lazy_proxy_dict([foo, foo2])


def test_use_cache_true(repo):
    @p.provenance()
    def increase(x):
        return x + 1

    a = increase(5)
    assert a == 6

    # We expect the values and artifacts to be the same
    b = increase(5)
    assert b == 6
    assert b.artifact.id == a.artifact.id

    # Changing a function definition without changing the version
    # results in a stale cache
    @p.provenance()
    def increase(x):
        return x + 2

    c = increase(5)
    assert c == 6
    assert c.artifact.id == a.artifact.id


def test_use_cache_false(repo):
    @p.provenance(use_cache=False)
    def increase(x):
        return x + 1

    a = increase(5)
    assert a == 6

    # We expect the values and artifacts to be the same because the
    # function hasn't changed
    b = increase(5)
    assert b == 6
    assert b.artifact.id == a.artifact.id

    # We can modify the function, but because we aren't using the caching
    # we shouldn't get stale values
    @p.provenance()
    def increase(x):
        return x + 2

    c = increase(5)
    assert c == 7
    assert c.artifact.id != a.artifact.id


def test_check_mutations(repo, with_check_mutations):
    @p.provenance()
    def load_data():
        return [1, 2, 3]

    @p.provenance()
    def process_data(data):
        return list(map(lambda x: x + 1, data))

    data = load_data()

    # We should be able to process unmutated data
    processed_data = process_data(data)
    assert processed_data == [2, 3, 4]

    # We should not be able to process mutated data
    data[0] = 5
    expected_msg = "Artifact {}, of type {} has been mutated".format(
        data.artifact.id, type(data.artifact.value))
    with pytest.raises(pc.MutatedArtifactValueError, message=expected_msg):
        process_data(data)
