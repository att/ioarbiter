#    Copyright (c) 2015 AT&T Labs Research
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#    
#    Author: Moo-Ryong Ra, mra@research.att.com

"""Container-related utilities and helpers."""

import os
import socket
import ConfigParser

from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_log import log as logging

from cinder.brick.local_dev import lvm as brick_lvm
from cinder.common import ioarbparams as ioarbiter
from cinder.i18n import _, _LE, _LI
from cinder import utils

#CONF = cfg.CONF
LOG = logging.getLogger(__name__)


def _get_default_conf_dir():
    return '/var/lib/cinder/ioarb-container/'

def _get_container_image():
    return 'ioarb/cinderbackend'

def _get_cont_backend_name(blkdev):
    return 'ioarb-' + blkdev.split('/')[2]

def _get_cont_vg_prefix():
    return 'ioarb-cvs-'

def _get_cont_vg_name(blkdev):
    return _get_cont_vg_prefix() + blkdev.split('/')[2]

def _get_container_name(blkdev):
    return socket.gethostname() + '-ioarbcont-' + blkdev.split('/')[2]

def _get_conf_path(blkdev):
    return (_get_default_conf_dir() + 'ioarb-cinder-' 
            + blkdev.split('/')[2] + '.conf')

# This function should be in the common library. 
# But, in order not to touch openstack distribution,
# I will keep this function locally.
def _read_cinder_conf(path='/etc/cinder/cinder.conf'):
    #
    config = ConfigParser.ConfigParser()
    if os.path.exists(path):
        config.read(path)
    else:
        LOG.error('[MRA] cannot find a cinder.conf file.')
        raise
    return config

def get_cmdprefix_for_exec_in_cont(config):
    return ['docker', 'exec', '-t', config['container_name']];

def create_cinder_conf_for_container(blkdev, stspec, config_info):
    """Automatically create cinder.conf for container."""
    
    info = {}
    if config_info is not None:
        info = config_info
    default_conf_dir = _get_default_conf_dir()

    # default values if not in config_info.
    if not 'container_name' in info:
        info['container_name'] = _get_container_name(blkdev)
    if not 'backend_name' in info:
        info['backend_name'] = _get_cont_backend_name(blkdev)
    if not 'config_path' in info:
        info['config_path'] = _get_conf_path(blkdev)
    if not 'blkdev' in info:
        info['blkdev'] = blkdev
    if not 'container_image' in info:
        info['container_image'] = _get_container_image()
    if not 'vg_gname' in info:
        info['vg_name'] = _get_cont_vg_name(blkdev)

    # read a local cinder.conf file.
    default_section = 'DEFAULT'
    config = _read_cinder_conf()

    # replace a backend information
    old_backends = config.get(default_section, 'enabled_backends').split(',')
    for backend in old_backends:
        config.remove_section(backend)

    backend = info['backend_name']
    config.add_section(backend)
    config.set(backend, 'volume_driver'
                    , 'cinder.volume.drivers.provlvm.LVMVolumeDriver')
    config.set(backend, 'iscsi_protocol', 'iscsi')
    config.set(backend, 'iscsi_helper', 'tgtadm')
    config.set(backend, 'volume_group', _get_cont_vg_name(blkdev))
    config.set(backend, 'volume_clear_size', '50')
    config.set(default_section, 'enabled_backends', backend)
    config.set(default_section, 'periodic_interval', '10')
    #config.set(default_section, 'iscsi_write_cache', 'off')

    if stspec is not None:
        config.set(backend, 'ioarb_raidconf', stspec['raidconf'])
        config.set(backend, 'ioarb_ndisk', stspec['ndisk'])
        perfmat = ioarbiter.get_perf_dict(int(stspec['ndisk']), stspec['medium'], 'rw')
        config.set(backend, 'ioarb_total_iops_4k', perfmat[stspec['raidconf']])
    
    # save it to the designated location.
    # [MRA] Todo: file creation should be done by rootwrapper. 
    with open(info['config_path'], 'wb') as configfile:
        config.write(configfile)

    return info


def check_container_is_running(config, root_helper):
    """Check if the container is already running."""

    LOG.debug('[MRA] entered check_container_is_running()')

    cmd = ['docker', 'ps', '-f', ('name=%s' % config['container_name']), '-q']
    try:
        (out, _err) = utils.execute(*cmd, root_helper=root_helper
                                        , run_as_root=True)
    except processutils.ProcessExecutionError as err:
        LOG.exception(_LE('Error checking running docker instances'))
        LOG.error(_LE('Cmd     :%s') % err.cmd)
        LOG.error(_LE('StdOut  :%s') % err.stdout)
        LOG.error(_LE('StdErr  :%s') % err.stderr)
        raise

    if out is not None and len(out) > 10:
        LOG.debug('[MRA] existed. container-id: %(contid)s'
                  % {'contid': out})
        config['container_id'] = out.strip()
    else:
        LOG.debug('[MRA] container does not exist: %(out)s' 
                  % {'out': out})

    return config
            

