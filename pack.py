
from tqdm import tqdm
import toml

from typing import cast, List, Dict, Optional, Union, Any
from zipfile import ZipFile
import math
import io
import os
import re

from filesystem import FileBase, FileReal, DirectoryZip, DirectoryReal, FileZip
from minecraft import MinecraftInstance
from mod import Mod, ModDependency, DependencyGraph
from version import Version, BadVersionString


MANIFEST_MAPPING: Dict[str, Union[str, List[str]]] = {
    'file.jarVersion': [
        'Implementation-Version',
        'Specification-Version',
        'Manifest-Version'
    ],
    # temp solution until I learn where to actually get these from
    'forge_version_range': '*',
    'minecraft_version_range': '*',
}


class ModPack:
    directory:  DirectoryReal
    instance:   MinecraftInstance
    mods:       Dict[str, Mod]
    errors:     List[str]

    def __init__(self, instance: MinecraftInstance):
        self.directory = instance.game_dir
        self.mods = {}
        self.errors = []
        self.instance = instance

    def read_jar(
                self,
                filename: str,
                toml_data: Dict[str, Any],
                manifest: str
            ) -> 'Mod':
        instance = Mod(self.directory, self.mods)
        instance.filename = filename

        if manifest != "":
            manifest = manifest.replace('\r\n', '\n')
            while '\n\n' in manifest:
                manifest = manifest.replace('\n\n', '\n')

            for line in manifest.split('\n'):
                parts = line.split(':')
                if len(parts) == 2:
                    instance.manifest[parts[0].strip()] = parts[1].strip()

        def processExternalField(field_raw: str) -> str:
            # checks if string is an external reference `${<var_name>}`
            extern = re.match(r'\${([^}]+)}', field_raw)
            if not extern:
                return field_raw

            field = cast(str, extern.groups(1)[0])
            map = MANIFEST_MAPPING.get(field, None)

            if type(map) is str:
                return map
            elif type(map) is list:
                result: str = ""
                for key in map:

                    result = instance.manifest.get(key, "")
                    if result:
                        break

                if result == "":
                    raise ValueError(
                        f"failed to process field value {field_raw}"
                    )
                return result

            elif map is None:
                return field_raw
            else:
                raise ValueError(f"failed to process field value {field_raw}")

        if "mods" in toml_data and len(toml_data['mods']) > 0:
            mod = toml_data['mods'][0]

            instance.modid = processExternalField(mod['modId'])
            instance._version = Version.fromString(
                processExternalField(mod['version'])
            )
            instance.name = processExternalField(mod["displayName"])
            instance.toml_data = toml_data

            if "dependencies" in toml_data:
                deps = toml_data["dependencies"]
                toml_deps_len = len(deps)
                if toml_deps_len == 0:
                    return instance
                if instance.modid in deps and len(deps[instance.modid]) > 0:
                    for dependency in deps[instance.modid]:
                        try:
                            version_range = processExternalField(
                                dependency['versionRange']
                            )
                            instance.dependencies.append(
                                ModDependency(
                                    dependency["modId"],
                                    dependency['mandatory'],
                                    version_range
                                )
                            )
                        except BadVersionString as e:
                            instance.errors.append(
                                f"'{instance.name}' dependency "
                                f"'{dependency['modId']}' has invalid "
                                f"version range '{dependency['versionRange']}'"
                            )

        return instance

    def process_jar(self, jar: DirectoryZip) -> bool:
        found = False

        for item in [x for x in jar.list() if x.name.endswith('.jar')]:
            with io.BytesIO(
                        cast(ZipFile, jar._zip).read(item.name)
                    ) as nested_jar_bytes:
                with ZipFile(nested_jar_bytes, 'r') as nested_jar:
                    _dir = DirectoryZip(jar, item.name, nested_jar)
                    found = found or self.process_jar(_dir)  # yay recursion

        if jar.has("META-INF/mods.toml"):
            found = True
            raw_text = FileZip("META-INF/mods.toml", jar).read().decode()
            fixed_text = raw_text

            # Python's TOML parser lib apparently cannot handle multiline
            # strings? So convert them to single-line strings before-hand.
            if "'''" in raw_text:
                for match in re.findall(r"('''.*?''')", raw_text, re.DOTALL):
                    replacement = cast(str, match).strip("'")
                    replacement = re.sub(r'\s+', ' ', replacement)
                    replacement = re.sub(r'"', '\'', replacement)
                    fixed_text = fixed_text.replace(match, f'"{replacement}"')

            toml_data = toml.loads(fixed_text)
            manifest = ""
            if jar.has("META-INF/MANIFEST.MF"):
                manifest = FileZip(
                    "META-INF/MANIFEST.MF",
                    jar
                ).read().decode()
            mod = self.read_jar(jar.full_path, toml_data, manifest)
            if hasattr(mod, 'modid'):
                self.mods[mod.modid] = mod
        return found

    def load(self) -> bool:
        mod_dir = cast(DirectoryReal, self.directory.get('mods'))

        # for file in mod_dir.list():
        for file in tqdm(mod_dir.list()):
            if not issubclass(type(file), FileBase):
                continue
            file = cast(FileBase, file)
            if file.name.endswith('.disabled'):
                continue

            with ZipFile(
                        os.path.join(mod_dir.full_path, file.name),
                        'r'
                    ) as jar:
                result = self.process_jar(
                    DirectoryZip(mod_dir, file.name, jar)
                )
                if not result:
                    self.errors.append(
                        f"Failed to locate mod in jar '{file.name}'"
                    )
                    # return False
        return True

    def validateVersions(self, verbose: bool) -> bool:
        for mod in self.mods.values():
            DependencyGraph._ALL_MODS[mod.modid] = mod
            for dep in mod.dependencies:
                if dep.modid in self.mods:
                    dependency = self.mods[dep.modid]
                    if not dep.validateMod(dependency):
                        dependency.errors.append(
                            f"'{mod.modid}' requires '{dep.version_reqs}'"
                        )

                    rdep_mod = ModDependency(mod.modid, False, '*')
                    rdep_mod.version_reqs = dep.version_reqs
                    dependency.dependents.append(rdep_mod)

                else:
                    if dep.required and dep.modid not in []:
                        mod.errors.append(
                            f"Could not find mod '{dep.modid}'! "
                            f"requirements: {dep.version_reqs}"
                        )

        err_num = 0
        for mod in self.mods.values():
            if len(mod.errors) > 0:
                err_num += 1
                if verbose:
                    print(f'{mod.name} ({mod.modid}) {mod._version}:')
                    print(f' ->  [file]: {mod.filename}')
                    for error in mod.errors:
                        print(f' --> {error}')
                    print()

        for error in self.errors:
            err_num += 1
            if verbose:
                print(f' -> {error}')

        return err_num == 0

    def why_depends(self, modid: str, error: bool) -> None:
        if modid not in self.mods:
            print('==================================')
            print(f'why-depends: modid "{modid}" not found!\n')
            return

        mod = self.mods[modid]
        print(f'{mod.name} ({modid}) [{mod._version}]:')
        print(f' -> File: "{mod.filename}"\n')
        print(f' -> Dependencies')
        for dep in mod.dependencies:
            vers_reqs_met = any(
                [range.contains(mod._version) for range in dep.version_reqs]
            )
            if not error or (error and not vers_reqs_met):
                dep_mod = self.mods.get(dep.modid, None)
                dep_name = dep_mod.name if dep_mod else dep.modid
                dep_installed = dep.modid in self.mods
                print(f'   -> name:      {dep_name}')
                print(f'   -> modid:     {dep.modid}')
                print(f'   -> required:  {"yes" if dep.required else "no"}')
                print(f'   -> installed: {"yes" if dep_installed else "no"}')
                print(f'   -> versions:  {dep.version_reqs}')
                print()

        print(f' -> Dependents')
        for dep in mod.dependents:
            vers_reqs_met = any(
                [range.contains(mod._version) for range in dep.version_reqs]
            )
            if not error or (error and not vers_reqs_met):
                dep_mod = self.mods.get(dep.modid, None)
                dep_name = dep_mod.name if dep_mod else dep.modid
                dep_installed = dep.modid in self.mods
                print(f'   -> name:      {dep_name}')
                print(f'   -> modid:     {dep.modid}')
                # print(f'   -> required: {dep.required}')
                print(f'   -> installed: {"yes" if dep_installed else "no"}')
                print(f'   -> versions:  {dep.version_reqs}')
                print()

    def identifyBrokenMods(self, error: str) -> bool:
        graph_list = DependencyGraph.gen_graphs(list(self.mods.values()))

        # sort by number of mods in graph
        graph_list = sorted(
            graph_list,
            key=(lambda x: sum([len(y.mod_set) for y in x.nodes])),
            reverse=False
        )

        # find the first True value in the list
        # number of iterations = int(ceil(log2(len(graph_list))))
        def binaryGraphElimination(_list: List[DependencyGraph]) -> int:
            left = 0
            right = len(_list) - 1

            iter_count = int(math.ceil(math.log2(right + 1)))
            with tqdm(total=iter_count) as pbar:
                while left <= right:
                    mid = (left + right) // 2

                    mods = []
                    for graph in _list[left:mid + 1]:
                        graph.enable_all()
                        mods.extend(graph.mods)
                        for mod in graph.mods:
                            print(f' -> {mod.name} ({mod.modid})')

                    timeout = max(8 * len(mods), 600)
                    result = self.instance.testForError(error, timeout, mods)
                    print('==============================')

                    for graph in _list[left:mid + 1]:
                        graph.disable_all()

                    if result:
                        left = mid + 1
                    else:
                        right = mid - 1

                    pbar.update(1)

            return right + 1

        # find the first True value in the list
        # number of iterations = int(ceil(log2(len(graph.nodes))))
        def binaryNodeElimination(_list: List[DependencyGraph.Node]) -> int:
            left = 0
            right = len(_list) - 1

            iter_count = int(math.ceil(math.log2(right + 1)))
            with tqdm(total=iter_count) as pbar:
                while left <= right:
                    mid = (left + right) // 2

                    mods = set()
                    for node in _list[left:mid + 1]:
                        for mod in node.mod_set:
                            mod.enable()
                            mods.add(mod)

                    timeout = max(8 * len(mods), 600)
                    result = self.instance.testForError(
                        error,
                        timeout,
                        list(mods)
                    )

                    for node in _list[left:mid + 1]:
                        for mod in node.mod_set:
                            mod.disable()

                    if result:
                        left = mid + 1
                    else:
                        right = mid - 1

                    self.instance.kill()
                    pbar.update(1)

            return right + 1

        try:
            for graph in graph_list:
                graph.disable_all()
                graph.recalculate_scores()
                graph.nodes = sorted(graph.nodes, key=(lambda x: x.score))

            if len(graph_list) == 0:
                print('no mods??')
                exit()  # no mods ??

            bad_graph_idx1 = binaryGraphElimination(graph_list)
            bad_graph_idx2 = len(graph_list)

            bad_graph1: Optional[DependencyGraph] = None
            bad_graph2: Optional[DependencyGraph] = None

            if bad_graph_idx1 == len(graph_list):
                raise Exception("The error doesn't seem to exist?")
            bad_graph1 = graph_list[bad_graph_idx1]
            # graph_list.remove(bad_graph1)
            # bad_graph_idx2 = binaryGraphElimination(graph_list)

            if bad_graph_idx2 not in [None, len(graph_list)]:
                bad_graph2 = graph_list[bad_graph_idx2]

                if len(bad_graph1.nodes) > 0:
                    bad_node_idx1 = binaryNodeElimination(bad_graph1.nodes)
                else:
                    raise Exception("The error doesn't seem to exist?")
                bad_mod_idx2 = binaryNodeElimination(bad_graph2.nodes)

                # report_mod_conflict(bad_mod1, bad_mod2)
                # print(f'Bad mods:')
                # print(f' -> {bad_}')

            else:
                bad_node_idx1 = binaryNodeElimination(bad_graph1.nodes)

                if bad_node_idx1 != len(graph_list):
                    # bad_graph1.freeze(bad_mod1)
                    # bad_mod2 = find_bad_mod(bad_graph1)
                    bad_node1 = bad_graph1.nodes[bad_node_idx1]
                    print(f'Found mod(s):')
                    for bad_mod in bad_node1.mod_set:
                        print(f' -> {bad_mod.name} {bad_mod.modid}')
                    # if bad_mod2:
                        # report_mod_conflict(bad_mod1, bad_mod2)
                        # ...
                    # else:
                        # report_bad_mod(bad_mod1)
                        # ...
                else:
                    ...  # no error??
        except KeyboardInterrupt as e:
            print('Received cancellation request')
            self.instance.kill()
            for graph in graph_list:
                graph.enable_all()

        return True

    def print_info(self, unify_threshold=1) -> None:
        graph_list = DependencyGraph.gen_graphs(list(self.mods.values()))

        # sort by number of mods in graph
        graph_list = sorted(
            graph_list,
            key=(lambda x: len(x.mods)),
            reverse=False
        )

        # merge tiny graphs into a single node
        # tiny_graphs = [x for x in graph_list if len(x.mods) <= unify_threshold]
        # for graph in tiny_graphs[1:]:
        #     graph_list.remove(graph)
        #     tiny_graphs[0].merge(graph)
        # tiny_graphs = [tiny_graphs[0]]

        for graph in graph_list:
            graph.disable_all()

        for i, graph in (list(enumerate(graph_list))):
            graph.enable_all()
            mod_list = graph.mods

            try:
                stats = self.instance.test(mod_list=mod_list)
            except KeyboardInterrupt as e:
                print('\nReceived cancellation request')
                self.instance.kill()
                for graph in graph_list:
                    graph.enable_all()
                    exit(0)

            print(f'[GRAPH {i:03}]:')
            print(f' ->  Succeeded: {stats.succeeded}')

            if stats.succeeded:
                print(f' ->  Mod Count:  {len(mod_list):03}')
                print(f' ->  Boot Time:  {stats.boot_time:03} Seconds')
                print(f' ->  Memory Use:  {stats.memory:02} GB')

            else:
                print(f' ->  Crash log: "{stats.crash_log}"')

            for mod in mod_list:
                print(f' --> {mod.name} ({mod.modid})')

            print('\n===============================')
            graph.disable_all()
        print(f'{len(graph_list):03} GRAPHS')

        for i, graph in enumerate(graph_list):
            # dotfile = FileReal(self.directory, f'graph_{i}.dot')

            contents = ""
            # TODO: Generate dotfile from graph
            for node in graph.nodes:
                ...

            # dotfile.write(contents.encode())

            # TODO: Generate image from dotfile
            ...
