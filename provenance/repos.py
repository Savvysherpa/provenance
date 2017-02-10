from collections import namedtuple
import copy
from datetime import datetime
import json
from memoized_property import memoized_property
import sqlalchemy
import sqlalchemy.orm
import toolz as t
import wrapt
import operator as ops
import os
from contextlib import contextmanager
import numpy as np

from .compatibility import string_type
from .hashing import value_repr, hash
from . import models as db
from . import serializers as s
from . import utils
from . import _commonstore as cs


class Registry(object):

    _current = None

    @classmethod
    def current(cls):
        return cls._current

    @classmethod
    def set_current(cls, registry):
        cls._current = registry

    def __init__(self, blobstores, repos, default_repo):
        self.blobstores = blobstores
        self.repos = repos
        self.set_default_repo(default_repo)

    def set_default_repo(self, repo):
        if isinstance(repo, string_type):
            if repo not in self.repos:
                raise Exception("There is no registered repo named '{}'.".format(repo))
            self.default_repo = self.repos[repo]
        else:
            self.default_repo = repo


Registry.set_current(Registry({}, {}, None))


def set_default_repo(repo_or_name):
    Registry.current().set_default_repo(repo_or_name)


def get_default_repo():
    return Registry.current().default_repo


@contextmanager
def using_repo(repo_or_name):
    prev_repo = get_default_repo()
    set_default_repo(repo_or_name)
    try:
        yield
    finally:
        set_default_repo(prev_repo)


def load_artifact(artifact_id):
    return get_default_repo().get_by_id(artifact_id)


def load_proxy(artifact_id):
    return get_default_repo().get_by_id(artifact_id).proxy()


def get_set_by_id(set_id):
    return get_default_repo().get_set_by_id(set_id)


def get_set_by_name(set_name):
    return get_default_repo().get_set_by_name(set_name)


def create_set(artifact_ids, name=None):
    return ArtifactSet(artifact_ids, name).put()


def name_set(artifact_set_or_id, name):
    repo = get_default_repo()
    if isinstance(artifact_set_or_id, ArtifactSet):
        artifact_set = artifact_set_or_id
    else:
        artifact_set = repo.get_set_by_id(artifact_set_or_id)

    return artifact_set.rename(name).put(repo)


def transform_value(proxy_artifact, transformer_fn):
    """
    Transforms the underlying value of the `proxy_artifact` with
    the provided `transformer_fn`. A new ArtifactProxy is returned
    with the transformed value but with the original artifact.

    The motivation behind this function is to allow archived files
    to be loaded into memory and passed around while preserving
    the provenance of the artifact. It could be used in any other
    situation where you want a different representation of an
    artifact value while allowing the provenance to be tracked.

    Care should be taken when using this function however because
    it will prevent you from reproducing exact artifacts from
    the lineage since this transformer_fn will not be tracked.
    """
    transformed = copy.copy(proxy_artifact)
    transformed.__wrapped__ = transformer_fn(transformed)
    return transformed


class Proxy():
    def value_repr(self):
        return value_repr(self.artifact.value)

    def transform_value(self, transformer_fn):
        return transform_value(self, transformer_fn)


class ArtifactProxy(wrapt.ObjectProxy, Proxy):
    def __init__(self, value, artifact):
        super(ArtifactProxy, self).__init__(value)
        self._self_artifact = artifact

    @property
    def artifact(self):
        return self._self_artifact

    def __repr__(self):
        return '<provenance.ArtifactProxy({}) {} >'.\
            format(self.artifact.id, repr(self.__wrapped__))

    def __reduce__(self):
        return (load_proxy , (self.artifact.id,))


class CallableArtifactProxy(wrapt.CallableObjectProxy, Proxy):
    def __init__(self, value, artifact):
        super(CallableArtifactProxy, self).__init__(value)
        self._self_artifact = artifact

    @property
    def artifact(self):
        return self._self_artifact

    def __repr__(self):
        return '<provenance.ArtifactProxy({}) {} >'.\
            format(self.artifact.id, repr(self.__wrapped__))

    def __reduce__(self):
        return (load_proxy, (self.artifact.id,))


def artifact_proxy(value, artifact):
    if callable(value):
        return CallableArtifactProxy(value, artifact)
    return ArtifactProxy(value, artifact)


def is_proxy(obj):
    return (type(obj) == ArtifactProxy or
            type(obj) == CallableArtifactProxy)


