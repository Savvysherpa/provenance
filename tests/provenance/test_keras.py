import os
import random
import shutil
import tempfile
from copy import copy, deepcopy

import pytest
pytest.importorskip("keras")

import cloudpickle as pickle
import keras.models
import numpy as np
import pandas as pd
import toolz as t

from keras import backend as K
from keras.datasets import mnist
from keras.layers import Dense, Dropout, Input, Lambda
from keras.layers.core import Activation, Dense, Dropout
from keras.models import Model, Sequential, load_model, save_model
from keras.optimizers import RMSprop
from keras.utils import np_utils

import conftest as c
import provenance as p
import provenance.core as pc
import provenance.keras as pk
import provenance.repos as r
import provenance.utils as u
from conftest import artifact_record
from provenance.hashing import hash


@p.provenance(returns_composite=True)
def mnist_data():
    nb_classes = 10
    (X_train, y_train), (X_test, y_test) = mnist.load_data()
    X_train = X_train.reshape(60000, 784)
    X_test = X_test.reshape(10000, 784)
    X_train = X_train.astype('float32')
    X_test = X_test.astype('float32')
    X_train /= 255
    X_test /= 255
    # convert class vectors to binary class matrices
    Y_train = np_utils.to_categorical(y_train, nb_classes)
    Y_test = np_utils.to_categorical(y_test, nb_classes)
    return {
        'X_train': X_train,
        'Y_train': Y_train,
        'X_test': X_test,
        'Y_test': Y_test
    }


# TODO: test with RmsProp obj and evaluate if merged defaults should do a deepcopy
DEFAULT_COMPILE_OPTS = {
    'optimizer': 'rmsprop',
    'loss': 'categorical_crossentropy',
    'metrics': ['accuracy']
}


@p.provenance(merge_defaults=True)
def compile_model(model, compile_opts=DEFAULT_COMPILE_OPTS):
    model_ = pk.copy_model(model)
    model_.compile(**compile_opts)
    return model_


@p.provenance(returns_composite=True)
def fit_model(model,
              x,
              y,
              batch_size=32,
              epochs=1,
              verbose=1,
              callbacks=None,
              validation_split=0.0,
              validation_data=None,
              shuffle=True,
              class_weight=None,
              sample_weight=None):
    model_ = pk.copy_model(model)
    if callbacks is None:
        callbacks = []

    history = model_.fit(
        x,
        y,
        batch_size=batch_size,
        epochs=epochs,
        verbose=verbose,
        callbacks=callbacks,
        validation_split=validation_split,
        validation_data=validation_data,
        shuffle=shuffle,
        class_weight=class_weight,
        sample_weight=sample_weight)
    return {'history': history.history, 'model': model_}


@p.provenance()
def basic_model(units=256, seed=42):
    np.random.seed(seed)
    model = Sequential()
    model.add(Dense(units, input_shape=(784, )))
    model.add(Activation('relu'))
    model.add(Dropout(0.2))
    model.add(Dense(units))
    model.add(Activation('relu'))
    model.add(Dropout(0.2))
    model.add(Dense(10))
    model.add(Activation('softmax'))
    return model


def test_copy_model_roundtripping():
    model = Sequential()
    model.add(Dense(5, input_shape=(5, )))
    model.add(Activation('relu'))
    model.add(Dropout(0.2))
    model.add(Dense(1))
    model.add(Activation('softmax'))

    model2 = pk.copy_model(model)
    assert hash(model) == hash(model2)


def test_same_model_same_hash():
    def simple_model():
        np.random.seed(42)
        model = Sequential()
        model.add(Dense(5, input_shape=(5, )))
        model.add(Activation('relu'))
        model.add(Dropout(0.2))
        model.add(Dense(1))
        model.add(Activation('softmax'))
        return model

    model1 = simple_model()
    model2 = simple_model()

    assert hash(model1) == hash(model2)


