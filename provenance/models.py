import copy
from datetime import datetime

import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as pg
import sqlalchemy.ext.declarative
import sqlalchemy.orm
from memoized_property import memoized_property

Base = sa.ext.declarative.declarative_base()

SHA1_LENGTH = 40
VALUE_ID_LENGTH = SHA1_LENGTH + 10 # extra 10 for optional file extension info

class Run(Base):
    __tablename__ = 'runs'

    id = sa.Column(pg.VARCHAR(SHA1_LENGTH), primary_key=True)
    hostname = sa.Column(pg.VARCHAR(256))
    info = sa.Column(pg.JSONB)
    created_at = sa.Column(pg.TIMESTAMP, default=datetime.utcnow)
    artifacts = sqlalchemy.orm.relationship("Artifact")

    def __init__(self, info):
        self.id = info['id']
        self.info = info
        self.hostname = info['host']['nodename']
        self.created_at = info['created_at']

    @memoized_property
    def info_with_datetimes(self):
        result = copy.copy(self.info)
        result['created_at'] = self.created_at
        return result

class Artifact(Base):
    __tablename__ = 'artifacts'

    id = sa.Column(pg.VARCHAR(SHA1_LENGTH), primary_key=True)
    value_id = sa.Column(pg.VARCHAR(VALUE_ID_LENGTH))
    run_id = sa.Column(pg.VARCHAR(SHA1_LENGTH), sa.ForeignKey("runs.id"))

    name = sa.Column(pg.VARCHAR(1000))
    version = sa.Column(pg.INTEGER)
    fn_module = sa.Column(pg.VARCHAR(100))
    fn_name = sa.Column(pg.VARCHAR(100))

    composite = sa.Column(pg.BOOLEAN)

    value_id_duration = sa.Column(pg.FLOAT)
    compute_duration = sa.Column(pg.FLOAT)
    hash_duration = sa.Column(pg.FLOAT)

    computed_at = sa.Column(pg.TIMESTAMP)
    added_at = sa.Column(pg.TIMESTAMP, default=datetime.utcnow)

    input_artifact_ids = sa.Column(pg.ARRAY(pg.VARCHAR(SHA1_LENGTH)))
    inputs_json = sa.orm.deferred(sa.Column(pg.JSONB))
    serializer = sa.Column(pg.VARCHAR(128), default='joblib')
    load_kwargs = sa.Column(pg.JSONB)
    dump_kwargs = sa.Column(pg.JSONB)
    custom_fields = sa.Column(pg.JSONB)

    def __init__(self, artifact, inputs_json, run):
        self.id = artifact.id
        self.run = run
        self.run_id = run.id
        self.value_id = artifact.value_id
        self.name = artifact.name
        self.version = artifact.version
        self.fn_module = artifact.fn_module
        self.fn_name = artifact.fn_name
        self.composite = artifact.composite
        self.value_id_duration = artifact.value_id_duration
        self.compute_duration = artifact.compute_duration
        self.hash_duration = artifact.hash_duration
        self.input_artifact_ids = artifact.input_artifact_ids
        self.inputs_json = inputs_json
        self.custom_fields = artifact.custom_fields
        self.computed_at = artifact.computed_at
        self.serializer = artifact.serializer
        self.load_kwargs = artifact.load_kwargs
        self.dump_kwargs = artifact.dump_kwargs

    @memoized_property
    def props(self):
        return {'id': self.id,
                'value_id': self.value_id,
                'name': self.name,
                'version': self.version,
                'fn_module': self.fn_module,
                'fn_name': self.fn_name,
                'composite': self.composite,
                'value_id_duration': self.value_id_duration,
                'compute_duration': self.compute_duration,
                'hash_duration': self.hash_duration,
                'input_artifact_ids': self.input_artifact_ids,
                'serializer': self.serializer,
                'load_kwargs': self.load_kwargs,
                'dump_kwargs': self.dump_kwargs,
                'custom_fields': self.custom_fields,
                'computed_at': self.computed_at}

    def __repr__(self):
        return '<Artifact %r>' % self.id


class ArtifactSet(Base):
    __tablename__ = 'artifact_sets'

    id = sa.Column(pg.INTEGER, primary_key=True)
    set_id = sa.Column(pg.VARCHAR(SHA1_LENGTH))
    labels = sa.Column(pg.JSONB)
    created_at = sa.Column(pg.TIMESTAMP, default=datetime.utcnow)


    def __init__(self, artifact_set):
        self.set_id = artifact_set.id
        labels = artifact_set.labels
        if isinstance(artifact_set.labels, str):
            labels = {'name': artifact_set.labels}
        self.labels = labels
        self.created_at = artifact_set.created_at

    @memoized_property
    def props(self):
        return {'id': self.set_id,
                'labels': self.labels,
                'created_at': self.created_at}

    def __repr__(self):
        return '<ArtifactSet %r, %r>' % (self.set_id, self.labels)


class ArtifactSetMember(Base):
    __tablename__ = 'artifact_set_members'

    set_id = sa.Column(pg.VARCHAR(SHA1_LENGTH), #sa.ForeignKey("artifact_sets.set_id"),
                       primary_key=True)
    artifact_id = sa.Column(pg.VARCHAR(SHA1_LENGTH), #sa.ForeignKey("artifacts.id"),
                            primary_key=True)
