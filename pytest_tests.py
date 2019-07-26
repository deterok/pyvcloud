#!/usr/bin/env python3

import asyncio
from functools import partial
import time
import uuid
from contextlib import contextmanager, asynccontextmanager

import pytest
import requests
from envparse import env
from lxml import etree

from pyvcloud.vcd.client import BasicLoginCredentials
from pyvcloud.vcd.client import Client, MetadataValueType,\
    MetadataVisibility, MetadataDomain, ResourceType,\
    NetworkAdapterType, VCLOUD_STATUS_MAP
from pyvcloud.vcd.client import EntityType
from pyvcloud.vcd.org import Org
from pyvcloud.vcd.task import Task, TaskStatus
from pyvcloud.vcd.vapp import VApp, RelationType
from pyvcloud.vcd.vdc import VDC
from pyvcloud.vcd.vm import VM
# from pyvcloud.vcd.system import System
from pyvcloud.vcd.utils import tag


env.read_envfile()

CREDS = dict(
    host = env('host'),
    org = env('org'),
    user = env('user'),
    password = env('password'),
    vdc_name = env('vdc_name'),
)

# Disable warnings from self-signed certificates.
requests.packages.urllib3.disable_warnings()


@pytest.fixture()
async def client():
    cli = Client(
        env('host'),
        api_version='31.0',
        verify_ssl_certs=False,
        log_file=None,
        log_requests=False,
        log_headers=False,
        log_bodies=False
    )
    login_credentials = BasicLoginCredentials(
        env('user'),
        env('org'),
        env('password')
    )
    await cli.set_credentials(
        login_credentials
    )

    yield cli

    await cli.logout()


@pytest.fixture()
async def org(client):
    org_resource = await client.get_org()
    org_inst = Org(client, resource=org_resource)
    yield org_inst


@pytest.fixture()
async def vdc(org):
    vdc_resource = await org.get_vdc(env('vdc_name'))
    vdc_inst = VDC(org.client, resource=vdc_resource)
    yield vdc_inst


@pytest.fixture()
async def vdc2(org):
    vdc_resource = await org.get_vdc(env('vdc_name2'))
    vdc_inst = VDC(org.client, resource=vdc_resource)
    yield vdc_inst


@pytest.mark.asyncio()
async def test_get_vdc_all(org):
    ls = []
    async for vdc in org.get_vdc_all():
        ls.append(vdc)
    assert len(ls)
    l = ls[0]
    for key in (
        'href',
        'name',
        'id',
    ):
        assert key in l



@pytest.fixture()
async def vapp(vdc, client):
    name = uuid.uuid4().hex[:5]
    await vdc.instantiate_vapp(
        name,
        env('catalog'),
        env('template_name'),
        storage_profile_id=env('storage_profile_id'),
    )

    await vdc.reload()
    vapp_xml = await vdc.get_vapp(name)
    vapp = VApp(vdc.client, name=name, resource=vapp_xml)

    yield vapp

    await asyncio.sleep(1.0)

    try:
        await vdc.reload()
    except:
        vdc = VDC(client, resource=vdc.resource)  # Hack for "server disconnected" bug
        await vdc.reload()
    await vdc.delete_vapp_by_id(vapp.id, force=True)


@pytest.fixture()
async def vapp_test(vdc):
    vapp_xml = await vdc.get_vapp_by_id('urn:vcloud:vapp:9508d5e3-14bf-4e8f-9a02-0f4c72ceca6f')
    vapp = VApp(vdc.client, resource=vapp_xml)

    yield vapp

    await asyncio.sleep(1.0)
    await vdc.reload()
    await vapp.reload()

    # await vdc.delete_vapp_by_id(vapp.id, force=True)


@pytest.fixture()
async def vapp_off(vdc):
    name = uuid.uuid4().hex[:5]
    await vdc.instantiate_vapp(
        name,
        env('catalog'),
        env('template_name'),
        storage_profile_id=env('storage_profile_id'),
        deploy=False,
        power_on=False,
    )

    await vdc.reload()
    vapp_xml = await vdc.get_vapp(name)
    vapp = VApp(vdc.client, name=name, resource=vapp_xml)

    yield vapp

    await vdc.reload()
    await asyncio.sleep(1.0)
    await vapp.reload()
    await vdc.delete_vapp(vapp.name, force=True)


