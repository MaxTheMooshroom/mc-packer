
from attrs import define
from tqdm import tqdm
import psutil

from typing import cast, List, Optional
import subprocess
import time
import os

from filesystem import DirectoryReal, FileReal
from mod import Mod


class MinecraftInstance:
    game_dir:           DirectoryReal
    args:               List[str]

    class Stats:
        modids:         List[str]
        succeeded:      bool
        return_code:    int
        crash_log:      str
        memory:         int         # GBs
        boot_time:      int         # seconds

        def __init__(self):
            self.modids = []
            self.succeeded = False
            self.return_code = 0
            self.crash_log = ''
            self.memory = 0
            self.boot_time = 0

    instance:           Optional[subprocess.Popen[bytes]]
    other_instances:    List[psutil.Process]
    stat_history:       List['MinecraftInstance.Stats']

    def __init__(self, game_dir: str, args: List[str]):
        self.game_dir = DirectoryReal(None, game_dir)
        self.args = args
        self.instance = None
        self.other_instances = []
        self.stat_history = []

    def kill(self) -> None:
        if self.instance is not None:
            self.instance = cast(subprocess.Popen[bytes], self.instance)
            if self.instance.poll():
                self.instance.terminate()
            self.instance = None
        for instance in self.other_instances:
            try:  # ran into a race condition ; handler for that
                instance.terminate()
            except psutil.NoSuchProcess as e:
                ...
        self.other_instances = []

    def wait(self, duration: int = 300, mod_count: int = 0) -> None:
        if self.instance is None:
            raise Exception("MinecraftInstance.wait() was called "
                            "with no active instance"
            )

        self.instance = cast(subprocess.Popen[bytes], self.instance)
        stats = self.stat_history[-1]
        start_time = time.time()
        diff = time.time() - start_time

        with tqdm(
                    total=duration,
                    leave=False,
                    desc=f'[{mod_count:03} MODS] Time Remaining'
                ) as pbar:
            while diff >= duration and self.instance.poll() is None:
                time.sleep(1)
                pbar.update(1)

                diff = time.time() - start_time

        stats.boot_time = int(round(diff))

        if self.instance.poll() is None:
            proc = psutil.Process(self.instance.pid)
            stats.memory = proc.memory_info().rss / (1024 ** 3)  # GB

            self.kill()
        else:
            stats.return_code = self.instance.returncode

        if diff >= duration:  # timed out
            stats.return_code = -1

    def test(
                self,
                timeout: int = 300,
                mod_list: List['Mod'] = []
            ) -> 'MinecraftInstance.Stats':
        '''
        Did minecraft fail to boot?
        '''
        pids = []

        # track programs already running
        for process in psutil.process_iter(['pid', 'name', 'cmdline']):
            is_java = 'java' in process.info['name']
            proc_args = process.info['cmdline'] or []
            if not is_java or len(proc_args) == 0:
                continue
            pids.append(process.info['pid'])

        # start minecraft
        self.instance = subprocess.Popen(args=self.args)

        time.sleep(3)

        # find all associated processes for termination later
        for process in psutil.process_iter(['pid', 'name', 'cmdline']):
            is_not_original = process.info['pid'] != self.instance.pid
            is_new = process.info['pid'] not in pids
            is_java = 'java' in process.info['name']

            if is_not_original and is_new and is_java:
                args = process.info['cmdline']
                for i in range(len(args)):
                    if 'minecraft' in args[i]:
                        self.other_instances.append(
                            psutil.Process(process.info['pid'])
                        )
                        break

        logs = self.game_dir.get('logs')
        logs = cast(DirectoryReal, logs)
        crash_reports = self.game_dir.get('crash-reports')
        crash_reports = cast(DirectoryReal, crash_reports)
        known_crashes = [item.name for item in crash_reports.list()]

        stats = MinecraftInstance.Stats()
        self.stat_history.append(stats)
        stats.modids = [mod.modid for mod in mod_list]

        self.wait(timeout, len(mod_list))
        if stats.return_code != 0:
            stats.succeeded = False
            time.sleep(3)  # allow time for updates to process

            # check for new crash report
            for item in crash_reports.list():
                item = cast(FileReal, item)
                if item.name not in known_crashes:
                    stats.crash_log = item.name
                    stats.succeeded = False
            stats.succeeded = True
        else:
            stats.succeeded = True

        return stats

    def testForError(
                self,
                error: str,
                timeout: int = 300,
                mod_list: List['Mod'] = []
            ) -> bool:
        '''
        Did minecraft fail to boot, with a specific error?
        '''
        logs = self.game_dir.get('logs')
        logs = cast(DirectoryReal, logs)
        crash_reports = self.game_dir.get('crash-reports')
        crash_reports = cast(DirectoryReal, crash_reports)

        stats = self.test(timeout, mod_list)
        if not stats.succeeded:
            if stats.crash_log != '':
                crash_report = FileReal(crash_reports, stats.crash_log)
                time.sleep(3)
                all_text = crash_report.read().decode(errors='ignore')
            else:
                all_text = ''
            all_text += "\n" + cast(
                FileReal,
                logs.get('latest.log')
            ).read().decode(errors='ignore')
            all_text += "\n" + cast(
                FileReal,
                logs.get('debug.log')
            ).read().decode(errors='ignore')
            all_text += "\n" + cast(
                FileReal,
                logs.get('latest_stdout.log')
            ).read().decode(errors='ignore')
            self.kill()
            return error in all_text
        return False

    @classmethod
    def findInstance(cls) -> 'MinecraftInstance':
        pids = []

        for process in psutil.process_iter(['pid', 'name', 'cmdline']):
            is_java = 'java' in process.info['name']
            proc_args = process.info['cmdline'] or []
            if not is_java or len(proc_args) == 0:
                continue
            pids.append(process.info['pid'])

        args = []
        game_dir = ''
        while True:
            found = False
            processes = list(psutil.process_iter(['pid', 'name', 'cmdline']))
            processes.reverse()
            for process in processes:
                is_new = process.info['pid'] not in pids
                is_java = 'java' in process.info['name']
                if is_new and is_java:
                    if process.info['cmdline']:
                        args = process.info['cmdline']
                    for i in range(len(args)):
                        if 'minecraft' in args[i]:
                            found = True
                    if not found:
                        break
                    for i in range(len(args)):
                        if args[i] == '--gameDir':
                            game_dir = args[i + 1]

                    if found:
                        process.terminate()
            if found:
                break
            time.sleep(1)
        return cls(game_dir, args)


if __name__ == '__main__':
    minecraft = MinecraftInstance.findInstance()
