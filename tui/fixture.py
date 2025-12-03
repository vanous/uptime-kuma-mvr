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


class KumaFixture:
    def __init__(self, data=None):
        if data is None:
            return
        self.name = data.get("name", "")
        self.id = data.get("id", 0)
        self.uuid = data.get("description", "")
        self.tags = [tag.get("name") for tag in data.get("tags", [])]

    def __str__(self):
        return f"{self.name=} {self.id=} {self.description=} tags={','.join(self.tags)}"


class KumaTag:
    def __init__(self, data=None):
        if data is None:
            return
        self.id = data.get("id", 0)
        self.name = data.get("name", "")

    def __str__(self):
        return f"{self.name=} {self.id=}"