class Artifact(object):
    def __init__(self, repo, props, value=None, inputs=None):
        assert ('id' in props), "props must contain 'id'"
        assert ('input_id' in props), "props must contain 'input_id'"
        self.__dict__ = props.copy()

        self.repo = repo

        # TODO: This means that None as an artifact cannot have
        # the value preloaded (which might not be a big deal)
        if value is not None:
            self._value = value
        if inputs is not None:
            self._inputs = inputs

    @memoized_property
    def value(self):
        return self.repo.get_value(self)

    @memoized_property
    def inputs(self):
        return self.repo.get_inputs(self)

    def proxy(self):
        if self.composite:
            value = lazy_dict(t.valmap(lambda a: lambda: a.proxy(), self.value))
        else:
            value = self.value
        return artifact_proxy(value, self)

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.id == other.id
        return NotImplemented

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(('--artifact--', self.id))

    def __repr__(self):
        return '<provenance.Artifact({})>'.format(self.id)

    def __reduce__(self):
        return (load_artifact, (self.id,))


@value_repr.register(Artifact)
def _(artifact):
    return ('artifact', artifact.id)


def _artifact_id(artifact_or_id):
    if isinstance(artifact_or_id, string_type):
        return artifact_or_id
    if hasattr(artifact_or_id, 'id'):
        return artifact_or_id.id
    if hasattr(artifact_or_id, 'artifact'):
        return artifact_or_id.artifact.id
    raise Exception("Unable to coerce into an artifact id: {}".\
                    format(artifact_or_id))


def _artifact_from_record(repo, record):
    if isinstance(record, Artifact):
        return record
    return Artifact(repo, t.dissoc(record._asdict(), 'value', 'inputs'),
                    record.value, record.inputs)


class ArtifactRepository(object):
    def __init__(self, read=True, write=True, read_through_write=True,
                 delete=False):
        self._read = read
        self._write = write
        self._read_through_write = read_through_write
        self._delete = delete

    def __getitem__(self, artifact_id):
        return self.get_by_id(artifact_id)

    def batch_get_by_id(self, artifact_ids):
        cs.ensure_read(self)
        return [self.get_by_id(id) for id in artifact_ids]


def _find_first(pred, seq):
    for i in seq:
        if pred(i):
            return i


class MemoryRepo(ArtifactRepository):
    def __init__(self, artifacts=None,
                 read=True, write=True, read_through_write=True, delete=True):
        super(MemoryRepo, self).__init__(read=read, write=write,
                                         read_through_write=read_through_write,
                                         delete=delete)
        self.artifacts = artifacts if artifacts else []
        self.sets = []

    def __contains__(self, artifact_or_id):
        cs.ensure_contains(self)
        artifact_id = _artifact_id(artifact_or_id)
        if _find_first(lambda a: a.id == artifact_id, self.artifacts):
            return True
        else:
            return False

    def put(self, record, read_through=False):
        artifact_id = _artifact_id(record)
        cs.ensure_put(self, artifact_id, read_through)
        self.artifacts.append(record)
        return _artifact_from_record(self, record)

    def get_by_id(self, artifact_id):
        cs.ensure_read(self)
        record = _find_first(lambda a: a.id == artifact_id, self.artifacts)
        if record:
            return _artifact_from_record(self, record)
        else:
            raise KeyError(artifact_id, self)

    def get_by_input_id(self, input_id):
        cs.ensure_read(self)
        record = _find_first(lambda a: a.input_id == input_id, self.artifacts)
        if record:
            return _artifact_from_record(self, record)
        else:
            raise KeyError(input_id, self)

    def get_value(self, artifact_id, composite=False):
        cs.ensure_read(self)
        return _find_first(lambda a: a.id == artifact_id, self.artifacts).value

    def get_inputs(self, artifact):
        cs.ensure_read(self)
        return _find_first(lambda a: a.input_id == artifact.input_id, self.artifacts).inputs

    def delete(self, artifact_or_id):
        artifact_id = _artifact_id(artifact_or_id)
        cs.ensure_delete(self)
        new_artifacts = list(t.filter(lambda a: a.id != artifact_id,
                                 self.artifacts))
        if len(new_artifacts) == len(self.artifacts):
            raise KeyError(artifact_id, self)
        else:
            self.artifacts = new_artifacts

    def contains_set(self, set_id):
        art_set = _find_first(lambda s: s.id == set_id, self.sets)
        return True if art_set else False

    def get_set_by_id(self, set_id):
        cs.ensure_read(self)
        art_set = _find_first(lambda s: s.id == set_id, self.sets)
        if not art_set:
            raise KeyError(self, set_id)

        return art_set
    def get_set_by_name(self, name):
        cs.ensure_read(self)
        versions = [s for s in self.sets if s.name == name]
        if not versions:
            raise KeyError(name, self)
        return sorted(versions, key=lambda s: s.created_at,
                      reverse=True)[0]

    def put_set(self, artifact_set, read_through=False):
        cs.ensure_write(self, 'put_set')
        self.sets.append(artifact_set)
        return artifact_set

    def delete_set(self, set_id):
        cs.ensure_delete(self, check_contains=False)
        prev_count = len(self.sets)
        self.sets = [s for s in self.sets if s.id != set_id]
        if len(self.sets) == prev_count:
            raise KeyError(set_id, self)




