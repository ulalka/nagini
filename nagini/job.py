# -*- coding: utf8 -*-
from nagini.properties import load_properties, save_properties
from dateutil.rrule import rrule, MO, MONTHLY, WEEKLY, DAILY
from dateutil.relativedelta import relativedelta
from abc import ABCMeta, abstractmethod
from nagini.fields import BaseField
from nagini.utility import flatten
from nagini.target import Target
from os.path import join, exists
from os import mkdir, environ
from datetime import datetime
from copy import deepcopy
import logging.config
import subprocess
import logging
import shutil
import json
import yaml


class BaseJob(object):
    _check_output_at_start = True
    _check_output_at_end = True
    name = None
    retries = 0
    retry_backoff = 0
    config = None

    def __init__(self):
        self.props = load_properties()
        self.env = environ.copy()
        if exists('/etc/nagini.yml'):
            with open('/etc/nagini.yml') as fd:
                config = yaml.load(fd)
                if 'logging' in config:
                    logging.config.dictConfig(config['logging'])
        self.logger = logging.getLogger('nagini.job.%s' %
                                        self.__class__.__name__)
        if 'working.dir' in self.props:
            self.props['working.dir.nagini'] = join(self.props['working.dir'],
                                                    'nagini_data')

            with open(join(self.props['working.dir'], 'config.yml')) as fd:
                self.config = yaml.load(fd)

        self._fields = []

    def requires(self):
        """Override me!"""
        return []

    def output(self):
        """Override me!"""
        return []

    def input(self):
        """Return outputs of requires

        :rtype: Target|list[Target]
        """
        requires = self.requires()
        if isinstance(requires, (tuple, list, set)):
            for job in requires:
                job.configure()
            return [r.output() for r in requires]
        elif isinstance(requires, BaseJob):
            requires.configure()
            return requires.output()
        elif isinstance(requires, dict):
            for item in requires.itervalues():
                item.configure()
            return {k: v.output() for k, v in requires.iteritems()}
        else:
            raise ValueError("requires() must return BaseJob or list[BaseJob]")

    def is_complete(self):
        return False

    def run(self):
        """Override me!"""
        self.logger.error("Nagini: this job is empty. Override run() method")

    def on_failure(self):
        for target in flatten(self.output()):
            target.clean_up()

    def on_success(self):
        pass

    def execute(self):
        self.props = load_properties()
        self.props["working.dir.nagini"] = join(self.props["working.dir"],
                                                "nagini_data")
        # if not exists(self.props["working.dir.nagini"]):
        try:
            mkdir(self.props["working.dir.nagini"])
        except OSError:
            pass
        self._define_fields()
        self.configure()
        self.logger.info("Init props:\n" +
                         json.dumps(self.props, ensure_ascii=False, indent=4))
        output = flatten(self.output())
        if not output:
            self._check_output_at_start = False
        try:
            self.logger.info("Nagini: start job")
            if self._check_output_at_start and all(t.exists() for t in output):
                self.logger.warning("All targets exists at start "
                                    "of the job, skip job...")
            else:
                self.logger.info('Nagini: about to execute "run" method')
                self.run()
            for key, value in self.env.iteritems():
                self.props["env.%s" % key] = value
            self._save_fields()
            save_properties(self.props)
            if self._check_output_at_end and not all(t.exists() for t in output):
                raise Exception("Not all output target exists "
                                "at end of the job")
            else:
                self.on_success()
        except BaseException as e:
            self.logger.error("NaginiJob: catch exception. Try on_failure()")
            self.on_failure()
            raise

    def rupdate_props(self, props):
        """Like self.props.update but not override existing props"""
        props.update(self.props)
        self.props = props

    def data_path(self, path=""):
        return join(self.props["working.dir.nagini"], path)

    def clear_data_dir(self):
        if exists(self.props["working.dir.nagini"]):
            shutil.rmtree(self.props["working.dir.nagini"])

    def configure(self):
        """Additional method to configure instead __init__
        Don't use __init__ to configure
        """

    def _define_fields(self):
        for name, field in self.__dict__.iteritems():
            if isinstance(field, BaseField):
                prop_name = field.name or name
                if field.name is None:
                    field.name = name
                if field.require and prop_name not in self.props:
                    raise KeyError('Property "%s" not set '
                                   'in props (required)' % prop_name)
                setattr(self, name, field.to_python(self.props.get(prop_name)))

    def _save_fields(self):
        for name in self._fields:
            value = getattr(self, name)
            if isinstance(value, unicode):
                value = value.encode('utf8')
            self.props[name] = str(value)


class UploadToMySqlJob(BaseJob):
    """
    Input must be LocalTarget
    Output must be MySqlTarget
    """
    host = None
    port = None
    user = None
    password = None
    config_file = None

    db = None
    table = None
    fields = None
    clear = False

    def run(self):
        sql = """
        SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE;
        SET autocommit=0;
        START TRANSACTION;
        {delete}
        LOAD DATA LOCAL INFILE '{filename}'
        IGNORE INTO TABLE {table} {fields};
        SHOW WARNINGS;
        COMMIT;""".format(
            filename=self.input().path,
            table=self.table,
            fields=("(`%s`)" % "`,`".join(self.fields)) if self.fields else "",
            delete="DELETE FROM `%s`;" % self.table if self.clear else ""
        )

        args = ["mysql"]
        if self.config_file:
            args.append("--defaults-extra-file=%s" % self.config_file)
        else:
            args += ["--host=%s" % self.host, "--port=%s" % str(self.port),
                     "--user=%s" % self.user, "--password=%s" % self.password]

        if self.db:
            args.append("--database=%s" % self.db)

        args += ["--default-character-set=utf8", "--local-infile=1",
                 "--silent", "-q", "-e", sql]

        subprocess.check_call(args)


