
from typing import cast, List, Dict, Union, Any, Optional, Set, Tuple
from zipfile import ZipFile

import re
import os

from filesystem import FileBase, FileReal, DirectoryZip, DirectoryReal, FileZip
from version import VersionRange, Version, BadVersionString


class DependencyFailure(Exception):
    ...


class ModDependency:
    modid: str
    required: bool
    version_reqs: List[VersionRange]

    def __init__(self, modid: str, required: bool, version_range: str):
        self.modid = modid
        self.required = required
        self.version_reqs = VersionRange.fromString(version_range)

    def __str__(self) -> str:
        return ','.join([str(req) for req in self.version_reqs])

    def validateMod(self, mod: 'Mod') -> bool:
        if mod.modid != self.modid:
            return False
        for version_req in self.version_reqs:
            if version_req.contains(mod._version):
                return True
        return False


class Mod:
    filename:       str
    name:           str
    modid:          str
    _version:       Version
    dependencies:   List[ModDependency]
    dependents:     List[ModDependency]
    errors:         List[str]

    # pack:           'ModPack'
    instance_dir:   DirectoryReal
    cohabitants:    Dict[str, 'Mod']

    manifest:       Dict[str, str]
    toml_data:      Dict[str, Any]
    parent:         Optional['Mod']

    # def __init__(self, pack: 'ModPack'):
    def __init__(
                self,
                instance_dir: DirectoryReal,
                cohabitants: Dict[str, 'Mod']
            ):
        self.filename = ''
        self.dependencies = []
        self.dependents = []
        self.errors = []
        self.manifest = {}
        # self.pack = pack
        self.instance_dir = instance_dir
        self.cohabitants = cohabitants
        self.parent = None

    def enable(self) -> None:
        if not os.path.exists(self.filename):
            return
        if self.filename.endswith('.jar.tempdisabled'):
            new_name = self.filename.removesuffix('.tempdisabled')
            FileReal(self.instance_dir, self.filename).rename(new_name)
            self.filename = new_name
        if self.filename and self.filename != '[no file]':
            for dep in self.dependencies:
                if dep.modid in self.cohabitants:
                    self.cohabitants[dep.modid].enable()

    def disable(self) -> None:
        if not os.path.exists(self.filename):
            return
        if self.filename.endswith('.jar'):
            new_name = self.filename + ".tempdisabled"
            FileReal(self.instance_dir, self.filename).rename(new_name)
            self.filename = new_name
        if self.filename and self.filename != '[no file]':
            for dep in self.dependents:
                if dep.modid in self.cohabitants:
                    self.cohabitants[dep.modid].enable()


class DependencyGraph:
    _ALL_GRAPHS:    Dict[str, 'DependencyGraph'] = {}
    _ALL_NODES:     Dict[str, 'Node'] = {}
    _ALL_MODS:      Dict[str, Mod] = {}

    class Node:
        mod_set:    Set[Mod]
        graph:      'DependencyGraph'
        score:      int

        def __init__(self, mod: Mod, graph: 'DependencyGraph'):
            self.mod_set = {mod}
            self.graph = graph
            if mod.modid in DependencyGraph._ALL_NODES:
                raise ValueError(f"modid '{mod.modid}' already has a node")
            self.score = 0
            graph.recalculate_scores()

        def merge(self, other: 'DependencyGraph.Node') -> None:
            self.mod_set.union(other.mod_set)
            for mod in other.mod_set:
                DependencyGraph._ALL_GRAPHS[mod.modid] = self.graph
                DependencyGraph._ALL_NODES[mod.modid] = self
            other.mod_set = set()

        @property
        def dependencies(self) -> List[ModDependency]:
            deps = []
            for mod in self.mod_set:
                deps.extend(mod.dependencies)
            return deps

        @property
        def dependents(self) -> List[ModDependency]:
            deps = []
            for mod in self.mod_set:
                deps.extend(mod.dependents)
            return deps

    nodes: List[Node]

    def __init__(self, mod: Mod):
        self.nodes = []
        self.nodes.append(DependencyGraph.Node(mod, self))

    @property
    def mods(self) -> List[Mod]:
        mods = []
        for node in self.nodes:
            for mod in node.mod_set:
                mods.append(mod)
        return mods

    def recalculate_scores(self) -> None:
        def score_dependencies(node: DependencyGraph.Node) -> None:
            dependency_nodes: Set[DependencyGraph.Node] = set()
            for dep in node.dependencies:
                if dep.modid not in DependencyGraph._ALL_NODES:
                    continue
                if not dep.required:
                    continue
                dep_node = DependencyGraph._ALL_NODES[dep.modid]
                dependency_nodes.add(dep_node)
            for dep_node in dependency_nodes:
                if node.score + 1 > dep_node.score:
                    dep_node.score = node.score + 1
                score_dependencies(dep_node)

        for node in self.nodes:
            if len(node.dependents) == 0:
                node.score = 0
                score_dependencies(node)

    def merge(self, other: 'DependencyGraph') -> None:
        for node in other.nodes:
            for mod in node.mod_set:
                DependencyGraph._ALL_GRAPHS[mod.modid] = self
                DependencyGraph._ALL_NODES[mod.modid] = node
            node.graph = self
            self.nodes.append(node)
        other.nodes = []
        self.recalculate_scores()

    def disable_all(self) -> None:
        for node in self.nodes:
            for mod in node.mod_set:
                mod.disable()

    def enable_all(self) -> None:
        for node in self.nodes:
            for mod in node.mod_set:
                mod.enable()

    def process_graph(self):
        for node in self.nodes:
            for dep in node.dependents:
                invalid_modid = dep.modid in ['minecraft', 'forge']
                not_installed = dep.modid not in self.mods
                if invalid_modid or not dep.required or not_installed:
                    continue
                dep_graph = DependencyGraph._ALL_GRAPHS[dep.modid]
                if dep_graph is not self:
                    self.merge(dep_graph)
                    self.process_graph(dep_graph)
            for dep in node.dependencies:
                invalid_modid = dep.modid in ['minecraft', 'forge']
                not_installed = dep.modid not in self.mods
                if invalid_modid or not dep.required or not_installed:
                    continue
                dep_graph = DependencyGraph._ALL_GRAPHS[dep.modid]
                if dep_graph is not self:
                    self.merge(dep_graph)
                    self.process_graph(dep_graph)

    def unify_circular_node_dependencies(graph: 'DependencyGraph') -> None:
        ...  # TODO

    @staticmethod
    def gen_graphs(mods: List[Mod]) -> List['DependencyGraph']:
        for mod in mods:
            if mod.modid in ['minecraft', 'forge']:
                continue
            graph = DependencyGraph(mod)
            DependencyGraph._ALL_NODES[mod.modid] = graph.nodes[0]
            DependencyGraph._ALL_GRAPHS[mod.modid] = graph

        for mod in mods:
            if mod.modid in ['minecraft', 'forge']:
                continue
            DependencyGraph._ALL_GRAPHS[mod.modid].process_graph()

        graph_list: List[DependencyGraph] = []
        for graph in DependencyGraph._ALL_GRAPHS.values():
            if graph not in graph_list:
                graph.unify_circular_node_dependencies()
                graph_list.append(graph)

        return graph_list
