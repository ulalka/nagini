#!/usr/bin/python2
# -*- coding: utf8 -*-
from os.path import join, isdir, basename, exists, abspath
from nagini.loader import load_module, remove_ext
from nagini.builder.wrappers import FlowWrapper
from os import getcwd, listdir, walk, remove
from nagini import BaseFlow, EmbeddedFlow
from nagini.client import AzkabanClient
from copy import deepcopy
import tempfile
import argparse
import inspect
import shutil
import yaml
import sys
import os


def debug(*args):
    print "DEBUG:", " ".join([str(i) for i in args])


def find_py(path):
    for item in [join(dp, f) for dp, dn, fn in walk(path) for f in fn]:
        if item.endswith(".py") and not item.endswith("Launcher.py"):
            if not item.endswith("__init__.py"):
                yield item


class PlainProjectPackage(object):
    base_dir = None
    _clean = False
    zip_path = None

    def __init__(self, project_path):
        self.project_path = project_path
        self.name = basename(abspath(project_path))
        self.tmp_dir = tempfile.mkdtemp(prefix=self.name + '-')

    def build(self, zip_filename=None, config=None):
        self.base_dir = self.tmp_dir
        shutil.copytree(self.project_path, join(self.tmp_dir, self.name))

        if zip_filename:
            self.zip_path = zip_filename
        else:
            _, self.zip_path = tempfile.mkstemp('.zip', self.name + '-')

        shutil.make_archive(
            remove_ext(self.zip_path),
            'zip',
            join(self.base_dir)
        )

    def clear(self):
        if not self._clean:
            if exists(self.tmp_dir):
                shutil.rmtree(self.tmp_dir)
            if self.zip_path and exists(self.zip_path):
                remove(self.zip_path)
            self._clean = True


class ProjectPackage(object):
    base_dir = None
    _clean = False
    zip_path = None

    def __init__(self, project_path):
        self.project_path = project_path
        self.name = basename(abspath(project_path))
        self.tmp_dir = tempfile.mkdtemp(prefix=self.name + "-")
        self.jobs = {}

    def build(self, zip_filename=None, config=None):
        sys.path.insert(0, self.tmp_dir)
        self.base_dir = self.tmp_dir
        shutil.copytree(self.project_path, join(self.tmp_dir, self.name))
        for module_path, item in self._find_flows():
            wrapper = FlowWrapper(item, self)
            wrapper.build()

        if exists(join(self.project_path, "system.properties")):
            shutil.copy(join(self.project_path, "system.properties"),
                        self.base_dir)

        if zip_filename:
            self.zip_path = zip_filename
        else:
            _, self.zip_path = tempfile.mkstemp(".zip", self.name + "-")

        if config:
            config = deepcopy(config)
            config["project"] = self.name
            with open(join(self.base_dir, "config.yml"), "w") as fd:
                yaml.dump(config, fd)

        shutil.make_archive(
            remove_ext(self.zip_path),
            "zip",
            join(self.base_dir)
        )

    def _find_flows(self):
        modules = list(find_py(self.tmp_dir))
        for n, module_path in enumerate(modules):
            self.draw_progress(int(100.0 / len(modules) * (n + 1)))
            if module_path.startswith(self.tmp_dir):
                module_path = module_path.replace(self.tmp_dir, "", 1)
                module_path = module_path.strip("/")

            for name, item in inspect.getmembers(load_module(module_path)):
                if inspect.isclass(item) and issubclass(item, BaseFlow):
                    if not issubclass(item, EmbeddedFlow):
                        yield module_path, item
        print

    def draw_progress(self, progress):
        """Width: 60"""
        if progress == 100:
            bar = ""
            progress = "OK"
        else:
            bar = "[{0:<18}]".format("#" * int(18.0 / 100 * progress))
            progress = "%d%%" % progress

        sys.stdout.write(
            "{name:<30}     {bar:>20}{progress:>5}\r".format(name=self.name,
                                                             bar=bar,
                                                             progress=progress)
        )
        sys.stdout.flush()

    def clear(self):
        if not self._clean:
            if exists(self.tmp_dir):
                shutil.rmtree(self.tmp_dir)
            if self.zip_path and exists(self.zip_path):
                remove(self.zip_path)
            self._clean = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(dest="root", nargs="?", type=str, default=getcwd(),
                        help="Project root dir")
    parser.add_argument("-p", "--plain", dest="plain", default=False,
                        action="store_true",
                        help="Don't build nagini project files")
    parser.add_argument('-a', '--all', dest='all', default=False,
                        action='store_true',
                        help='If set "root" must be directory of project '
                             'directories')
    parser.add_argument('-H', '--host', dest='host', required=True,
                        help='Host to upload projects')
    parser.add_argument('-u', '--user', dest='user', required=True,
                        help='Username to authenticate on server')
    parser.add_argument('-P', '--password', dest='password', required=True,
                        help='Password to authenticate on server')
    args = parser.parse_args()

    config = {
        'server': {
            'host': args.host,
            'username': args.user,
            'password': args.password
        }
    }

    client = AzkabanClient(config["server"]["host"])
    client.login(config["server"]["username"], config["server"]["password"])

    projects = []
    os.environ['NAGINI_BUILDING'] = 'true'
    print 'Inspecting projects:'

    if args.all:
        items = map(lambda p: join(args.root, p), listdir(args.root))
    else:
        items = [args.root]

    for root_path in items:
        if isdir(root_path):
            if args.plain:
                project = PlainProjectPackage(root_path)
            else:
                project = ProjectPackage(root_path)
            project.build(config=config)
            projects.append(project)

    print "Uploading projects:"
    for item in projects:
        sys.stdout.write("{0:<56}".format(item.name))
        sys.stdout.flush()
        client.upload_project_zip(item.name, item.zip_path)
        sys.stdout.write("{0:>4}\n".format("OK"))
        sys.stdout.flush()
        item.clear()

if __name__ == "__main__":
    main()
