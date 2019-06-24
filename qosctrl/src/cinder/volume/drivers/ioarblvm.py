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
#    Disclaimer: This source file is a modified version of the lvm driver 
#                in OpenStack Kilo sources.

"""
Driver for Linux servers running LVM.

"""

import math
import os
import socket
import time

from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import importutils
from oslo_utils import units

from cinder import context
from cinder.brick import exception as brick_exception
from cinder.brick.local_dev import ioarblvm as lvm
from cinder.brick.local_dev import ioarbcontainer as contutil
from cinder import exception
from cinder.i18n import _, _LE, _LI, _LW
from cinder.image import image_utils
from cinder.openstack.common import fileutils
from cinder.common import ioarbparams as ioarbiter
from cinder.common import ioarbresv as ioarbresv
from cinder import utils

from cinder.volume import driver
from cinder.volume import utils as volutils
from cinder.volume import qos_specs
from cinder.volume import volume_types

LOG = logging.getLogger(__name__)

# FIXME(jdg):  We'll put the lvm_ prefix back on these when we
# move over to using this as the real LVM driver, for now we'll
# rename them so that the config generation utility doesn't barf
# on duplicate entries.
volume_opts = [
    cfg.StrOpt('volume_group',
               default='cinder-volumes',
               help='Name for the VG that will contain exported volumes'),
    cfg.IntOpt('lvm_mirrors',
               default=0,
               help='If >0, create LVs with multiple mirrors. Note that '
                    'this requires lvm_mirrors + 2 PVs with available space'),
    cfg.StrOpt('lvm_type',
               default='default',
               choices=['default', 'thin'],
               help='Type of LVM volumes to deploy'),
    cfg.StrOpt('lvm_conf_file',
               default='/etc/cinder/lvm.conf',
               help='LVM conf file to use for the LVM driver in Cinder; '
                    'this setting is ignored if the specified file does '
                    'not exist (You can also specify \'None\' to not use '
                    'a conf file even if one exists).'),
    cfg.IntOpt('reclaim_interval',
               default=300,
               help='How much you would wait before you reclaim block resources.'),
    cfg.StrOpt('physical_devices',
               default='auto',
               help='This setting contains a list of block devices that'
                    'ioarbiter backend will manage.                    '
                    'Example:                                          '
                    'physical_devices = /dev/sdl,/dev/sdm,/dev/sdn     '
                    'physical_devices = auto                           ')
]

CONF = cfg.CONF
CONF.register_opts(volume_opts)