def create_container_instance(config, root_helper):
    """Create a docker instance that runs tgt and cinder-volume in them."""

    LOG.debug('[MRA] entered create_container_instance()')

    # run a docker instance.
    cinder_root = '/usr/lib/python2.7/dist-packages/cinder'
    resv = '%s/common/ioarbresv.py' % cinder_root
    params = '%s/common/ioarbparams.py' % cinder_root

    cmd = ['docker', 'run', '--name', config['container_name'], '-it', 
           '-p', '3260', '-d', '--privileged', 
           '-v', '%s:%s' % (config['config_path'], '/etc/cinder/cinder.conf'),
           '-v', '%s:%s' % (resv, resv),
           '-v', '%s:%s' % (params, params),
           '-v', '%s:%s' % (config['resv_info'], config['resv_info']),
           '-v', '/etc/hosts:/etc/hosts-hostmachine', config['container_image']]
    try:
        (out, _err) = utils.execute(*cmd, root_helper=root_helper
                                       , run_as_root=True)
    except processutils.ProcessExecutionError as err:
        LOG.exception(_LE('Error running docker instance'))
        LOG.error(_LE('Cmd     :%s') % err.cmd)
        LOG.error(_LE('StdOut  :%s') % err.stdout)
        LOG.error(_LE('StdErr  :%s') % err.stderr)
        raise

    config['container_id'] = out[0:12]
    LOG.debug('[MRA] created. container-id: %(out)s' % {'out': config['container_id']})

    return config

def configure_container_instance(config, root_helper):
    """Configure container instance."""

    # get a mapped port.
    cmd = ['docker', 'inspect', 
           "--format='{{(index (index .NetworkSettings.Ports \"3260/tcp\") 0).HostPort}}'",
           config['container_name']]
    try:
        (out, _err) = utils.execute(*cmd, root_helper=root_helper
                                       , run_as_root=True)
    except processutils.ProcessExecutionError as err:
        LOG.exception(_LE('Error inspecting container port mapping'))
        LOG.error(_LE('Cmd     :%s') % err.cmd)
        LOG.error(_LE('StdOut  :%s') % err.stdout)
        LOG.error(_LE('StdErr  :%s') % err.stderr)
        raise

    LOG.debug('[MRA] host port: %(out)s' % {'out': out})
    if out is not None:
        hostport = out.split()[0]
    else:
        raise

    # update container's /etc/hosts and re-map tgt port 
    # with a corresponding host port.
    cmd = ['docker', 'exec', '-t', config['container_name'],
           'ioarbiter-conf.sh', hostport]
    try:
        (out, _err) = utils.execute(*cmd, root_helper=root_helper
                                       , run_as_root=True)
    except processutils.ProcessExecutionError as err:
        LOG.exception(_LE('Error configuring container instance'))
        LOG.error(_LE('Cmd     :%s') % err.cmd)
        LOG.error(_LE('StdOut  :%s') % err.stdout)
        LOG.error(_LE('StdErr  :%s') % err.stderr)
        raise

    # change hostname of the container
    cmd = ['docker', 'exec', '-t', config['container_name'],
           'hostname', config['container_name']]
    try:
        (out, _err) = utils.execute(*cmd, root_helper=root_helper
                                       , run_as_root=True)
    except processutils.ProcessExecutionError as err:
        LOG.exception(_LE('Error changing container hostname.'))
        LOG.error(_LE('Cmd     :%s') % err.cmd)
        LOG.error(_LE('StdOut  :%s') % err.stdout)
        LOG.error(_LE('StdErr  :%s') % err.stderr)
        raise

    return config


def restart_processes_in_container(container_name, svclist, root_helper):
    #
    if len(svclist) == 0:
        return

    for svc in svclist:
        cmd = ['docker', 'exec', '-t', container_name, 'service', svc, 'restart']
        try:
            utils.execute(*cmd, root_helper=root_helper
                             , run_as_root=True)
        except processutils.ProcessExecutionError as err:
            LOG.exception(_LE('Error restarting services in container.'))
            LOG.error(_LE('Cmd     :%s') % err.cmd)
            LOG.error(_LE('StdOut  :%s') % err.stdout)
            LOG.error(_LE('StdErr  :%s') % err.stderr)
            raise


def remove_cont_cinder_volume(root_helper, arrdev):
    """Stop and remove a cinder-volume container"""

    # stop docker instance.
    cont_name = _get_container_name(arrdev)
    cmd = ['docker', 'stop', cont_name]
    try:
        utils.execute(*cmd, root_helper=root_helper
                          , run_as_root=True)
    except processutils.ProcessExecutionError as err:
        if "no such id" in err.stderr:
            LOG.debug('[MRA] container does not exists.')
            LOG.debug('[MRA] cmd: %s' % err.cmd)
            return
        LOG.exception(_LE('Error stopping container.'))
        LOG.error(_LE('Cmd     :%s') % err.cmd)
        LOG.error(_LE('StdOut  :%s') % err.stdout)
        LOG.error(_LE('StdErr  :%s') % err.stderr)
        raise

    # remove docker instance.
    cmd = ['docker', 'rm', cont_name]
    try:
        utils.execute(*cmd, root_helper=root_helper
                          , run_as_root=True)
    except processutils.ProcessExecutionError as err:
        LOG.exception(_LE('Error removing container.'))
        LOG.error(_LE('Cmd     :%s') % err.cmd)
        LOG.error(_LE('StdOut  :%s') % err.stdout)
        LOG.error(_LE('StdErr  :%s') % err.stderr)
        raise

    # remove reservation info.
    resv_fpath = '/var/lib/cinder/ioarb-resv/resv-' + arrdev.split('/')[-1]
    cmd = ['rm', '-f', resv_fpath]
    try:
        utils.execute(*cmd, root_helper=root_helper
                          , run_as_root=True)
    except processutils.ProcessExecutionError as err:
        LOG.exception(_LE('Error removing resv info.'))
        LOG.error(_LE('Cmd     :%s') % err.cmd)
        LOG.error(_LE('StdOut  :%s') % err.stdout)
        LOG.error(_LE('StdErr  :%s') % err.stderr)
        raise

# unit test code.
if __name__ == '__main__':
    print "* started."
    config = create_cinder_conf_for_container('/dev/md0', None, None)
    info = create_container_instance(config, None)
    svclist = ['tgt', 'cinder-volume']
    restart_processes_in_container(info['container_name'], svclist, None)
    print "* ended."
    