@pytest.fixture
async def template(vapp, vdc):
    org = Org(
        vapp.client,
        resource=(
            await vapp.client.get_org()
        )
    )
    template_name = uuid.uuid4().hex[:10]
    catalog_resource = await org.get_catalog(env('catalog'))
    await org.capture_vapp(
        catalog_resource,
        vapp.href,
        template_name,
        'Test template'
    )

    yield template_name

    await vdc.reload()
    href = await vdc.get_resource_href(
        template_name,
        entity_type=EntityType.VAPP_TEMPLATE
    )
    await vapp.client.delete_resource(href, force=True)


@pytest.mark.asyncio
async def test_create_delete_template(vapp):
    org = Org(
        vapp.client,
        resource=(
            await vapp.client.get_org()
        )
    )
    catalog_resource = await org.get_catalog(env('catalog'))
    template_name = uuid.uuid4().hex[:10]
    template_id = await org.capture_vapp(
        catalog_resource,
        vapp.href,
        template_name,
        ''
    )

    await org.reload()
    try:
        item = await org.get_catalog_item('Test', template_name)
        assert item is not None
        assert item.get('id') == template_id
    finally:
        await org.delete_catalog_item_by_id('Test', template_id)


@pytest.mark.asyncio
async def test_suspend_on_off(vapp):
    # await asyncio.sleep(1)
    await vapp.reload()
    await vapp._perform_power_operation(
        rel=RelationType.POWER_SUSPEND, operation_name='power suspend')
    await vapp.reload()
    assert VCLOUD_STATUS_MAP[await vapp.get_power_state()] == 'Suspended'

    await vapp._perform_power_operation(
        rel=RelationType.DISCARD_SUSPENDED_STATE, operation_name='discard suspend')
    await vapp.reload()
    assert VCLOUD_STATUS_MAP[await vapp.get_power_state()] in (
        'Powered off',
        'Powered on'
    )


@pytest.mark.asyncio
async def test_create_delete_getlist_vapp(vapp, vdc):
    vapps = await vdc.list_resources(EntityType.VAPP)
    apps = set([vapp['name'] for vapp in vapps])
    assert vapp.name in apps


@pytest.mark.skip
@pytest.mark.asyncio
async def test_poweroff_shutdown(vapp):
    assert VCLOUD_STATUS_MAP[await vapp.get_power_state()] == 'Powered on'
    await vapp.reload()
    await vapp.power_off()
    await vapp.reload()
    assert VCLOUD_STATUS_MAP[await vapp.get_power_state()] == 'Powered off'

    await vapp.power_on()
    await vapp.reload()
    assert VCLOUD_STATUS_MAP[await vapp.get_power_state()] == 'Powered on'

    await vapp.shutdown()
    await vapp.reload()
    await asyncio.sleep(5)
    assert VCLOUD_STATUS_MAP[await vapp.get_power_state()] == 'Powered off'


# @pytest.mark.asyncio
# async def test_get_vm_by_href(vapp, vdc):
#     vm_resource = await vapp.get_vm()
#     vm_resource2 = await vdc.get_vm_by_href(vm_resource.get('href'))
#     assert vm_resource2 is not None


@pytest.mark.asyncio
async def test_vm_change_storage_policy(vapp, vdc):
    storage_profile_id = env('storage_profile_id')
    storage_profile_id2 = env('storage_profile_id2')
    vm_resource = await vapp.get_vm()
    vm = VM(vapp.client, resource=vm_resource)
    vdc_resource = await vdc.get_resource()
    for profile in vdc_resource.VdcStorageProfiles.VdcStorageProfile:
        print('test', profile.get('id'), storage_profile_id, profile.get('id') == storage_profile_id)
        if profile.get('id') == storage_profile_id2:
            storage_profile = profile
            break
    else:
        raise ValueError('No this StorageProfile with id={}'.format(storage_profile_id))

    await vm.update_general_setting(storage_policy_href=storage_profile.get('href'))

    await vm.reload()
    assert (await vm.get_storage_profile_id()) == storage_profile_id2



