#!/usr/bin/env python

__copyright__ = "Copyright 2014"

"""
Name:
     trex_unit_test.py


Description:

    This script creates the functionality to test the performance of the T-Rex traffic generator
    The tested scenario is a T-Rex TG directly connected to a Cisco router.

::

    Topology:

       -------                         --------
      |       | Tx---1gig/10gig----Rx |        |
      | T-Rex |                       | router |
      |       | Rx---1gig/10gig----Tx |        |
       -------                         --------

"""

import os
import sys
import outer_packages
import nose
from nose.plugins import Plugin
import logging
import CustomLogger
import misc_methods
from rednose import RedNose
import termstyle
from trex import CTRexScenario
from client.trex_client import *
from common.trex_exceptions import *
from trex_stl_lib.api import *
import trex
import socket
from pprint import pprint
import subprocess
import re
import time

def check_trex_path(trex_path):
    if os.path.isfile('%s/trex_daemon_server' % trex_path):
        return os.path.abspath(trex_path)

def check_setup_path(setup_path):
    if os.path.isfile('%s/config.yaml' % setup_path):
        return os.path.abspath(setup_path)


def get_trex_path():
    latest_build_path = check_trex_path(os.getenv('TREX_UNDER_TEST'))    # TREX_UNDER_TEST is env var pointing to <trex-core>/scripts
    if not latest_build_path:
        latest_build_path = check_trex_path(os.path.join(os.pardir, os.pardir))
    if not latest_build_path:
        raise Exception('Could not determine trex_under_test folder, try setting env.var. TREX_UNDER_TEST')
    return latest_build_path

STATEFUL_STOP_COMMAND = './trex_daemon_server stop; sleep 1; ./trex_daemon_server stop; sleep 1'
STATEFUL_RUN_COMMAND = 'rm /var/log/trex/trex_daemon_server.log; ./trex_daemon_server start; sleep 2; ./trex_daemon_server show'
TREX_FILES = ('_t-rex-64', '_t-rex-64-o', '_t-rex-64-debug', '_t-rex-64-debug-o')

def trex_remote_command(trex_data, command, background = False):
    return misc_methods.run_remote_command(trex_data['trex_name'], ('cd %s; ' % CTRexScenario.scripts_path)+ command, background)

# 1 = running, 0 - not running
def check_trex_running(trex_data):
    commands = []
    for filename in TREX_FILES:
        commands.append('ps -C %s > /dev/null' % filename)
    (return_code, stdout, stderr) = trex_remote_command(trex_data, ' || '.join(commands))
    return not return_code

def kill_trex_process(trex_data):
    (return_code, stdout, stderr) = trex_remote_command(trex_data, 'ps -u root --format comm,pid,cmd | grep _t-rex-64 | grep -v grep || true')
    assert return_code == 0, 'last remote command failed'
    if stdout:
        for process in stdout.split('\n'):
            try:
                proc_name, pid, full_cmd = re.split('\s+', process, maxsplit=2)
                if proc_name.find('t-rex-64') >= 0:
                    print 'Killing remote process: %s' % full_cmd
                    trex_remote_command(trex_data, 'kill %s' % pid)
            except:
                continue

def address_to_ip(address):
    for i in range(10):
        try:
            return socket.gethostbyname(address)
        except:
            continue
    return socket.gethostbyname(address)