def pickle_roundtrip(obj):
    # with tempfile.NamedTemporaryFile('wb') as tf:
    #     pickle.dump(obj, tf)
    #     tf.close()
    #     with open(tf.name, 'rb') as lf:
    #         return pickle.load(lf)

    tf_name = '/tmp/prov_pickle_obj.pkl'
    with open(tf_name, 'wb') as tf:
        pickle.dump(obj, tf)

    with open(tf_name, 'rb') as lf:
        return pickle.load(lf)


def test_pickle_roundtrip():
    def simple_model():
        np.random.seed(42)
        model = Sequential()
        model.add(Dense(5, input_shape=(5, )))
        model.add(Activation('relu'))
        model.add(Dropout(0.2))
        model.add(Dense(1))
        model.add(Activation('softmax'))
        return model

    model = simple_model()
    loaded_model = pickle_roundtrip(model)

    assert hash(model) == hash(loaded_model)


def test_integration_keras_test(dbdiskrepo):

    data = mnist_data()
    model = basic_model()
    compiled_model = compile_model(model)
    compiled_model2 = compile_model(model)
    assert compiled_model2.artifact.id == compiled_model.artifact.id
    assert hash(compiled_model2) == hash(compiled_model)

    fitted_model = fit_model(compiled_model, data['X_train'], data['Y_train'])

    assert model.artifact.id != compiled_model.artifact.id
    assert compiled_model.artifact.id != fitted_model.artifact.id
    assert fitted_model.artifact.value_id == p.hash(fitted_model.artifact.value)

    model2 = basic_model()
    assert model2.artifact.id == model.artifact.id
    assert hash(model2) == hash(model)

    compiled_model3 = compile_model(model2)
    assert compiled_model3.artifact.id == compiled_model.artifact.id

    fitted_model2 = fit_model(compiled_model3, data['X_train'],
                              data['Y_train'])


    assert fitted_model2.artifact.id == fitted_model.artifact.id

    # now see if a model that is modified in place ends up with the same hash
    model3 = basic_model()
    assert hash(model3) == hash(model)
    model3.compile(**DEFAULT_COMPILE_OPTS)
    assert hash(model3) == hash(compiled_model)


def test_reloading_from_disk_has_same_value_id(dbdiskrepo):

    data = mnist_data()
    model = basic_model()
    compiled_model = compile_model(model)
    fitted_model = fit_model(compiled_model, data['X_train'], data['Y_train'])

    K.clear_session()

    reloaded_model = p.load_proxy(fitted_model.artifact.id)

    assert reloaded_model.artifact.value_id == p.hash(reloaded_model.artifact.value)


# this gets to the core of deterministic training by TF (or theano)
# not sure how best to do it and question the value of it so for now
# I am not going to worry about it.
@pytest.mark.xfail(run=False)
def test_consistent_hashing_after_fits(dbdiskrepo):
    data = mnist_data()

    model_a = compile_model(basic_model())
    model_b = compile_model(basic_model())
    assert hash(model_a) == hash(model_b)

    np.random.seed(42)
    model_a.fit(data['X_train'], data['Y_train'], batch_size=32, epochs=1)
    np.random.seed(42)
    model_b.fit(data['X_train'], data['Y_train'], batch_size=32, epochs=1)
    assert hash(model_a) == hash(model_b)




def test_unartifacted_models_can_be_used_as_inputs(dbdiskrepo):
    def basic_model(units=256):
        model = Sequential()
        model.add(Dense(units, input_shape=(784, )))
        model.add(Activation('relu'))
        model.add(Dropout(0.2))
        model.add(Dense(units))
        model.add(Activation('relu'))
        model.add(Dropout(0.2))
        model.add(Dense(10))
        model.add(Activation('softmax'))
        return model

    data = mnist_data()
    model = basic_model()
    compiled_model = compile_model(model)

    inputs = compiled_model.artifact.inputs
    assert hash(inputs['kargs']['model']) == hash(model)


