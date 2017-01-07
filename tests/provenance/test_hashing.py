from hypothesis import given
import provenance as p
from provenance.hashing import hash
import provenance.hashing as h
import numpy as np
import copy
import hypothesis.strategies as st

from strategies import data

@given(data)
def test_shallow_and_deep_copies_hashing(o):
    original_hash = hash(o)
    shallow_copy = copy.copy(o)
    deep_copy = copy.deepcopy(o)
    assert hash(shallow_copy) == original_hash
    assert hash(deep_copy) == original_hash


@given(st.data())
def test_shared_values_hashing(base_data):
    base_data = base_data.draw(data)
    base_copy = lambda: copy.deepcopy(base_data)

    shared_dict = {'a': base_data, 'b': base_data}
    without_sharing_dict = {'a': base_copy(), 'b': base_copy()}

    assert hash(shared_dict) == hash(without_sharing_dict)


    shared_tuple = (base_data, base_data)
    without_sharing_tuple = (base_copy(), base_copy())

    assert hash(shared_tuple) == hash(without_sharing_tuple)


    shared_list = [base_data, base_data]
    without_sharing_list = [base_copy(), base_copy()]

    assert hash(shared_list) == hash(without_sharing_list)

def test_hash_of_contiguous_array_is_the_same_as_noncontiguous():
    a = np.asarray(np.arange(6000).reshape((1000, 2, 3)),
                   order='F')[:, :1, :]
    b = np.ascontiguousarray(a)
    assert hash(a) == hash(b)

def test_hash_of_fortran_array_is_the_same_as_c_array():
    c = np.asarray(np.arange(6000).reshape((1000, 2, 3)),
                   order='C')
    f = np.asarray(np.arange(6000).reshape((1000, 2, 3)),
                   order='F')

    assert hash(c) == hash(f)

def test_hashing_of_functions():

    def foo(a, b):
        return a + b

    assert hash(foo) == hash(foo)

def test_hashing_of_artifacts_and_proxies(repo):

    @p.provenance()
    def load_data():
        return [1, 2, 3]

    original_proxy = load_data()
    original_artifact = original_proxy.artifact
    loaded_artifact = repo.get_by_id(original_artifact.id)
    loaded_proxy = loaded_artifact.proxy()

    # All artifacts should have the same hash
    assert hash(original_artifact) == hash(loaded_artifact)

    # All proxies should have the same hash
    assert hash(original_proxy) == hash(loaded_proxy)

    # All values should have the same hash
    assert hash(original_artifact.value) == hash(loaded_artifact.value)

    # Artifacts and proxies should not have the same hash
    assert hash(original_artifact) != hash(original_proxy)

    # Proxies and values should have the same hash
    assert hash(original_proxy) == hash(original_artifact.value)
