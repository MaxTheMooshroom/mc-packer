
from zipfile import ZipFile
from typing import cast
import argparse
import os

from filesystem import DirectoryReal, DirectoryZip, FileReal, FileZip
from version import VersionRange, Version, VersionRangePart
from minecraft import MinecraftInstance
from pack import ModPack, Mod


def validate_gamedir(args: argparse.Namespace) -> MinecraftInstance:
    requested_errors = args.subcommand == "find-error"
    gamedir_provided = not hasattr(args, 'game_dir') or not args.game_dir
    if requested_errors or gamedir_provided:
        try:
            print('Please start your modded minecraft instance. Listening....')
            minecraft = MinecraftInstance.findInstance()
        except KeyboardInterrupt as e:
            print('Cancelled.')
            exit(0)
    else:
        minecraft = MinecraftInstance(args.game_dir, [])

    if not os.path.isdir(minecraft.game_dir.full_path):
        print(f'invalid instance directory "{minecraft.game_dir.full_path}"')
        exit(255)

    os.chdir(minecraft.game_dir.full_path)

    print(f'Found profile at "{minecraft.game_dir.full_path}"')
    return minecraft


def clean(pack: ModPack) -> None:
    mod_dir = cast(DirectoryReal, pack.instance.game_dir.get('mods'))
    for item in mod_dir.list():
        if type(item) is DirectoryReal:
            continue
        item = cast(FileReal, item)
        if item.name.endswith('.tempdisabled'):
            item.rename(item.name.removesuffix('.tempdisabled'))


def load(args: argparse.Namespace, pack: ModPack) -> None:
    print('LOADING PACK')
    if not pack.load():
        return
    if args.versions:
        for modid, version in [
                    vers.split('=') for vers in args.versions.split(',')
                ]:
            if modid not in pack.mods:
                mod = Mod(pack.directory, pack.mods)
                mod._version = Version.fromString(version)
                mod.filename = '[no file]'
                mod.name = modid
                mod.modid = modid
                pack.mods[modid] = mod
            else:
                pack.mods[modid]._version = Version.fromString(version)

    if args.lies:
        for modid in args.lies.split(','):
            if modid not in pack.mods:
                continue
            mod = pack.mods[modid]
            for dep in mod.dependencies:
                if dep.modid in pack.mods:
                    range_part = VersionRangePart(
                        pack.mods[dep.modid]._version,
                        True
                    )
                    dep.version_reqs = [VersionRange(range_part, range_part)]


def validate(pack: ModPack, verbose: bool) -> bool:
    print('VALIDATING PACK')
    validation = pack.validateVersions(verbose)
    if verbose and validation:
        print(' -> [PASS]')
        return True
    else:
        return False


def manage(args: argparse.Namespace, pack: ModPack) -> None:
    if args.manage == 'enable-all':
        mod_dir = cast(DirectoryReal, pack.instance.game_dir.get('mods'))
        for item in mod_dir.list():
            if type(item) is DirectoryReal:
                continue
            item = cast(FileReal, item)
            if item.name.endswith('.disabled'):
                item.rename(item.name.removesuffix('.disabled'))
            elif item.name.endswith('.tempdisabled'):
                item.rename(item.name.removesuffix('.tempdisabled'))

    elif args.manage == 'disable-all':
        mod_dir = cast(DirectoryReal, pack.instance.game_dir.get('mods'))
        for item in mod_dir.list():
            if type(item) is DirectoryReal:
                continue

            item = cast(FileReal, item)
            if item.name.endswith('.jar'):
                new_name = item.name + '.disabled'
                item.rename(new_name)

            elif item.name.endswith('.tempdisabled'):
                new_name = item.name.removesuffix('.tempdisabled')
                new_name = new_name + '.disabled'
                item.rename(new_name)


def main(args: argparse.Namespace):
    minecraft = validate_gamedir(args)

    pack = ModPack(minecraft)

    if args.subcommand == 'clean':
        clean(pack)
        return

    load(args, pack)

    validation_requested = args.subcommand == "validate"
    if not validate(pack, validation_requested) and validation_requested:
        return

    if args.subcommand == 'why-depends':
        pack.why_depends(args.modid, args.why_errors)

    elif args.subcommand == 'find-error':
        print('TESTING PACK')
        pack.identifyBrokenMods(args.error)

    elif args.subcommand == 'mods':
        print('MANAGING MODS')
        manage(args, pack)

    elif args.subcommand == 'mod-info':
        pack.print_info()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--override-versions',
        dest='versions',
        type=str,
        help='<modid>=<version>[,<modid>=<version>[,...]]'
    )
    parser.add_argument(
        '--lie-depends',
        dest='lies',
        help='lie to the provided mods so they think requirements are met. '
             'eg: `<modid>[,<modid>[,...]]`'
    )
    # ---------------
    subparsers = parser.add_subparsers(
        dest='subcommand',
        required=True
    )
    # ---------------
    validate_parser = subparsers.add_parser(
        'validate',
        help='validate dependendies in the pack (10-second runtime)'
    )
    validate_parser.add_argument(
        '--profile-dir',
        dest='game_dir',
        help='The folder location of the install. '
             'Will auto-detect if not provided.'
    )
    # ---------------
    mods_parser = subparsers.add_parser(
        'mods',
        help='sub-subcommands for managing your mods'
    )
    mods_parser.add_argument(
        'manage',
        choices=['enable-all', 'disable-all'],
        help='What to do with your mods',
    )
    # ---------------
    why_depends_parser = subparsers.add_parser(
        'why-depends',
        help='show dependencies of the provided modid (10-second runtime)'
    )
    why_depends_parser.add_argument(
        '--profile-dir',
        dest='game_dir',
        help='The folder location of the install. '
             'Will auto-detect if not provided.'
    )
    why_depends_parser.add_argument(
        '--errors',
        action='store_true',
        help='only print version mismatches',
        dest='why_errors'
    )
    why_depends_parser.add_argument(
        'modid',
        type=str,
        help="the modid to check"
    )
    # ---------------
    find_error_parser = subparsers.add_parser(
        'find-error',
        help='intelligently enable/disable mods until the mod causing the '
             'provided error is found. (very long runtime)'
        )
    find_error_parser.add_argument(
        'error',
        type=str,
        help='the error to solve for'
    )
    # ---------------
    clean_parser = subparsers.add_parser(
        'clean',
        help='clean up any previous actions, usually after a failure'
    )
    clean_parser.add_argument(
        '--profile-dir',
        dest='game_dir',
        help='The folder location of the install. '
             'Will auto-detect if not provided.'
    )
    # ---------------
    mod_info_parser = subparsers.add_parser(
        'mod-info',
        help='Print data associated with each dependency graph'
    )
    # ---------------
    args = parser.parse_args()
    main(args)