@pytest.mark.asyncio
async def test_vm_disk(vapp, vdc):
    vm_resource = await vapp.get_vm()
    vm = VM(vapp.client, resource=vm_resource)

    disk_id = await vm.add_disk(
        'test_add_disk',
        300,
        env('storage_profile_id'),
        '6',
        'VirtualSCSI'
    )
    try:
        disk = await vm.get_disk(disk_id)
        assert disk is not None
        disk_resource_list = await vm.get_disks()
        for disk_resource in disk_resource_list:
            if getattr(
                    disk_resource,
                    tag('rasd')('InstanceID')
            ).text == getattr(
                disk,
                tag('rasd')('InstanceID')
            ).text:
                break
        else:
            raise Exception(f'No new disk with id {disk_id} in disk list')

        assert int(
            getattr(
                disk,
                tag('rasd')('HostResource')
            ).get(
                tag('vcloud')('capacity')
            )
        ) == 300

        await vm.modify_disk(disk_id,
                             size=1024)
        disk_resource = await vm.get_disk(disk_id)
        assert int(
            getattr(
                disk_resource,
                tag('rasd')('HostResource')
            ).get(
                tag('vcloud')('capacity')
            )
        ) == 1024
        storage_profile_href = disk_resource[
            tag('rasd')('HostResource')
        ].get(tag('vcloud')('storageProfileHref'))
        resource = await vapp.client.get_resource(storage_profile_href)
        storage_profile_id = resource.get('id')
        assert storage_profile_id.startswith('urn:')

        # Change storage profile
        storage_policy_id_2 = env('storage_profile_id')
        await vm.modify_disk(disk_id, storage_policy_id=storage_policy_id_2)
        await vm.reload()
        disk_resource = await vm.get_disk(disk_id)
        storage_profile_href = disk_resource[
            tag('rasd')('HostResource')
        ].get(tag('vcloud')('storageProfileHref'))
        resource = await vapp.client.get_resource(storage_profile_href)
        storage_profile_id = resource.get('id')
        assert storage_profile_id == storage_policy_id_2
    finally:
        await vm.delete_disk(disk_id)


@pytest.mark.asyncio
async def test_add_resources(vapp, vdc):
    await vapp.reload()

    await vapp.power_off()
    await vdc.reload()
    for vm_xml in await vapp.get_all_vms():
        vm = VM(vdc.client, resource=vm_xml)

        # Modify memory
        await vm.modify_memory(2024)

        await vm.reload()
        mem = await vm.get_memory()
        assert mem == 2024

        await vm.modify_memory(1024)
        await vm.reload()
        mem = await vm.get_memory()
        assert mem == 1024

        # Modify CPU
        await vm.modify_cpu(4, 2)

        await vm.reload()
        result = await vm.get_cpus()
        assert result['num_cpus'] == 4
        assert result['num_cores_per_socket'] == 2

        # Create a disk
        disk_id = await vdc.create_disk(
            name='TestName',
            size=1024 * 1024 * 50,  # 50 MB
            description='Test description'
        )
        await vdc.reload()
        try:
            disk = await vdc.get_disk(disk_id=disk_id)

            assert disk.get('name') == 'TestName'
            assert int(disk.get('size')) == 1024 * 1024 * 50
            assert disk.Description == 'Test description'

            # Attach disk
            await vapp.attach_disk_to_vm(disk_href=disk.get('href'),
                                            vm_name=vm_xml.get('name'))

            # Detach disk
            await vapp.detach_disk_from_vm(disk_href=disk.get('href'),
                                            vm_name=vm_xml.get('name'))

            # Update disk
            await vdc.update_disk(
                disk_id=disk_id,
                new_name='New name',
                new_size=str(1024 * 1024 * 100),  # 100 MB
                new_description='New description',
            )

            disk = await vdc.get_disk(disk_id=disk_id)

            assert disk.get('id') == disk_id
            assert disk.get('name') == 'New name'
            assert int(disk.get('size')) == 1024 * 1024 * 100
            assert disk.Description == 'New description'
        finally:
            # Delete disk
            await vdc.delete_disk(disk_id=disk_id)