def test_model_config_hash_repr_sequential_models():
    sequential = {
        'class_name':
        'Sequential',
        'config': [{
            'class_name': 'Dense',
            'config': {
                'activation': 'linear',
                'name': 'dense_1',
            }
        }, {
            'class_name': 'Activation',
            'config': {
                'activation': 'relu',
                'name': 'activation_12'
            }
        }]
    }

    expected = {
        'class_name':
        'Sequential',
        'config': [{
            'class_name': 'Dense',
            'config': {
                'activation': 'linear',
                'name': 'dense_AUTOGENERATED',
            }
        }, {
            'class_name': 'Activation',
            'config': {
                'activation': 'relu',
                'name': 'activation_AUTOGENERATED'
            }
        }]
    }

    transformed, original_layer_names = pk.model_config_hash_repr(sequential)

    assert transformed == expected
    assert original_layer_names == ['dense_1', 'activation_12']


def test_model_config_hash_repr_complex_models_and_custom_loss_function():
    model_config = {
        'class_name': 'Model',
        'config': {
            'input_layers': [['input_1', 0, 0], ['input_2', 0, 0]],
            'layers': [{
                'class_name': 'InputLayer',
                'config': {
                    'name': 'input_1',
                    'sparse': False
                },
                'inbound_nodes': [],
                'name': 'input_1'
            }, {
                'class_name': 'InputLayer',
                'config': {
                    'name': 'input_2',
                    'sparse': False
                },
                'inbound_nodes': [],
                'name': 'input_2'
            }, {
                'class_name':
                'Sequential',
                'config': [{
                    'class_name': 'Dense',
                    'config': {
                        'activation': 'relu',
                        'name': 'dense_1',
                    }
                }],
                'inbound_nodes': [[['input_1', 0, 0, {}]],
                                  [['input_2', 0, 0, {}]]],
                'name':
                'sequential_2'
            }, {
                'class_name':
                'Lambda',
                'config': {
                    'arguments': {},
                    'function_type': 'lambda',
                    'name': 'lambda_1'
                },
                'inbound_nodes': [[['sequential_2', 1, 0, {}],
                                   ['sequential_2', 2, 0, {}]]],
                'name':
                'lambda_1'
            }],
            'name':
            'model_1',
            'output_layers': [['lambda_1', 0, 0]]
        }
    }

    expected = {
        'class_name': 'Model',
        'config': {
            'input_layers': [['input_AUTOGENERATED', 0, 0],
                             ['input_AUTOGENERATED', 0, 0]],
            'layers': [{
                'class_name': 'InputLayer',
                'config': {
                    'name': 'input_AUTOGENERATED',
                    'sparse': False
                },
                'inbound_nodes': [],
                'name': 'input_AUTOGENERATED'
            }, {
                'class_name': 'InputLayer',
                'config': {
                    'name': 'input_AUTOGENERATED',
                    'sparse': False
                },
                'inbound_nodes': [],
                'name': 'input_AUTOGENERATED'
            }, {
                'class_name':
                'Sequential',
                'config': [{
                    'class_name': 'Dense',
                    'config': {
                        'activation': 'relu',
                        'name': 'dense_AUTOGENERATED',
                    }
                }],
                'inbound_nodes': [[['input_AUTOGENERATED', 0, 0, {}]],
                                  [['input_AUTOGENERATED', 0, 0, {}]]],
                'name':
                'sequential_AUTOGENERATED'
            }, {
                'class_name':
                'Lambda',
                'config': {
                    'arguments': {},
                    'function_type': 'lambda',
                    'name': 'lambda_AUTOGENERATED'
                },
                'inbound_nodes': [[['sequential_AUTOGENERATED', 1, 0, {}],
                                   ['sequential_AUTOGENERATED', 2, 0, {}]]],
                'name':
                'lambda_AUTOGENERATED'
            }],
            'name':
            'model_AUTOGENERATED',
            'output_layers': [['lambda_AUTOGENERATED', 0, 0]]
        }
    }

    transformed, original_layer_names = pk.model_config_hash_repr(model_config)

    assert transformed == expected
    assert original_layer_names == [
        'model_1', 'input_1', 'input_2', 'sequential_2', 'dense_1', 'lambda_1'
    ]