class IOArbLVMVolumeDriver(driver.VolumeDriver):
    """Executes commands relating to Volumes."""

    VERSION = '0.5.0'

    def __init__(self, vg_obj=None, *args, **kwargs):
        # Parent sets db, host, _execute and base config
        super(IOArbLVMVolumeDriver, self).__init__(*args, **kwargs)

        self.configuration.append_config_values(volume_opts)
        self.hostname = socket.gethostname()
        self.vg = vg_obj
        self.backend_name =\
            self.configuration.safe_get('volume_backend_name') or 'LVM'

        # Target Driver is what handles data-transport
        # Transport specific code should NOT be in
        # the driver (control path), this way
        # different target drivers can be added (iscsi, FC etc)
        target_driver = \
            self.target_mapping[self.configuration.safe_get('iscsi_helper')]

        LOG.debug('Attempting to initialize LVM driver with the '
                  'following target_driver: %s',
                  target_driver)

        self.target_driver = importutils.import_object(
            target_driver,
            configuration=self.configuration,
            db=self.db,
            executor=self._execute)
        self.protocol = self.target_driver.protocol

        # [MRA] ioarbiter specifics.
        self.ref_physical_devices = self.configuration.physical_devices
        self._update_available_physical_devices()

        self.raidstat = {}

    def _update_available_physical_devices(self):
        """Filter out already-in-use devices"""
        root_helper = utils.get_root_helper()
        old_devlist = self.ref_physical_devices.split(',')
        new_devlist = lvm.LVM.filter_blkdev_in_use(root_helper, old_devlist)
        LOG.debug('[MRA] orig: %(old)s, filtered: %(new)s' 
                     % {'old': old_devlist, 'new': new_devlist})
        # put back with original format.
        self.configuration.physical_devices = ','.join('{1}'.format(*k) 
                                              for k in enumerate(new_devlist))
        return len(new_devlist)

    def _sizestr(self, size_in_g):
        return '%sg' % size_in_g

    def _volume_not_present(self, volume_name):
        return self.vg.get_volume(volume_name) is None

    def _delete_volume(self, volume, is_snapshot=False):
        """Deletes a logical volume."""
        if self.configuration.volume_clear != 'none' and \
                self.configuration.lvm_type != 'thin':
            self._clear_volume(volume, is_snapshot)

        name = volume['name']
        if is_snapshot:
            name = self._escape_snapshot(volume['name'])
        self.vg.delete(name)

    def _clear_volume(self, volume, is_snapshot=False):
        # zero out old volumes to prevent data leaking between users
        # TODO(ja): reclaiming space should be done lazy and low priority
        if is_snapshot:
            # if the volume to be cleared is a snapshot of another volume
            # we need to clear out the volume using the -cow instead of the
            # directly volume path.  We need to skip this if we are using
            # thin provisioned LVs.
            # bug# lp1191812
            dev_path = self.local_path(volume) + "-cow"
        else:
            dev_path = self.local_path(volume)

        # TODO(jdg): Maybe we could optimize this for snaps by looking at
        # the cow table and only overwriting what's necessary?
        # for now we're still skipping on snaps due to hang issue
        if not os.path.exists(dev_path):
            msg = (_LE('Volume device file path %s does not exist.')
                   % dev_path)
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        size_in_g = volume.get('volume_size') or volume.get('size')
        if size_in_g is None:
            msg = (_LE("Size for volume: %s not found, "
                   "cannot secure delete.") % volume['id'])
            LOG.error(msg)
            raise exception.InvalidParameterValue(msg)

        # clear_volume expects sizes in MiB, we store integer GiB
        # be sure to convert before passing in
        vol_sz_in_meg = size_in_g * units.Ki

        volutils.clear_volume(
            vol_sz_in_meg, dev_path,
            volume_clear=self.configuration.volume_clear,
            volume_clear_size=self.configuration.volume_clear_size)

    def _escape_snapshot(self, snapshot_name):
        # Linux LVM reserves name that starts with snapshot, so that
        # such volume name can't be created. Mangle it.
        if not snapshot_name.startswith('snapshot'):
            return snapshot_name
        return '_' + snapshot_name

    def _create_volume(self, name, size, lvm_type, mirror_count, vg=None, cmd_prefix=None):
        vg_ref = self.vg
        if vg is not None:
            vg_ref = vg

        vg_ref.create_volume(name, size, lvm_type, mirror_count, cmd_prefix=cmd_prefix)

    def _update_volume_stats(self):
        """Retrieve stats info from volume group."""

        # [MRA] piggypack periodic tasks here.
        root_helper = utils.get_root_helper()
        self._reclaim_unused_storage()
        ndev = self._update_available_physical_devices()
        if ndev == 0:
            LOG.debug("[MRA] nothing to update. ndev=0")
            self._stats = {}
            #return

        LOG.debug(("Updating volume stats"))

#        if self.vg is None:
#            LOG.warning(_LW('Unable to update stats on non-initialized '
#                            'Volume Group: %s'),
#                        self.configuration.volume_group)
#            return
#
#        self.vg.update_volume_group_info()
        data = {}

        # Note(zhiteng): These information are driver/backend specific,
        # each driver may define these values in its own config options
        # or fetch from driver specific configuration file.
        data["volume_backend_name"] = self.backend_name
        data["vendor_name"] = 'ATT Research'
        data["driver_version"] = self.VERSION
        data["storage_protocol"] = self.protocol
        data["pools"] = []

        total_capacity = 0
        free_capacity = 0