def expand_inputs(inputs):
    def transform(val):
        if isinstance(val, (Artifact)):
            return {'type': 'Artifact', 'id': val.id,
                    'inputs': expand_inputs(val.inputs)}
        elif type(val) in {ArtifactProxy, CallableArtifactProxy}:
            return {'type': 'ArtifactProxy', 'id': val.artifact.id,
                    'inputs': expand_inputs(val.artifact.inputs)}
        else:
            return val

    expanded = t.valmap(transform, inputs['kargs'])
    expanded['__varargs'] = list(t.map(transform, inputs['varargs']))

    return expanded


def _ping_postgres(conn, branch):
    """
    Code taken from example here: http://docs.sqlalchemy.org/en/latest/core/pooling.html#dealing-with-disconnects
    """
    if branch:
        # "branch" refers to a sub-connection of a connection,
        # we don't want to bother pinging on these.
        return

    # turn off "close with result".  This flag is only used with
    # "connectionless" execution, otherwise will be False in any case
    save_should_close_with_result = conn.should_close_with_result
    conn.should_close_with_result = False

    try:
        # run a SELECT 1.   use a core select() so that
        # the SELECT of a scalar value without a table is
        # appropriately formatted for the backend
        conn.scalar(sqlalchemy.select([1]))
    except sqlalchemy.exc.DBAPIError as err:
        # catch SQLAlchemy's DBAPIError, which is a wrapper
        # for the DBAPI's exception.  It includes a .connection_invalidated
        # attribute which specifies if this connection is a "disconnect"
        # condition, which is based on inspection of the original exception
        # by the dialect in use.
        if err.connection_invalidated:
            # run the same SELECT again - the connection will re-validate
            # itself and establish a new connection.  The disconnect detection
            # here also causes the whole connection pool to be invalidated
            # so that all stale connections are discarded.
            conn.scalar(sqlalchemy.select([1]))
        else:
            raise
    finally:
        # restore "close with result"
        conn.should_close_with_result = save_should_close_with_result


def _record_pid(dbapi_connection, connection_record):
    connection_record.info['pid'] = os.getpid()


def _check_pid(dbapi_connection, connection_record, connection_proxy):
    pid = os.getpid()
    if connection_record.info['pid'] != pid:
        connection_record.connection = connection_proxy.connection = None
        raise sqlalchemy.exc.DisconnectionError(
            "Connection record belongs to pid %s, "
            "attempting to check out in pid %s" %
            (connection_record.info['pid'], pid))


def _insert_set_members_sql(artifact_set):
    pairs = [(artifact_set.id, id) for id in artifact_set.artifact_ids]
    return """
INSERT INTO artifact_set_members (set_id, artifact_id)
VALUES
{}
ON CONFLICT DO NOTHING
    """.strip().format(",\n".join(t.map(str,pairs)))


class Encoder(json.JSONEncoder):
    def default(self, val):
        if isinstance(val, (datetime)):
            return str(val)
        elif isinstance(val, np.integer):
            return int(val)
        elif isinstance(val, np.floating):
            return float(val)
        elif isinstance(val, np.bool_):
            return bool(val)
        elif isinstance(val, np.ndarray):
            return val.tolist()
        elif callable(val):
            try:
                return utils.fn_info(val)
            except:
                pass
        else:
            try:
                return super(Encoder, self).default(val)
            except Exception as e:
                print("Could not serialize type: {}".format(type(val)))
                return str(type(val))