class CTRexTestConfiguringPlugin(Plugin):
    def options(self, parser, env = os.environ):
        super(CTRexTestConfiguringPlugin, self).options(parser, env)
        parser.add_option('--cfg', '--trex-scenario-config', action='store',
                            dest='config_path',
                            help='Specify path to folder with config.yaml and benchmark.yaml')
        parser.add_option('--skip-clean', '--skip_clean', action='store_true',
                            dest='skip_clean_config',
                            help='Skip the clean configuration replace on the platform.')
        parser.add_option('--load-image', '--load_image', action='store_true', default = False,
                            dest='load_image',
                            help='Install image specified in config file on router.')
        parser.add_option('--log-path', '--log_path', action='store',
                            dest='log_path',
                            help='Specify path for the tests` log to be saved at. Once applied, logs capturing by nose will be disabled.') # Default is CURRENT/WORKING/PATH/trex_log/trex_log.log')
        parser.add_option('--verbose-mode', '--verbose_mode', action="store_true", default = False,
                            dest="verbose_mode",
                            help="Print RPC command and router commands.")
        parser.add_option('--server-logs', '--server_logs', action="store_true", default = False,
                            dest="server_logs",
                            help="Print server side (TRex and trex_daemon) logs per test.")
        parser.add_option('--kill-running', '--kill_running', action="store_true", default = False,
                            dest="kill_running",
                            help="Kills running TRex process on remote server (useful for regression).")
        parser.add_option('--func', '--functional', action="store_true", default = False,
                            dest="functional",
                            help="Run functional tests.")
        parser.add_option('--stl', '--stateless', action="store_true", default = False,
                            dest="stateless",
                            help="Run stateless tests.")
        parser.add_option('--stf', '--stateful', action="store_true", default = False,
                            dest="stateful",
                            help="Run stateful tests.")
        parser.add_option('--copy', action="store_true", default = False,
                            dest="copy",
                            help="Copy TRex server to temp directory and run from there.")
        parser.add_option('--no-ssh', '--no_ssh', action="store_true", default = False,
                            dest="no_ssh",
                            help="Flag wherever not to connect via ssh to run the daemons etc.")

    def configure(self, options, conf):
        self.functional = options.functional
        self.stateless = options.stateless
        self.stateful = options.stateful
        self.copy = options.copy
        self.collect_only = options.collect_only
        if self.functional or self.collect_only:
            return
        if CTRexScenario.setup_dir and options.config_path:
            raise Exception('Please either define --cfg or use env. variable SETUP_DIR, not both.')
        if not options.config_path and CTRexScenario.setup_dir:
            options.config_path = CTRexScenario.setup_dir
        if options.config_path:
            self.configuration = misc_methods.load_complete_config_file(os.path.join(options.config_path, 'config.yaml'))
            self.configuration.trex['trex_name'] = address_to_ip(self.configuration.trex['trex_name'])
            self.benchmark = misc_methods.load_benchmark_config_file(os.path.join(options.config_path, 'benchmark.yaml'))
            self.enabled = True
        else:
            raise Exception('Please specify path to config.yaml using --cfg parameter or env. variable SETUP_DIR')
        self.modes         = self.configuration.trex.get('modes', [])
        self.kill_running  = options.kill_running
        self.load_image    = options.load_image
        self.verbose_mode  = options.verbose_mode
        self.no_ssh        = options.no_ssh
        self.clean_config  = False if options.skip_clean_config else True
        self.server_logs   = options.server_logs
        if options.log_path:
            self.loggerPath = options.log_path

    def begin (self):
        # initialize CTRexScenario global testing class, to be used by all tests
        CTRexScenario.configuration = self.configuration
        CTRexScenario.benchmark     = self.benchmark
        CTRexScenario.modes         = set(self.modes)
        CTRexScenario.server_logs   = self.server_logs
        if self.copy and not CTRexScenario.is_copied and not self.no_ssh:
            new_path = '/tmp/trex_scripts'
            return_code, stdout, stderr = trex_remote_command(self.configuration.trex,
                                                              'mkdir -p %s; rsync -L -az %s/ %s' % (new_path, CTRexScenario.scripts_path, new_path))
            if return_code:
                print 'Failed copying'
                sys.exit(-1)
            CTRexScenario.scripts_path = new_path
            CTRexScenario.is_copied = True
        if self.functional or self.collect_only:
            return
        # launch TRex daemon on relevant setup
        if not self.no_ssh:
            if self.kill_running:
                if self.stateful:
                    trex_remote_command(self.configuration.trex, STATEFUL_STOP_COMMAND)
                kill_trex_process(self.configuration.trex)
                time.sleep(1)
            elif check_trex_running(self.configuration.trex):
                print 'TRex is already running'
                sys.exit(-1)


        if self.stateful:
            if not self.no_ssh:
                trex_remote_command(self.configuration.trex, STATEFUL_RUN_COMMAND)
            CTRexScenario.trex = CTRexClient(trex_host = self.configuration.trex['trex_name'], verbose = self.verbose_mode)
        elif self.stateless:
            if not self.no_ssh:
                trex_remote_command(self.configuration.trex, './t-rex-64 -i', background = True)
            CTRexScenario.stl_trex = STLClient(username = 'TRexRegression',
                                               server = self.configuration.trex['trex_name'],
                                               verbose_level = self.verbose_mode)
        if 'loopback' not in self.modes:
            CTRexScenario.router_cfg = dict(config_dict      = self.configuration.router,
                                            forceImageReload = self.load_image,
                                            silent_mode      = not self.verbose_mode,
                                            forceCleanConfig = self.clean_config,
                                            tftp_config_dict = self.configuration.tftp)
        try:
            CustomLogger.setup_custom_logger('TRexLogger', self.loggerPath)
        except AttributeError:
            CustomLogger.setup_custom_logger('TRexLogger')

    def finalize(self, result):
        if self.functional or self.collect_only:
            return
        CTRexScenario.is_init = False
        if not self.no_ssh:
            if self.stateful:
                trex_remote_command(CTRexScenario.configuration.trex, STATEFUL_STOP_COMMAND)
            kill_trex_process(CTRexScenario.configuration.trex)