#        if self.configuration.lvm_mirrors > 0:
#            total_capacity =\
#                self.vg.vg_mirror_size(self.configuration.lvm_mirrors)
#            free_capacity =\
#                self.vg.vg_mirror_free_space(self.configuration.lvm_mirrors)
#            provisioned_capacity = round(
#                float(total_capacity) - float(free_capacity), 2)
#        elif self.configuration.lvm_type == 'thin':
#            total_capacity = self.vg.vg_thin_pool_size
#            free_capacity = self.vg.vg_thin_pool_free_space
#            provisioned_capacity = self.vg.vg_provisioned_capacity
#        else:
#            total_capacity = self.vg.vg_size
#            free_capacity = self.vg.vg_free_space
#            provisioned_capacity = round(
#                float(total_capacity) - float(free_capacity), 2)

        if ndev == 0:
            devinfo = []
            total_capacity = 0
        else:
            devinfo = lvm.LVM.get_blkdev_info(
                root_helper, self.configuration.physical_devices)
            total_capacity = sum(dev['size'] for dev in devinfo)

        free_capacity = total_capacity
        provisioned_capacity = round(
            float(total_capacity) - float(free_capacity), 2)

        location_info = \
            ('IOArbLVMVolumeDriver:%(hostname)s:%(vg)s'
             ':%(lvm_type)s:%(lvm_mirrors)s' %
             {'hostname': self.hostname,
              'vg': self.configuration.volume_group,
              'lvm_type': self.configuration.lvm_type,
              'lvm_mirrors': self.configuration.lvm_mirrors})

        thin_enabled = self.configuration.lvm_type == 'thin'

        # Calculate the total volumes used by the VG group.
        # This includes volumes and snapshots.
        #total_volumes = len(self.vg.get_volumes())
        total_volumes = 0

        # Skip enabled_pools setting, treat the whole backend as one pool
        # XXX FIXME if multipool support is added to LVM driver.
        single_pool = {}
        single_pool.update(dict(
            pool_name=data["volume_backend_name"],
            total_capacity_gb=total_capacity,
            free_capacity_gb=free_capacity,
            reserved_percentage=self.configuration.reserved_percentage,
            location_info=location_info,
            QoS_support=False,
            provisioned_capacity_gb=provisioned_capacity,
            max_over_subscription_ratio=(
                self.configuration.max_over_subscription_ratio),
            thin_provisioning_support=thin_enabled,
            thick_provisioning_support=not thin_enabled,
            total_volumes=total_volumes,
            filter_function=self.get_filter_function(),
            goodness_function=self.get_goodness_function(),
            # [MRA] announce that this cinder-volume is capable.
            ioarb_sttype='ioarbiter',
            ioarb_cvtype='host',
            ioarb_resource=devinfo
        ))
        data["pools"].append(single_pool)

        self._stats = data

    def _create_initial_vg(self, conf):
        """Create an initial volume group for ioarbiter backend.

           We will use the first block device specified in 'physical_devices' 
           field as a starting volume group for cinder-volume service. 
           we might need a better mechanism later.
        """
        LOG.debug('[MRA] initial vg [%(dev)s]' % {'dev': conf.physical_devices})
        vgname = conf.volume_group
        phydev = conf.physical_devices.split(',')[0]	
        return vgname, [ phydev ]

    def check_for_setup_error(self):
        """Verify that requirements are in place to use LVM driver."""

        # [MRA] we will not use this function.
        return
       
        if self.vg is None:
            root_helper = utils.get_root_helper()

            lvm_conf_file = self.configuration.lvm_conf_file
            if lvm_conf_file.lower() == 'none':
                lvm_conf_file = None

            # [MRA] Automatic redundancy control. 
            vgname, phydev = self._create_initial_vg(self.configuration)

            try:
                self.vg = lvm.LVM(self.configuration.volume_group,
                                  root_helper,
                                  create_vg=True,
                                  physical_volumes=phydev,
                                  lvm_type=self.configuration.lvm_type,
                                  executor=self._execute,
                                  lvm_conf=lvm_conf_file)

            except brick_exception.VolumeGroupNotFound:
                message = (_("Volume Group %s does not exist") %
                           self.configuration.volume_group)
                raise exception.VolumeBackendAPIException(data=message)

        vg_list = volutils.get_all_volume_groups(
            self.configuration.volume_group)
        vg_dict = \
            (vg for vg in vg_list if vg['name'] == self.vg.vg_name).next()
        if vg_dict is None:
            message = (_("Volume Group %s does not exist") %
                       self.configuration.volume_group)
            raise exception.VolumeBackendAPIException(data=message)

        if self.configuration.lvm_type == 'thin':
            # Specific checks for using Thin provisioned LV's
            if not volutils.supports_thin_provisioning():
                message = _("Thin provisioning not supported "
                            "on this version of LVM.")
                raise exception.VolumeBackendAPIException(data=message)

            pool_name = "%s-pool" % self.configuration.volume_group
            if self.vg.get_volume(pool_name) is None:
                try:
                    self.vg.create_thin_pool(pool_name)
                except processutils.ProcessExecutionError as exc:
                    exception_message = (_("Failed to create thin pool, "
                                           "error message was: %s")
                                         % exc.stderr)
                    raise exception.VolumeBackendAPIException(
                        data=exception_message)

    def _reclaim_unused_storage(self):
        """If an empty array exists, reclaim it for future use.""" 

        # get root_helper.
        root_helper = utils.get_root_helper()
        arraydevs = lvm.LVM.get_raid_arrays(root_helper)
        jboddevs = lvm.LVM.get_jbods_devs(root_helper, 
                                          contutil._get_cont_vg_prefix())
        arraydevs.extend(jboddevs)

        for arrdev in arraydevs:
            vgname = contutil._get_cont_vg_name(arrdev)
            cnt = lvm.LVM.get_lvcnt_by_vgname(root_helper, vgname)
            if cnt == 0:
                if (arrdev in self.raidstat and self.raidstat[arrdev][0] == 0):
                    if (time.time() - self.raidstat[arrdev][1] > 
                            self.configuration.reclaim_interval):
                        # reclaim it if it has been unused for more than 5 min.
                        contutil.remove_cont_cinder_volume(root_helper, arrdev)
                        lvm.LVM.remove_array(root_helper, 
                                             arrdev, 
                                             vgname=vgname)
                        del self.raidstat[arrdev]
                        LOG.debug('[MRA] array [%(arr)s] has been reclaimed' % {'arr': arrdev})
                    continue

            # update stat.
            self.raidstat[arrdev] = [cnt, time.time()]


    # [MRA] this function is copied from solidfire driver.
    def _retrieve_qos_info(self, ctxt, type_id):
        qosspec = {}
        volume_type = volume_types.get_volume_type(ctxt, type_id)
        qos_specs_id = volume_type.get('qos_specs_id')
        voltype = volume_type.get('extra_specs')

        if qos_specs_id is not None:
            qosspec = qos_specs.get_qos_specs(ctxt, qos_specs_id)['specs']

        return voltype, qosspec


    def _fork_cinder_volume_service(self, blkdev, root_helper, stspec, volume):
        """Create (or retrieve) a container for cinder-volume service."""

        LOG.debug('[MRA] entered _fork_cinder_volume_service()'
                  ' with [%(blk)s]' % {'blk': blkdev})

        # container naming.
        cont_name = contutil._get_container_name(blkdev)
        backend_name = contutil._get_cont_backend_name(blkdev)

        # create a cinder.conf for the container.
        config = contutil.create_cinder_conf_for_container(blkdev, 
                                                           stspec, None)

        # memo reservation info.
        resv_fpath = ioarbresv.get_resv_filepath(blkdev)
        ioarbresv.add_resv_info(resv_fpath, volume['id'], stspec)

        # create a container instance.
        config = contutil.check_container_is_running(config, root_helper)
        config['resv_info'] = resv_fpath
        if not 'container_id' in config:
            config = contutil.create_container_instance(config, root_helper)

        # configure container.
        config = contutil.configure_container_instance(config, root_helper)

        # restart daemons.
        svclist = ['tgt', 'cinder-volume']
        contutil.restart_processes_in_container(config['container_name']
                                              , svclist, root_helper)

        return config

    def create_volume(self, volume):
        """Creates a logical volume.
           [MRA] this function is extended to support dynamic
           RAID configuration. 
        """

        # ensure usable block devices.
        ndev = self._update_available_physical_devices()
        if ndev == 0:
            LOG.error('[MRA] no available block devices.')
            raise

        # variable initialization.
        root_helper = utils.get_root_helper()
        vtype = None
        qosspec = None

        mirror_count = 0
        if self.configuration.lvm_mirrors:
            mirror_count = self.configuration.lvm_mirrors

        ctxt = context.get_admin_context()
        type_id = volume['volume_type_id']
        if type_id is not None:
            voltype, qosspec = self._retrieve_qos_info(ctxt, type_id)

            # check request format.
            if not ioarbiter.STTYPE in qosspec.keys():
                LOG.error('[MRA] invalid qos spec. [%(stype)s] field is missing.'
                          % {'stype': ioarbiter.STTYPE})
                raise
        else:
            # no volume type.
            qosspec = { ioarbiter.STTYPE: "ioarb-unknown" }

        # qosspec translation.
        stspec = ioarbiter.translate_qosspec(qosspec)
        stspec['phydevs'] = self.configuration.physical_devices
        LOG.debug('[MRA] setup: %(spec)s' % {'spec': stspec})

        # sanity check.
        if len(stspec['phydevs']) == 0:
            LOG.error('[MRA] no avaialble physical devices.')
            raise

        # software RAID configuration. new_raiddev looks like '/dev/md[n]'
        new_blkdev = lvm.LVM.create_software_raid(root_helper, stspec)
        new_vgname = contutil._get_cont_vg_name(new_blkdev)

        # logical volume creation.
        newvg = lvm.LVM(new_vgname, root_helper,
                        create_vg=True,
                        physical_volumes=[ new_blkdev ])

        # invoke a container & update volume metadata.
        config = self._fork_cinder_volume_service(
                     new_blkdev, root_helper, stspec, volume)
        cmd_prefix = contutil.get_cmdprefix_for_exec_in_cont(config)
        self._create_volume(volume['name'],
                            self._sizestr(volume['size']),
                            self.configuration.lvm_type,
                            mirror_count,
                            vg=newvg,
                            cmd_prefix=cmd_prefix)

        # return new cinder-volume endpoint.
        newhost = (config['container_name'] + '@' + config['backend_name'])

        LOG.debug('[MRA] volume is created. newhost: [%(newhost)s]'  
                  % {'newhost': newhost})

        return { 'host': newhost }


    def create_volume_from_snapshot(self, volume, snapshot):
        """Creates a volume from a snapshot."""
        self._create_volume(volume['name'],
                            self._sizestr(volume['size']),
                            self.configuration.lvm_type,
                            self.configuration.lvm_mirrors)

        # Some configurations of LVM do not automatically activate
        # ThinLVM snapshot LVs.
        self.vg.activate_lv(snapshot['name'], is_snapshot=True)

        # copy_volume expects sizes in MiB, we store integer GiB
        # be sure to convert before passing in
        volutils.copy_volume(self.local_path(snapshot),
                             self.local_path(volume),
                             snapshot['volume_size'] * units.Ki,
                             self.configuration.volume_dd_blocksize,
                             execute=self._execute)

    def delete_volume(self, volume):
        """Deletes a logical volume."""

        # NOTE(jdg):  We don't need to explicitly call
        # remove export here because we already did it
        # in the manager before we got here.

        if self._volume_not_present(volume['name']):
            # If the volume isn't present, then don't attempt to delete
            return True

        if self.vg.lv_has_snapshot(volume['name']):
            LOG.error(_LE('Unabled to delete due to existing snapshot '
                          'for volume: %s') % volume['name'])
            raise exception.VolumeIsBusy(volume_name=volume['name'])

        self._delete_volume(volume)
        LOG.info(_LI('Successfully deleted volume: %s'), volume['id'])

    def create_snapshot(self, snapshot):
        """Creates a snapshot."""

        self.vg.create_lv_snapshot(self._escape_snapshot(snapshot['name']),
                                   snapshot['volume_name'],
                                   self.configuration.lvm_type)

    def delete_snapshot(self, snapshot):
        """Deletes a snapshot."""
        if self._volume_not_present(self._escape_snapshot(snapshot['name'])):
            # If the snapshot isn't present, then don't attempt to delete
            LOG.warning(_LW("snapshot: %s not found, "
                            "skipping delete operations") % snapshot['name'])
            LOG.info(_LI('Successfully deleted snapshot: %s'), snapshot['id'])
            return True

        # TODO(yamahata): zeroing out the whole snapshot triggers COW.
        # it's quite slow.
        self._delete_volume(snapshot, is_snapshot=True)

    def local_path(self, volume, vg=None):
        if vg is None:
            vg = self.configuration.volume_group
        # NOTE(vish): stops deprecation warning
        escaped_group = vg.replace('-', '--')
        escaped_name = self._escape_snapshot(volume['name']).replace('-', '--')
        return "/dev/mapper/%s-%s" % (escaped_group, escaped_name)

    def copy_image_to_volume(self, context, volume, image_service, image_id):
        """Fetch the image from image_service and write it to the volume."""
        image_utils.fetch_to_raw(context,
                                 image_service,
                                 image_id,
                                 self.local_path(volume),
                                 self.configuration.volume_dd_blocksize,
                                 size=volume['size'])

    def copy_volume_to_image(self, context, volume, image_service, image_meta):
        """Copy the volume to the specified image."""
        image_utils.upload_volume(context,
                                  image_service,
                                  image_meta,
                                  self.local_path(volume))

    def create_cloned_volume(self, volume, src_vref):
        """Creates a clone of the specified volume."""

        mirror_count = 0
        if self.configuration.lvm_mirrors:
            mirror_count = self.configuration.lvm_mirrors
        LOG.info(_LI('Creating clone of volume: %s') % src_vref['id'])
        volume_name = src_vref['name']
        temp_id = 'tmp-snap-%s' % volume['id']
        temp_snapshot = {'volume_name': volume_name,
                         'size': src_vref['size'],
                         'volume_size': src_vref['size'],
                         'name': 'clone-snap-%s' % volume['id'],
                         'id': temp_id}

        self.create_snapshot(temp_snapshot)

        # copy_volume expects sizes in MiB, we store integer GiB
        # be sure to convert before passing in
        try:
            self._create_volume(volume['name'],
                                self._sizestr(volume['size']),
                                self.configuration.lvm_type,
                                mirror_count)

            self.vg.activate_lv(temp_snapshot['name'], is_snapshot=True)
            volutils.copy_volume(
                self.local_path(temp_snapshot),
                self.local_path(volume),
                src_vref['size'] * units.Ki,
                self.configuration.volume_dd_blocksize,
                execute=self._execute)
        finally:
            self.delete_snapshot(temp_snapshot)

    def clone_image(self, context, volume,
                    image_location, image_meta,
                    image_service):
        return None, False

    def backup_volume(self, context, backup, backup_service):
        """Create a new backup from an existing volume."""
        volume = self.db.volume_get(context, backup['volume_id'])
        volume_path = self.local_path(volume)
        with utils.temporary_chown(volume_path):
            with fileutils.file_open(volume_path) as volume_file:
                backup_service.backup(backup, volume_file)

    def restore_backup(self, context, backup, volume, backup_service):
        """Restore an existing backup to a new or existing volume."""
        volume_path = self.local_path(volume)
        with utils.temporary_chown(volume_path):
            with fileutils.file_open(volume_path, 'wb') as volume_file:
                backup_service.restore(backup, volume['id'], volume_file)

    def get_volume_stats(self, refresh=False):
        """Get volume status.

        If 'refresh' is True, run update the stats first.
        """

        if refresh:
            self._update_volume_stats()

        return self._stats

    def extend_volume(self, volume, new_size):
        """Extend an existing volume's size."""
        self.vg.extend_volume(volume['name'],
                              self._sizestr(new_size))

    def manage_existing(self, volume, existing_ref):
        """Manages an existing LV.

        Renames the LV to match the expected name for the volume.
        Error checking done by manage_existing_get_size is not repeated.
        """
        lv_name = existing_ref['source-name']
        self.vg.get_volume(lv_name)

        # Attempt to rename the LV to match the OpenStack internal name.
        try:
            self.vg.rename_volume(lv_name, volume['name'])
        except processutils.ProcessExecutionError as exc:
            exception_message = (_("Failed to rename logical volume %(name)s, "
                                   "error message was: %(err_msg)s")
                                 % {'name': lv_name,
                                    'err_msg': exc.stderr})
            raise exception.VolumeBackendAPIException(
                data=exception_message)

    def manage_existing_get_size(self, volume, existing_ref):
        """Return size of an existing LV for manage_existing.

        existing_ref is a dictionary of the form:
        {'source-name': <name of LV>}
        """

        # Check that the reference is valid
        if 'source-name' not in existing_ref:
            reason = _('Reference must contain source-name element.')
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing_ref, reason=reason)
        lv_name = existing_ref['source-name']
        lv = self.vg.get_volume(lv_name)

        # Raise an exception if we didn't find a suitable LV.
        if not lv:
            kwargs = {'existing_ref': lv_name,
                      'reason': 'Specified logical volume does not exist.'}
            raise exception.ManageExistingInvalidReference(**kwargs)

        # LV size is returned in gigabytes.  Attempt to parse size as a float
        # and round up to the next integer.
        try:
            lv_size = int(math.ceil(float(lv['size'])))
        except ValueError:
            exception_message = (_("Failed to manage existing volume "
                                   "%(name)s, because reported size %(size)s "
                                   "was not a floating-point number.")
                                 % {'name': lv_name,
                                    'size': lv['size']})
            raise exception.VolumeBackendAPIException(
                data=exception_message)
        return lv_size

    def migrate_volume(self, ctxt, volume, host, thin=False, mirror_count=0):
        """Optimize the migration if the destination is on the same server.

        If the specified host is another back-end on the same server, and
        the volume is not attached, we can do the migration locally without
        going through iSCSI.
        """

        false_ret = (False, None)
        if volume['status'] != 'available':
            return false_ret
        if 'location_info' not in host['capabilities']:
            return false_ret
        info = host['capabilities']['location_info']
        try:
            (dest_type, dest_hostname, dest_vg, lvm_type, lvm_mirrors) =\
                info.split(':')
            lvm_mirrors = int(lvm_mirrors)
        except ValueError:
            return false_ret
        if (dest_type != 'IOArbLVMVolumeDriver' or dest_hostname != self.hostname):
            return false_ret

        if dest_vg != self.vg.vg_name:
            vg_list = volutils.get_all_volume_groups()
            try:
                (vg for vg in vg_list if vg['name'] == dest_vg).next()
            except StopIteration:
                message = (_LE("Destination Volume Group %s does not exist") %
                           dest_vg)
                LOG.error(message)
                return false_ret

            helper = utils.get_root_helper()

            lvm_conf_file = self.configuration.lvm_conf_file
            if lvm_conf_file.lower() == 'none':
                lvm_conf_file = None

            dest_vg_ref = lvm.LVM(dest_vg, helper,
                                  lvm_type=lvm_type,
                                  executor=self._execute,
                                  lvm_conf=lvm_conf_file)

            self.remove_export(ctxt, volume)
            self._create_volume(volume['name'],
                                self._sizestr(volume['size']),
                                lvm_type,
                                lvm_mirrors,
                                dest_vg_ref)

            volutils.copy_volume(self.local_path(volume),
                                 self.local_path(volume, vg=dest_vg),
                                 volume['size'],
                                 self.configuration.volume_dd_blocksize,
                                 execute=self._execute)
            self._delete_volume(volume)
            model_update = self.create_export(ctxt, volume, vg=dest_vg)

            return (True, model_update)
        else:
            message = (_("Refusing to migrate volume ID: %(id)s. Please "
                         "check your configuration because source and "
                         "destination are the same Volume Group: %(name)s."),
                       {'id': volume['id'], 'name': self.vg.vg_name})
            LOG.exception(message)
            raise exception.VolumeBackendAPIException(data=message)

    def get_pool(self, volume):
        return self.backend_name

    # #######  Interface methods for DataPath (Target Driver) ########

    def ensure_export(self, context, volume):
        volume_path = "/dev/%s/%s" % (self.configuration.volume_group,
                                      volume['name'])

        model_update = \
            self.target_driver.ensure_export(context, volume, volume_path)
        return model_update

    def create_export(self, context, volume, vg=None):
        if vg is None:
            vg = self.configuration.volume_group

        volume_path = "/dev/%s/%s" % (vg, volume['name'])

        export_info = self.target_driver.create_export(
            context,
            volume,
            volume_path)
        return {'provider_location': export_info['location'],
                'provider_auth': export_info['auth'], }

    def remove_export(self, context, volume):
        self.target_driver.remove_export(context, volume)

    def initialize_connection(self, volume, connector):
        return self.target_driver.initialize_connection(volume, connector)

    def validate_connector(self, connector):
        return self.target_driver.validate_connector(connector)

    def terminate_connection(self, volume, connector, **kwargs):
        return self.target_driver.terminate_connection(volume, connector,
                                                       **kwargs)