class PostgresRepo(ArtifactRepository):
    def __init__(self, db, store,
                 read=True, write=True, read_through_write=True, delete=True):
        super(PostgresRepo, self).__init__(read=read, write=write,
                                           read_through_write=read_through_write,
                                           delete=delete)
        if isinstance(db, string_type):
            self._sessionmaker = self._create_sessionmaker(db)
        else:
            self._session = db

        self.blobstore = store

    @contextmanager
    def session(self):
        if hasattr(self, '_session'):
            close = False
        else:
            self._session = self._sessionmaker()
            close = True

        try:
            yield self._session
        except:
            self._session.rollback()
            raise
        finally:
            if close:
                self._session.close()
                del self._session

    def _create_sessionmaker(self, conn_string):
        db_engine = sqlalchemy.create_engine(conn_string, json_serializer=Encoder().encode)
        sqlalchemy.event.listens_for(db_engine, "engine_connect")(_ping_postgres)
        sqlalchemy.event.listens_for(db_engine, "connect")(_record_pid)
        sqlalchemy.event.listens_for(db_engine, "checkout")(_check_pid)
        return sqlalchemy.orm.sessionmaker(bind=db_engine)

    def __contains__(self, artifact_or_id):
        cs.ensure_contains(self)
        artifact_id = _artifact_id(artifact_or_id)
        with self.session() as s:
            return s.query(db.Artifact).filter(db.Artifact.id == artifact_id).count() > 0

    def put(self, artifact_record, read_through=False):
        with self.session() as session:
            cs.ensure_put(self, artifact_record.id, read_through)
            self.blobstore.put(artifact_record.id, artifact_record.value,
                               s.serializer(artifact_record))
            self.blobstore.put(artifact_record.input_id, artifact_record.inputs,
                               s.DEFAULT_INPUT_SERIALIZER)

            expanded_inputs = expand_inputs(artifact_record.inputs)

            db_artifact = db.Artifact(artifact_record, expanded_inputs)
            session.add(db_artifact)
            session.commit()

            return _artifact_from_record(self, artifact_record)

    def get_by_id(self, artifact_id):
        cs.ensure_read(self)
        with self.session() as session:
            result = session.query(db.Artifact).filter(db.Artifact.id == artifact_id).first()

        if result:
            return Artifact(self, result.props)
        else:
            raise KeyError(artifact_id, self)

    def batch_get_by_id(self, artifact_ids):
        cs.ensure_read(self)
        with self.session() as session:
            results = session.query(db.Artifact).filter(db.Artifact.id.in_(artifact_ids)).all()

        if len(results) == len(artifact_ids):
            return [Artifact(self, result.props) for result in results]
        else:
            ids = set(artifact_ids)
            found = set([a.id for a in results])
            missing = ids - found
            raise KeyError(missing, self)

    def get_by_input_id(self, input_id):
        cs.ensure_read(self)
        with self.session() as session:
            result = session.query(db.Artifact).filter(db.Artifact.input_id == input_id).first()

        if result:
            return Artifact(self, result.props)
        else:
            raise KeyError(input_id, self)

    def get_value(self, artifact):
        cs.ensure_read(self)
        return self.blobstore.get(artifact.id, s.serializer(artifact))

    def get_inputs(self, artifact):
        cs.ensure_read(self)
        return self.blobstore.get(artifact.input_id, s.DEFAULT_INPUT_SERIALIZER)

    def delete(self, artifact_or_id):
        with self.session() as session:
            cs.ensure_delete(self)
            artifact = self.get_by_id(artifact_or_id)
            (session.query(db.Artifact).
             filter(db.Artifact.id == artifact.id).delete())
            self.blobstore.delete(artifact.id)
            self.blobstore.delete(artifact.input_id)
            session.commit()

    def put_set(self, artifact_set, read_through=False):
        with self.session() as session:
            cs.ensure_write(self, 'put_set')
            db_set = db.ArtifactSet(artifact_set)
            session.add(db_set)
            session.execute(_insert_set_members_sql(artifact_set))
            session.commit()

            return artifact_set

    def _db_to_mem_set(self, result):
        with self.session() as session:
            members = (session.query(db.ArtifactSetMember)
                       .filter(db.ArtifactSetMember.set_id == result.set_id)
                       .all())
            props = result.props
            props['artifact_ids'] = [m.artifact_id for m in members]
            return ArtifactSet(**props)

    def contains_set(self, set_id):
        with self.session() as session:
            if (session.query(db.ArtifactSet)
                      .filter(db.ArtifactSet.set_id == set_id).count() > 0):
                return True
            else:
                return False

    def get_set_by_id(self, set_id):
        cs.ensure_read(self)
        with self.session() as session:
            result = (session.query(db.ArtifactSet)
                      .filter(db.ArtifactSet.set_id == set_id).first())

        if result:
            return self._db_to_mem_set(result)
        else:
            raise KeyError(set_id, self)


    def get_set_by_name(self, name):
        cs.ensure_read(self)
        with self.session() as session:
            result = (session.query(db.ArtifactSet)
                      .filter(db.ArtifactSet.name == name)
                      .order_by(db.ArtifactSet.created_at.desc())
                      .first())

        if result:
            return self._db_to_mem_set(result)
        else:
            raise KeyError(name, self)

    def delete_set(self, set_id):
        cs.ensure_delete(self, check_contains=False)
        with self.session() as session:
            num_deleted = (session.query(db.ArtifactSet).
             filter(db.ArtifactSet.set_id == set_id).delete())
            (session.query(db.ArtifactSetMember).
             filter(db.ArtifactSetMember.set_id == set_id).delete())

        if num_deleted == 0:
            raise KeyError(set_id, self)

