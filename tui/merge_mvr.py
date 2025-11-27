# Copyright (C) 2025 vanous
#
# This file is part of MVRtoKuma.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from types import SimpleNamespace
import pymvr
from pathlib import Path
from copy import deepcopy


def process_mvr_child_list(child_list, result):
    for fixture in child_list.fixtures:
        result.append((fixture))
    for group in child_list.group_objects:
        if group.child_list is not None:
            process_mvr_child_list(group.child_list, result)


def get_fixtures(file_path):
    path = Path(file_path)
    fixtures = []

    mvr_scene = pymvr.GeneralSceneDescription(path)
    if hasattr(mvr_scene, "scene") and mvr_scene.scene:
        for layer in mvr_scene.scene.layers:
            if layer.child_list is not None:
                process_mvr_child_list(layer.child_list, fixtures)

    return (
        mvr_scene,
        fixtures,
    )


def get_ipv4_network(fixture):
    for network in fixture.addresses.networks:
        if network.ipv4:
            return network


def get_address(fixture):
    for address in fixture.addresses.addresses:
        if address:
            return address


def address_equals(this, that):
    return this.address == that.address and this.universe == that.universe


def copy_network(in_ipv4_network, out_fixture):
    out_ipv4_network = get_ipv4_network(out_fixture)
    if out_ipv4_network is None:
        ipv4 = deepcopy(in_ipv4_network)
        out_fixture.addresses.networks.append(ipv4)
    else:
        out_ipv4_network = deepcopy(in_ipv4_network)


def merger(in_path, out_path):
    in_scene, in_fixtures = get_fixtures(in_path)
    out_scene, out_fixtures = get_fixtures(out_path)
    done_already = []

    while in_fixtures:
        in_fixture = in_fixtures.pop()
        in_address = get_address(in_fixture)
        in_ipv4_network = get_ipv4_network(in_fixture)
        if in_ipv4_network is None:
            continue  # we cannot use fixtures without network...

        for out_fixture in out_fixtures:
            if out_fixture.uuid in done_already:
                continue
            if in_fixture.uuid == out_fixture.uuid:
                # match by uuid:
                copy_network(in_ipv4_network, out_fixture)
                done_already.append(out_fixture.uuid)
                break
            out_address = get_address(out_fixture)
            if in_address is None or out_address is None:
                continue  # no uuid match and one or both fixtures have no dmx, so we cannot match, skip

            if address_equals(in_address, out_address):
                # match by universe and address
                copy_network(in_ipv4_network, out_fixture)
                done_already.append(out_fixture.uuid)

    mvr_writer = pymvr.GeneralSceneDescriptionWriter()
    out_scene.scene.to_xml(parent=mvr_writer.xml_root)
    out_scene.user_data.to_xml(parent=mvr_writer.xml_root)
    output_path = Path("merged_with_network.mvr")
    mvr_writer.write_mvr(output_path)


if __name__ == "__main__":
    merger(
        "/home/vanous/bin/projects/uptime-kuma-mvr/test.mvr",
        "/home/vanous/bin/projects/uptime-kuma-mvr/6pointe.mvr",
    )