@pytest.mark.asyncio
async def test_snapshot(vapp, vdc):
    await vapp.reload()

    await vapp.power_off()
    await vdc.reload()

    for vm_xml in await vapp.get_all_vms():
        vm = VM(vdc.client, resource=vm_xml)

        # Create snapshot
        await vm.snapshot_create(name='TestSnapshot')

        # Check is there a snapshot
        await vm.reload()
        assert len((await vm.get_resource()).SnapshotSection.Snapshot)

        # Revert to current shapshot
        await vm.reload()
        await vm.snapshot_revert_to_current()

        # Remove all shapshot
        await vm.reload()
        await vm.snapshot_remove_all()


@pytest.mark.asyncio
async def test_status(vapp_off):
    assert await vapp_off.is_powered_off() == True
    assert await vapp_off.is_powered_on() == False
    assert await vapp_off.is_suspended() == False
    assert await vapp_off.is_deployed() == False

    await vapp_off.deploy(True)

    await vapp_off.reload()
    assert (await vapp_off.get_resource()).get('deployed') == 'true'
    assert await vapp_off.is_powered_on() == True

    await vapp_off.reload()
    await vapp_off.undeploy('powerOff')

    await vapp_off.reload()
    assert (await vapp_off.get_resource()).get('deployed') == 'false'

    assert await vapp_off.is_powered_off() == True
    assert await vapp_off.is_powered_on() == False
    assert await vapp_off.is_suspended() == False


@pytest.mark.asyncio
async def test_change_name(vapp_off):
    name = 'testChangeName'

    await vapp_off.edit_name_and_description(name)

    await vapp_off.reload()
    assert vapp_off.name == name


@pytest.mark.asyncio
async def test_vm_change_name(vapp):
    vm_resource = await vapp.get_vm()
    vm = VM(vapp.client, resource=vm_resource)
    await vm.change_name('new_test_name2')
    await vm.reload()
    vm_resource = await vm.get_resource()
    assert vm_resource.get('name') == 'new_test_name2'


@pytest.mark.asyncio
async def test_get_vdc_list(org):
    l = org.list_vdcs()
    l = list(map(lambda xml: VDC(org.client, resource=xml), l))
    assert env('vdc_name') in (vdc.name for vdc in l)


@pytest.mark.asyncio
async def test_template(template, vdc):
    await vdc.reload()
    assert sum([
        dic['name'] == template for dic in await vdc.list_resources(
            EntityType.VAPP_TEMPLATE
        )
    ]) == 1


@pytest.mark.asyncio
async def test_vm_network(vapp_off, vdc):
    """
    Test create, connect and remove network connection.
    """
    test_network_name = env('test_network_name')

    # Connect vapp to network
    networks = await vapp_off.get_all_networks()
    network_name_list = []
    for network in networks:
        network_name_list.append(
            network.get(tag('ovf')('name'))
        )
    if test_network_name not in network_name_list:
        await vapp_off.connect_org_vdc_network(test_network_name)

    # Get current vm
    vm_resource = await vapp_off.get_vm()
    vm = VM(vapp_off.client, resource=vm_resource)

    # Add a nic
    idx = await vm.add_nic(
        NetworkAdapterType.VMXNET3.value,
        True,
        True,
        test_network_name,
        'DHCP',
        ''
    )
    await vm.reload()
    try:
        # Check nic in VM
        assert test_network_name in [dic['network'] for dic in await vm.list_nics()]
    finally:
        await vm.reload()

    # Add a nic
    idx2 = await vm.add_nic(
        NetworkAdapterType.VMXNET3.value,
        False,
        True,
        test_network_name,
        'DHCP',
        ''
    )
    await vm.reload()
    try:
        # Check nic in VM
        assert test_network_name in [dic['network'] for dic in await vm.list_nics()]
    finally:
        await vm.reload()
        await vm.delete_nic(idx)
        await vm.delete_nic(idx2)


