import sys

from . import hashing as h
from . import repos as r


def _save(obj, artifacts):
    if isinstance(obj, r.Artifact):
        artifacts[obj.id] = obj
    if r.is_proxy(obj):
        artifacts[obj.artifact.id] = obj.artifact


class ArtifactHasher(h.Hasher):
    def __init__(self, artifacts=None, hash_name='sha1'):
        if artifacts is None:
            artifacts = {}

        self.artifacts = artifacts
        h.Hasher.__init__(self, hash_name=hash_name)

    def save(self, obj):
        _save(obj, self.artifacts)
        h.Hasher.save(self, obj)

    def hash(self, obj):
        return (h.Hasher.hash(self, obj), self.artifacts.values())


class NumpyArtifactHasher(h.NumpyHasher):
    def __init__(self, artifacts=None, hash_name='sha1', coerce_mmap=True):
        if artifacts is None:
            artifacts = {}

        self.artifacts = artifacts
        h.NumpyHasher.__init__(self, hash_name=hash_name, coerce_mmap=coerce_mmap)

    def save(self, obj):
        _save(obj, self.artifacts)
        h.NumpyHasher.save(self, obj)

    def hash(self, obj):
        return (h.NumpyHasher.hash(self, obj), self.artifacts.values())


def artifact_hasher(*args, **kwargs):
    if 'numpy' in sys.modules:
        return NumpyArtifactHasher(*args, **kwargs)
    else:
        return ArtifactHasher(*args, **kwargs)