def euclidean_distance(vects):
    x, y = vects
    return K.sqrt(K.sum(K.square(x - y), axis=1, keepdims=True))

def eucl_dist_output_shape(shapes):
    shape1, shape2 = shapes
    return (shape1[0], 1)

def contrastive_loss(y_true, y_pred):
    '''Contrastive loss from Hadsell-et-al.'06
    http://yann.lecun.com/exdb/publis/pdf/hadsell-chopra-lecun-06.pdf
    '''
    margin = 1
    return K.mean(y_true * K.square(y_pred) + (1 - y_true) * K.square(
        K.maximum(margin - y_pred, 0)))


def test_non_sequential_model_with_custom_loss(dbdiskrepo):

    pk.register_custom_objects({'contrastive_loss': contrastive_loss,
                                'eucl_dist_output_shape': eucl_dist_output_shape,
                                'euclidean_distance': euclidean_distance})

    def create_pairs(x, digit_indices):
        '''Positive and negative pair creation.
        Alternates between positive and negative pairs.
        '''
        pairs = []
        labels = []
        n = min([len(digit_indices[d]) for d in range(10)]) - 1
        for d in range(10):
            for i in range(n):
                z1, z2 = digit_indices[d][i], digit_indices[d][i + 1]
                pairs += [[x[z1], x[z2]]]
                inc = random.randrange(1, 10)
                dn = (d + inc) % 10
                z1, z2 = digit_indices[d][i], digit_indices[dn][i]
                pairs += [[x[z1], x[z2]]]
                labels += [1, 0]
                return np.array(pairs), np.array(labels)

    def create_base_network(input_dim):
        '''Base network to be shared (eq. to feature extraction).
            '''
        seq = Sequential()
        seq.add(Dense(128, input_shape=(input_dim, ), activation='relu'))
        seq.add(Dropout(0.1))
        seq.add(Dense(128, activation='relu'))
        seq.add(Dropout(0.1))
        seq.add(Dense(128, activation='relu'))
        return seq

    def compute_accuracy(predictions, labels):
        '''Compute classification accuracy with a fixed threshold on distances. '''
        return labels[predictions.ravel() < 0.5].mean()

    # the data, shuffled and split between train and test sets
    (x_train, y_train), (x_test, y_test) = mnist.load_data()
    x_train = x_train.reshape(60000, 784)
    x_test = x_test.reshape(10000, 784)
    x_train = x_train.astype('float32')
    x_test = x_test.astype('float32')
    x_train /= 255
    x_test /= 255
    input_dim = 784
    epochs = 1

    # create training+test positive and negative pairs
    digit_indices = [np.where(y_train == i)[0] for i in range(10)]
    tr_pairs, tr_y = create_pairs(x_train, digit_indices)

    digit_indices = [np.where(y_test == i)[0] for i in range(10)]
    te_pairs, te_y = create_pairs(x_test, digit_indices)

    # network definition
    base_network = create_base_network(input_dim)

    input_a = Input(shape=(input_dim, ))
    input_b = Input(shape=(input_dim, ))

    # because we re-use the same instance `base_network`,
    # the weights of the network
    # will be shared across the two branches
    processed_a = base_network(input_a)
    processed_b = base_network(input_b)

    distance = Lambda(
        euclidean_distance,
        output_shape=eucl_dist_output_shape)([processed_a, processed_b])
    model = Model([input_a, input_b], distance)

    # train
    #rms = RMSprop()
    compiled_model = compile_model(model, {'loss': contrastive_loss})
    fited_model = fit_model(
        compiled_model, [tr_pairs[:, 0], tr_pairs[:, 1]],
        tr_y,
        validation_data=([te_pairs[:, 0], te_pairs[:, 1]], te_y),
        batch_size=32,
        epochs=1)