def save_setup_info():
    try:
        if CTRexScenario.setup_name and CTRexScenario.trex_version:
            setup_info = ''
            for key, value in CTRexScenario.trex_version.items():
                setup_info += '{0:8}: {1}\n'.format(key, value)
            cfg = CTRexScenario.configuration
            setup_info += 'Server: %s, Modes: %s' % (cfg.trex.get('trex_name'), cfg.trex.get('modes'))
            if cfg.router:
                setup_info += '\nRouter: Model: %s, Image: %s' % (cfg.router.get('model'), CTRexScenario.router_image)
            with open('%s/report_%s.info' % (CTRexScenario.report_dir, CTRexScenario.setup_name), 'w') as f:
                f.write(setup_info)
    except Exception as err:
        print 'Error saving setup info: %s ' % err


def set_report_dir (report_dir):
    if not os.path.exists(report_dir):
        os.mkdir(report_dir)

if __name__ == "__main__":

    # setting defaults. By default we run all the test suite
    specific_tests              = False
    CTRexScenario.report_dir    = 'reports'
    need_to_copy                = False
    setup_dir                   = os.getenv('SETUP_DIR', '').rstrip('/')
    CTRexScenario.setup_dir     = check_setup_path(setup_dir)
    CTRexScenario.scripts_path  = get_trex_path()
    if not CTRexScenario.setup_dir:
        CTRexScenario.setup_dir = check_setup_path(os.path.join('setups', setup_dir))


    nose_argv = ['', '-s', '-v', '--exe', '--rednose', '--detailed-errors']
    if '--collect-only' in sys.argv: # this is a user trying simply to view the available tests. no need xunit.
        CTRexScenario.is_test_list   = True
        xml_arg                      = ''
    else:
        xml_name                     = 'unit_test.xml'
        if CTRexScenario.setup_dir:
            CTRexScenario.setup_name = os.path.basename(CTRexScenario.setup_dir)
            xml_name = 'report_%s.xml' % CTRexScenario.setup_name
        xml_arg= '--xunit-file=%s/%s' % (CTRexScenario.report_dir, xml_name)
        set_report_dir(CTRexScenario.report_dir)

    sys_args = sys.argv[:]
    for i, arg in enumerate(sys.argv):
        if 'log-path' in arg:
            nose_argv += ['--nologcapture']
        else:
            for tests_type in CTRexScenario.test_types.keys():
                if tests_type in arg:
                    specific_tests = True
                    CTRexScenario.test_types[tests_type].append(arg[arg.find(tests_type):])
                    sys_args.remove(arg)

    if not specific_tests:
        for key in ('--func', '--functional'):
            if key in sys_args:
                CTRexScenario.test_types['functional_tests'].append('functional_tests')
                sys_args.remove(key)
        for key in ('--stf', '--stateful'):
            if key in sys_args:
                CTRexScenario.test_types['stateful_tests'].append('stateful_tests')
                sys_args.remove(key)
        for key in ('--stl', '--stateless'):
            if key in sys_args:
                CTRexScenario.test_types['stateless_tests'].append('stateless_tests')
                sys_args.remove(key)
        # Run all of the tests or just the selected ones
        if not sum([len(x) for x in CTRexScenario.test_types.values()]):
            for key in CTRexScenario.test_types.keys():
                CTRexScenario.test_types[key].append(key)

    nose_argv += sys_args

    config_plugin = CTRexTestConfiguringPlugin()
    red_nose = RedNose()
    result = True
    try:
        if len(CTRexScenario.test_types['functional_tests']):
            additional_args = ['--func'] + CTRexScenario.test_types['functional_tests']
            if xml_arg:
                additional_args += ['--with-xunit', xml_arg.replace('.xml', '_functional.xml')]
            result = nose.run(argv = nose_argv + additional_args, addplugins = [red_nose, config_plugin])
        if len(CTRexScenario.test_types['stateful_tests']):
            additional_args = ['--stf'] + CTRexScenario.test_types['stateful_tests']
            if xml_arg:
                additional_args += ['--with-xunit', xml_arg.replace('.xml', '_stateful.xml')]
            result = result and nose.run(argv = nose_argv + additional_args, addplugins = [red_nose, config_plugin])
        if len(CTRexScenario.test_types['stateless_tests']):
            additional_args = ['--stl', 'stateless_tests/stl_general_test.py:STLBasic_Test.test_connectivity'] + CTRexScenario.test_types['stateless_tests']
            if xml_arg:
                additional_args += ['--with-xunit', xml_arg.replace('.xml', '_stateless.xml')]
            result = result and nose.run(argv = nose_argv + additional_args, addplugins = [red_nose, config_plugin])
    finally:
        save_setup_info()

    if (result == True and not CTRexScenario.is_test_list):
        print termstyle.green("""
                 ..::''''::..
               .;''        ``;.
              ::    ::  ::    ::
             ::     ::  ::     ::
             ::     ::  ::     ::
             :: .:' ::  :: `:. ::
             ::  :          :  ::
              :: `:.      .:' ::
               `;..``::::''..;'
                 ``::,,,,::''

               ___  ___   __________
              / _ \/ _ | / __/ __/ /
             / ___/ __ |_\ \_\ \/_/
            /_/  /_/ |_/___/___(_)

        """)
        sys.exit(0)
    sys.exit(-1)