@pytest.mark.asyncio
async def test_get_vapp_by_id(vapp, vdc):
    vapp_name = vapp.name
    vapp_id = vapp.id
    vapp_resource = await vdc.get_vapp_by_id(vapp_id)
    vapp = VApp(vapp.client, resource=vapp_resource)
    assert vapp_name == vapp.name


@pytest.mark.asyncio
async def test_vm_product_section(vdc, vapp):
    vm_resource = await vapp.get_vm()
    vm = VM(vdc.client, resource=vm_resource)
    await vm.del_product_section(('tag1', 'tag2'))
    try:
        d = await vm.get_product_section(('tag1', 'tag2'))
        assert d == {}
        await vm.add_product_section(tag1='test1')
        await vm.add_product_section(tag2='test2')
        result = await vm.get_product_section(('tag1', 'tag2'))
        assert result == {
            'tag1': 'test1',
            'tag2': 'test2'
        }
        await vm.modify_product_section(tag1='test11', tag3='test3')
        result = await vm.get_product_section(('tag1', 'tag2', 'tag3', 'tag4'))
        assert result == {
            'tag1': 'test11',
            'tag2': 'test2',
            'tag3': 'test3',
        }
    finally:
        await vm.del_product_section(('tag1', 'tag2', 'tag3'))
    result = await vm.get_product_section(('tag1', 'tag2'))
    assert result == {}


@pytest.mark.asyncio
async def test_guest_customization_section(vapp):
    vm_resource = await vapp.get_vm()
    vm = VM(vapp.client, resource=vm_resource)
    guest_xml_old = await vm.get_guest_customization_section()
    for field_name in ('VirtualMachineId', 'ComputerName'):
        assert hasattr(guest_xml_old, field_name)
    await vm.set_guest_customization_section(
        Enabled=True,
        AdminPassword='1234567890',
        AdminPasswordAuto=False,
        AdminPasswordEnabled=True,
        # JoinDomainEnabled=True,
        # UseOrgSettings=False,
        ComputerName='TestComputer5',
        # ChangeSid=None,
        # ResetPasswordRequired=None,
        # AdminAutoLogonCount=None,
        # AdminAutoLogonEnabled=None,
        # AdminPasswordAuto=None,
        # AdminPasswordEnabled=None,
        # UseOrgSettings=None,
        # JoinDomainEnabled=None,
        # VirtualMachineId=None,
    )
    await vm.reload()
    guest_xml_new = await vm.get_guest_customization_section()
    assert guest_xml_new.AdminPassword.text == '1234567890'
    assert guest_xml_new.ComputerName.text == 'TestComputer5'
    for field_name in ('VirtualMachineId',):
        assert getattr(
            guest_xml_old, field_name
        ).text == getattr(
            guest_xml_new, field_name
        ).text


@pytest.mark.skip
@pytest.mark.asyncio
async def test_copy_vm(vapp, vdc, vdc2):
    test_new_name = 'TestCloneVapp2'
    await vdc2.create_vapp(test_new_name)
    try:
        vm_resource = await vapp.get_vm()
        vm = VM(vapp.client, resource=vm_resource)
        # await vm.power_off()
        await vapp.power_off()
        await vapp.reload()
        await vm.reload()
        await vm.copy_to(vapp.name, test_new_name, vm_resource.get('name'))
        await vdc2.reload()
        vapp_resource = await vdc2.get_vapp(test_new_name)
        vapp = VApp(vapp.client, resource=vapp_resource)
        vm_resource_new = await vapp.get_vm()
        assert vm_resource.get('name') == vm_resource_new.get('name')

        await vm.power_on()
    finally:
        await vdc2.reload()
        await vdc2.delete_vapp(test_new_name, force=True)