DbRepo = PostgresRepo


def _put_only_value(store, id, value, **kargs):
    return store.put(value, **kargs)


def _put_set(store, id, value, **kargs):
    return store.put_set(value, **kargs)


def _contains_set(store, id):
    return store.contains_set(id)


def _delete_set(store, id):
    return store.delete_set(id)


class ChainedRepo(ArtifactRepository):
    def __init__(self, repos):
        self.stores = repos

    def __contains__(self, id):
        return cs.chained_contains(self, id)

    def put(self, record):
        return cs.chained_put(self, record.id, record, put=_put_only_value)

    def put_set(self, artifact_set, read_through=False):
        return cs.chained_put(self, None, artifact_set,
                              contains=_contains_set, put=_put_set)

    def get_by_id(self, artifact_id):
        def get(store, id):
            return store.get_by_id(id)
        return cs.chained_get(self, get, artifact_id, put=_put_only_value)

    def contains_set(self, id):
        return cs.chained_contains(self, id, contains=_contains_set)

    def get_set_by_id(self, set_id):
        def get(store, id):
            return store.get_set_by_id(id)
        return cs.chained_get(self, get, set_id, put=_put_set)

    def get_set_by_name(self, set_name):
        def get(store, name):
            return store.get_set_by_name(name)
        return cs.chained_get(self, get, set_name, put=_put_set)

    def delete_set(self, id):
        return cs.chained_delete(self, id,
                                 contains=_contains_set, delete=_delete_set)

    def get_by_input_id(self, input_id):
        def get(store, id):
            return store.get_by_input_id(id)
        return cs.chained_get(self, get, input_id, put=_put_only_value)

    def get_value(self, artifact_id):
        for store in self.stores:
            try:
                return store.get_value(artifact_id)
            except KeyError:
                pass
        raise KeyError(artifact_id, self)

    def delete(self, id):
        return cs.chained_delete(self, id)


### ArtifactSet logic

def _set_op(operator, *sets, name=None):
    new_ids = t.reduce(operator, t.map(lambda s: s.artifact_ids, sets))
    return ArtifactSet(new_ids, name)


set_union = t.partial(_set_op, ops.or_)
set_difference = t.partial(_set_op, ops.sub)
set_intersection = t.partial(_set_op, ops.and_)

artifact_set_properties = ['id', 'artifact_ids', 'created_at', 'name']
class ArtifactSet(namedtuple('ArtifactSet', artifact_set_properties)):

    def __new__(cls, artifact_ids, name=None, created_at=None, id=None):
        artifact_ids = t.map(_artifact_id, artifact_ids)
        ids = frozenset(artifact_ids)
        if id:
            set_id = id
        else:
            set_id = hash(ids)
        created_at = created_at if created_at else datetime.utcnow()
        return super(ArtifactSet, cls).__new__(cls, set_id, ids, created_at, name)

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.id == other.id
        return NotImplemented

    def artifacts_named(self, artifact_name):
        pass
        # return all artifacts found wrapped in list

    def add(self, artifact_or_id, name=None):
        artifact_id = _artifact_id(artifact_or_id)
        return ArtifactSet(self.artifact_ids | {artifact_id}, name)

    def remove(self, artifact_or_id, name=None):
        artifact_id = _artifact_id(artifact_or_id)
        return ArtifactSet(self.artifact_ids - {artifact_id}, name)

    def union(self, *sets, name=None):
        return set_union(self, *sets, name=name)

    def __or__(self, other_set):
        return set_union(self, other_set)

    def difference(self, *sets, name=None, repo=None):
        return set_difference(self, *sets, name=name)

    def __sub__(self, other_set):
        return set_difference(self, other_set)

    def intersection(self, *sets, name=None):
        return set_intersection(self, *sets, name=name)

    def __and__(self, other_set):
        return set_intersection(self, other_set)

    def rename(self, name):
        return self._replace(name=name)

    def put(self, repo=None):
        repo = repo if repo else get_default_repo()
        return repo.put_set(self)

    def proxy_dict(self, repo=None):
        return lazy_proxy_dict(self.artifact_ids)