class MySqlQueryJob(BaseJob):
    """You can set sql in subclass or override run method and use
    `query` method
    """
    host = None
    port = None
    user = None
    password = None
    config_file = None

    db = None
    table = None
    fields = None
    sql = None
    sort_by = None  # works only with fields

    def run(self):
        sql = "SELECT {fields} FROM {table}".format(
            fields=("`%s`" % "`, `".join(self.fields)) if self.fields else "*",
            table=self.table
        )

        self.query(self.sql or sql)

    def query(self, sql):
        args = ["mysql"]
        if self.config_file:
            args.append("--defaults-extra-file=%s" % self.config_file)
        else:
            args += ["--host=%s" % self.host, "--port=%s" % str(self.port),
                     "--user=%s" % self.user, "--password=%s" % self.password]

        if self.db:
            args.append("--database=%s" % self.db)

        args += ["--quick", "--default-character-set=utf8", "--silent",
                 "--column-names", "--skip-pager", "-e", sql]

        if self.output():
            with open(self.output().path, "wb") as fd:
                if self.sort_by is not None:
                    if isinstance(self.sort_by, int):
                        field_n = self.sort_by
                    else:
                        field_n = self.fields.index(self.sort_by) + 1
                    qp = subprocess.Popen(args, stdout=subprocess.PIPE)
                    sort_cmd = ["sort", "-k{0},{0}".format(field_n),
                                "-t", "\t"]
                    sort = subprocess.Popen(sort_cmd,
                                            stdin=qp.stdout, stdout=fd,
                                            env={"LC_ALL": "C", "LANG": "C"})
                    if qp.wait():
                        raise subprocess.CalledProcessError(qp.returncode,
                                                            args)
                    if sort.wait():
                        raise subprocess.CalledProcessError(sort.returncode,
                                                            sort_cmd)
                else:
                    subprocess.check_call(args, stdout=fd.fileno())
        else:
            subprocess.check_call(args)


class ClearAllJob(BaseJob):
    def run(self):
        self.clear_data_dir()


class BaseChecker(BaseJob):
    work_flow = None
    work_flow_params = None
    __metaclass__ = ABCMeta

    def run(self):
        if self.need_update():
            self.start_work_flow()

    @abstractmethod
    def need_update(self):
        raise NotImplementedError('You must implement "need_update" method')

    def start_work_flow(self):
        if self.work_flow is None:
            raise ValueError('You must set "work_flow" property')
        else:
            self.work_flow.start(self.work_flow_params or {})


class DataChecker(BaseChecker):
    __metaclass__ = ABCMeta

    def need_update(self):
        if self.src_data_exists() and self.dst_data_exists():
            self.start_work_flow()

    @abstractmethod
    def src_data_exists(self):
        raise NotImplementedError('You must implement '
                                  '"src_data_exists" method')

    @abstractmethod
    def dst_data_exists(self):
        raise NotImplementedError('You must implement '
                                  '"dst_data_exists" method')


class IntervalDataChecker(BaseJob):
    __metaclass__ = ABCMeta
    check_interval = None
    type = None
    src = None
    source_data_peace_delta = None
    source_data_count = 1
    prepared_data_pattern = None
    work_flow = None
    work_flow_params = None

    def run(self):
        end = None
        start = None

        if self.type == MONTHLY:
            end = datetime.now() - relativedelta(months=1, day=1, hour=0,
                                                 minute=0, second=0)
            start = end - relativedelta(months=self.check_interval)
        elif self.type == WEEKLY:
            end = datetime.now() - relativedelta(weekday=MO(-1), hour=0,
                                                 minute=0, second=0)
            start = end - relativedelta(weeks=self.check_interval)
        elif self.type == DAILY:
            end = datetime.now() - relativedelta(hour=0, minute=0, second=0)
            start = end - relativedelta(days=self.check_interval)

        for current in rrule(self.type, start, until=end):
            s = current
            if self.type == MONTHLY:
                e = s + relativedelta(months=1)
            elif self.type == WEEKLY:
                e = s + relativedelta(weeks=1)
            elif self.type == DAILY:
                e = s + relativedelta(days=1)

            if self.work_flow and not self.dst_data_exists(s, e):
                print 'Prepared data not exists for', s, e
                if self.src_data_exists(s, e):
                    print "Start prepare data. Start: %s, End: %s" % (s, e)
                    self.start_work_flow(s, e)

    @abstractmethod
    def src_data_exists(self, s, e):
        raise NotImplementedError('You must implement '
                                  '"src_data_exists" method')

    @abstractmethod
    def dst_data_exists(self, s, e):
        raise NotImplementedError('You must implement '
                                  '"dst_data_exists" method')

    def start_work_flow(self, s, e):
        params = deepcopy(self.work_flow_params) or {}

        if self.type == MONTHLY:
            params["month"] = s.strftime("%Y-%m")
        elif self.type == WEEKLY:
            params.update({"week": s.strftime("%Y-%m-%d"),
                           "start": s.strftime("%Y-%m-%d"),
                           "end": e.strftime("%Y-%m-%d")})
        elif self.type == DAILY:
            params["day"] = s.strftime("%Y-%m-%d")

        self.work_flow.start(params)