@pytest.mark.asyncio
async def test_clone_vapp(vapp, vdc, vdc2):
    test_new_name = 'TestCloneVapp2'
    clone_vapp_id = await vapp.clone(
        test_new_name,
        vdc2.href,
        deploy=False,
        power_on=False,
        linked_clone=False,
    )
    await vdc2.reload()
    try:
        vm_resource = await vapp.get_vm()
        clone_vapp_resource = await vdc2.get_vapp_by_id(clone_vapp_id)
        clone_vapp = VApp(vapp.client, resource=clone_vapp_resource)
        clone_vm_resource = await clone_vapp.get_vm()

        assert vm_resource.get('name') == clone_vm_resource.get('name')
        assert clone_vapp_resource.get('deployed') == 'false'
        assert await clone_vapp.is_powered_on() == False
    finally:
        await vdc2.delete_vapp_by_id(clone_vapp_id)


@pytest.mark.asyncio
async def test_catalogs(org):
    catalogs = await org.list_catalogs()
    for catalog in catalogs:
        assert 'isShared' in catalog
        assert 'name' in catalog
        assert 'id' in catalog


@pytest.mark.asyncio
async def test_networks(vdc):
    networks = await vdc.list_orgvdc_network_resources()
    for network in networks:
        assert network.get('id') is not None
        assert network.get('name') is not None


@pytest.mark.asyncio
async def test_template_without_networks(vdc):
    vapp_id = await vdc.instantiate_vapp(
        'TestTemplateWithoutNetwork',
        env('catalog'),
        env('template_name'),
        storage_profile_id=env('storage_profile_id'),
    )
    try:
        await vdc.reload()
        vapp_resource = await vdc.get_vapp_by_id(vapp_id)
        vapp = VApp(vdc.client, resource=vapp_resource)
        vm_resource = await vapp.get_vm()
        vm = VM(client, resource=vm_resource)
        nics = await vm.list_nics()
        nics = [nic for nic in nics if nic['connected'] == True]
        assert len(nics) == 0
    finally:
        await vdc.delete_vapp_by_id(vapp_id, True)


@pytest.mark.asyncio
async def test_get_media(vapp):
    vm_resource = await vapp.get_vm()
    vm = VM(vapp.client, resource=vm_resource)
    _ = await vm.get_medias()


@pytest.mark.asyncio
async def test_mks_ticket(vapp):
    vm_resource = await vapp.get_vm()
    vm = VM(vapp.client, resource=vm_resource)
    dic = await vm.get_mks_ticket()
    assert isinstance(dic['host'], str)
    assert isinstance(dic['port'], str)
    assert isinstance(dic['vmx'], str)
    assert isinstance(dic['ticket'], str)


@pytest.mark.asyncio
async def test_ticket(vapp):
    vm_resource = await vapp.get_vm()
    vm = VM(vapp.client, resource=vm_resource)
    dic = await vm.get_mks_ticket()
    assert isinstance(dic['ticket'], str)


@pytest.mark.asyncio
async def test_vmtools_installed(vapp):
    vm_resource = await vapp.get_vm()
    vm = VM(vapp.client, resource=vm_resource)
    result = await vm.is_vmtools_installed()
    assert isinstance(result, bool)


@pytest.mark.skip()
@pytest.mark.asyncio
async def test_tmp(vapp):
    # vapp_resource = await vdc.get_vapp_by_id('urn:vcloud:vapp:ae16b508-fea3-412a-8ba7-25fbd366607f')
    # vdc_resource = await vdc.get_resource()
    # storage_profile_id2 = "urn:vcloud:vdcstorageProfile:812d8160-48bb-4c7a-b03e-7637124c1d6a"
    # catalog = 'Test'
    # test_network_name = 'cloudmng-lab-internal01'
    # vapp_resource = await vapp.get_resource()
    vm_resource = await vapp.get_vm()
    vm = VM(vapp.client, resource=vm_resource)
    ticket = await vm.get_ticket()
    raise ZeroDivisionError(ticket)
    # with open('tmp.xml', 'wb') as f:
    #     f.write(
    #         etree.tostring(
    #             resource,
    #             pretty_print=True
    #         )
    #     )