def save_artifact(f, artifact_ids):

    def wrapped(*args, **kargs):
        artifact = f(*args, **kargs)
        artifact_ids.add(artifact.id)
        return artifact

    return wrapped


class RepoSpy(wrapt.ObjectProxy):
    def __init__(self, repo):
        super(RepoSpy, self).__init__(repo)
        self.artifact_ids = set()
        self.put = save_artifact(repo.put,
                                 self.artifact_ids)
        self.get_by_id = save_artifact(repo.get_by_id,
                                       self.artifact_ids)
        self.get_by_input_id = save_artifact(repo.get_by_input_id,
                                             self.artifact_ids)


@contextmanager
def capture_set(name=None, initial_set=None):
    if initial_set:
        initial = set(t.map(_artifact_id, initial_set))
    else:
        initial = set()

    repo = get_default_repo()
    spy = RepoSpy(repo)
    with using_repo(spy):
        result = []
        yield result
        artifact_ids = spy.artifact_ids | initial
    result.append(ArtifactSet(artifact_ids, name=name).put(repo))


def coerce_to_artifact(artifact_or_id, repo=None):
    repo = repo if repo else get_default_repo()
    if isinstance(artifact_or_id, string_type):
        return repo.get_by_id(artifact_or_id)
    if isinstance(artifact_or_id, Artifact):
        return artifact_or_id
    if is_proxy(artifact_or_id):
        return artifact_or_id.artifact
    raise ValueError('Was unable to coerce object into an Artifact: {}'
                     .format(artifact_or_id))

def coerce_to_artifacts(artifact_or_ids, repo=None):
    repo = repo if repo else get_default_repo()
    #TODO: bring this back when/if batch_get_by_id is added to chained repo
    # if all(isinstance(a, string_type) for a in artifact_or_ids):
    #     return repo.batch_get_by_id(artifact_or_ids)
    return [coerce_to_artifact(a, repo) for a in artifact_or_ids]


class lazy_dict(object):
    def __init__(self, thunks):
        self.thunks = thunks
        self.realized = {}

    def __getstate__(self):
        return self.thunks

    def __setstate__(self, thunks):
        self.__init__(thunks)

    def __getitem__(self, key):
        if key in self.thunks:
            if key not in self.realized:
                self.realized[key] = self.thunks[key]()
            return self.realized[key]
        else:
            raise KeyError(key, self)

    def __setitem__(self, key, value):
        self.thunks[key] = lambda: value
        self.realized[key] = value

    def __delitem__(self, key):
        if key in self.thunks:
            del self.thunks[key]
            if key in self.realized:
                del self.realized[key]
        else:
            KeyError(key, self)

    def __contains__(self, key):
        return key in self.thunks

    def items(self):
        return ((key, self[key]) for key in self.thunks.keys())

    def keys(self):
        return self.thunks.keys()

    def values(self):
        return (self[key] for key in self.thunks.keys())

    def __repr__(self):
        return "lazy_dict({})".format(
            t.merge(t.valmap(lambda _: "...", self.thunks), self.realized))

def lazy_proxy_dict(artifacts_or_ids):
    artifacts = coerce_to_artifacts(artifacts_or_ids)
    names = [a.name for a in artifacts]
    if not t.isdistinct(names):
        multi = t.thread_last(names,
                              t.frequencies,
                              (t.valfilter, lambda x: x > 1))
        raise ValueError("""Only artifacts with distinct names can be used in a lazy_proxy_dict.
-Offending names: {}""".format(multi))
    lambdas = {a.name: (lambda a: lambda: a.proxy())(a) for a in artifacts}

    return lazy_dict(lambdas)
