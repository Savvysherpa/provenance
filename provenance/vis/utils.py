import graphviz
import toolz as t

from frozendict import frozendict as fd

from ..repos import is_proxy


def elide(obj, length=30):
    table = str.maketrans({"{": "\{", "}": "\}"})
    s = str(obj).translate(table)
    return (s[:length] + '..') if len(s) > length else s


def artifact_id(artifact, length=7):
    return artifact.id[0:7]


def artifact_record(artifact, elide_len=30):
    return "|".join(["<f0>" +  artifact_id(artifact), "<f1>" + elide(artifact.value, elide_len)])


def param_node_id(child_artifact, name, val):
    if is_proxy(val):
        artifact = val.artifact
        return artifact.id
    # hmmm... we could share the inputs to other functions if we wanted to remove the child_artifact.id...
    return "|".join([child_artifact.id, name])


def node(name, label=None, **attrs):
    attrs['type'] = 'node'
    attrs['name'] = name
    attrs['label'] = label
    return fd(attrs)


def edge(tail_name, head_name, **attrs):
    attrs['type'] = 'edge'
    attrs['tail_name'] = tail_name
    attrs['head_name'] = head_name
    return fd(attrs)


def dicts_to_digraph(dicts):
    g = graphviz.Digraph()
    for d in dicts:
        d = dict(d)
        t = d['type']
        del d['type']
        if t == 'node':
            g.node(**d)
        elif t == 'edge':
            g.edge(**d)
    return g


class DigraphDicts(object):
    def __init__(self):
        self.set = set()

    def node(self, name, label=None, **attrs):
        self.set.add(node(name, label, **attrs))
        return self

    def edge(self, tail_name, head_name, **attrs):
        self.set.add(edge(tail_name, head_name, **attrs))
        return self

    def to_dot(self):
        return dicts_to_digraph(self.set)

    def  _repr_svg_(self):
        return self.to_dot()._repr_svg_()


def _viz_artifact(artifact, g):
    function_id = "fn_" + artifact.id
    fn_qalified_name = ".".join([artifact.fn_module, artifact.fn_name])
    fn_name = artifact.fn_name
    fn_params = "{fn}({params})".format(fn=fn_qalified_name,
                                        params=','.join(artifact.inputs['kargs'].keys()))

    g.node(function_id, fn_name, shape="circle", tooltip=fn_params)
    g.edge(function_id, artifact.id)
    g.node(artifact.id, label=artifact_record(artifact, elide_len=15),
           shape="record", tooltip=elide(artifact.value, 50), color='red')

    # ignore varargs for now...
    for name, val in artifact.inputs['kargs'].items():
        arg_node_id = param_node_id(artifact, name, val)
        if is_proxy(val):
            _viz_artifact(val.artifact, g)
            g.edge(val.artifact.id, function_id, label=name)
        else:
            g.node(arg_node_id, label=elide(val), shape='box')
            g.edge(arg_node_id, function_id, label=name)


def lineage_dot(artifact):
    """Walks the lineage of an artifact returning a DigraphDicts object
    that can be turned into a graphviz.Digraph and is automatically rendered
    as SVG in an IPython notebook.
    """
    g = DigraphDicts()
    if is_proxy(artifact):
        artifact = artifact.artifact
    _viz_artifact(artifact, g)
    return g
