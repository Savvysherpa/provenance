import cloudpickle
import io
from ordered_set import OrderedSet
import pickle

from . import repos as r
from .compatibility import string_type


Pickler = cloudpickle.CloudPickler


class DependencyWalker(Pickler):
    def __init__(self):
        self.stream = io.BytesIO()
        self.dependents = []
        self.branches = []
        protocol = pickle.DEFAULT_PROTOCOL
        Pickler.__init__(self, self.stream, protocol=protocol)

    def save(self, obj):
        if isinstance(obj, r.Artifact):
            self.dependents.append(obj)
        elif r.is_proxy(obj):
            self.dependents.append(obj.artifact)
        else:
            Pickler.save(self, obj)

    def deps(self, artifact):
        self.dependents = []
        self.dump(artifact)
        return self.dependents


def _deps(val):
    return DependencyWalker().deps(val)


def _artifact_branches(artifact):
    objs = _deps(artifact.inputs) + _deps(artifact.value)
    objs.sort(key=lambda a: a.id)
    return objs


def dependencies(artifact_or_id):
    """
    Returns a reversed breadth first search. This guarantees that
    for all artifacts in the list. All of an artifacts dependencies
    will come before it.
    """
    artifact = r.coerce_to_artifact(artifact_or_id)
    visited = []
    queue = [artifact]
    while queue:
        a, *queue = queue

        if a in visited:
            continue

        visited.append(a)
        queue.extend(_artifact_branches(a))

    visited.reverse()
    return visited