class IOArbLVMISCSIDriver(IOArbLVMVolumeDriver):
    """Empty class designation for LVMISCSI.

    Since we've decoupled the inheritance of iSCSI and LVM we
    don't really need this class any longer.  We do however want
    to keep it (at least for now) for back compat in driver naming.

    """
    def __init__(self, *args, **kwargs):
        super(IOArbLVMISCSIDriver, self).__init__(*args, **kwargs)
        LOG.warning(_LW('IOArbLVMISCSIDriver is deprecated, you should '
                        'now just use IOArbLVMVolumeDriver and specify '
                        'target_helper for the target driver you '
                        'wish to use.'))


class IOArbLVMISERDriver(IOArbLVMVolumeDriver):
    """Empty class designation for LVMISER.

    Since we've decoupled the inheritance of data path in LVM we
    don't really need this class any longer.  We do however want
    to keep it (at least for now) for back compat in driver naming.

    """
    def __init__(self, *args, **kwargs):
        super(IOArbLVMISERDriver, self).__init__(*args, **kwargs)

        LOG.warning(_LW('IOArbLVMISERDriver is deprecated, you should '
                        'now just use IOArbLVMVolumeDriver and specify '
                        'target_helper for the target driver you '
                        'wish to use. In order to enable iser, please '
                        'set iscsi_protocol with the value iser.'))

        LOG.debug('Attempting to initialize LVM driver with the '
                  'following target_driver: '
                  'cinder.volume.targets.iser.ISERTgtAdm')
        self.target_driver = importutils.import_object(
            'cinder.volume.targets.iser.ISERTgtAdm',
            configuration=self.configuration,
            db=self.db,
            executor=self._execute)
