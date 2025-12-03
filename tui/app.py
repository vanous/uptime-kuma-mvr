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

import functools
import json
import os
import random
import traceback
import subprocess
import uuid
from types import SimpleNamespace
from textual.app import App, ComposeResult
from textual import on, work, events
from textual.containers import Horizontal, Vertical, VerticalScroll, Grid
from textual.widgets import (
    Header,
    Footer,
    Input,
    Button,
    Static,
    Checkbox,
)
from textual.worker import Worker, WorkerState
from tui.screens import (
    MVRScreen,
    QuitScreen,
    ConfigScreen,
    DeleteScreen,
    AddMonitorsScreen,
    AddTagScreen,
    EditTagsScreen,
)
from uptime_kuma_api import UptimeKumaApi, MonitorType, UptimeKumaException
from textual.message import Message
from tui.fixture import KumaFixture, KumaTag
from textual.reactive import reactive
from tui.messages import MvrParsed, Errors
from tui.read_mvr import get_fixtures


class ListDisplay(Vertical):
    def update_items(self, items: list):
        self.remove_children()
        for item in items:
            tags = ""
            if hasattr(item, "tags"):
                tags = ", ".join(item.tags)
            if self.app.details_toggle:
                self.mount(
                    Static(
                        f"[green]{item.name}[/green] {item.uuid or ''} {f' {item.id or ""}' if hasattr(item, 'id') else ''}{f' [blue]Tags:[/blue] {tags}' if tags else ''}"
                    )
                )
            else:
                self.mount(
                    Static(
                        f"[green]{item.name}[/green]{f' [blue]Tags:[/blue] {tags}' if tags else ''}"
                    )
                )


class DictListDisplay(Vertical):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = []
        self.filter_text = ""
        self.list_container: VerticalScroll | None = None
        self.selected_ids: set[str] = set()

    def compose(self) -> ComposeResult:
        with Horizontal(id="mvr_filter_row"):
            yield Input(
                placeholder="Filter by name or IP",
                id="mvr_fixture_filter",
            )
        self.list_container = VerticalScroll(id="mvr_fixture_list")
        self.list_container.can_focus = False  # keep focus on checkboxes
        yield self.list_container

    def update_items(self, items: list):
        self.items = items or []
        self.refresh_options()

    def refresh_options(self):
        if not self.list_container:
            return
        # remember existing selections
        current_selected = set(self.selected_ids)
        self.list_container.remove_children()
        filter_value = self.filter_text.lower()
        for item in self.items or []:  # layers
            for fixture in item.fixtures or []:
                url = None
                for network in getattr(
                    fixture, "addresses", SimpleNamespace(networks=[])
                ).networks:
                    if network.ipv4 is not None:
                        url = network.ipv4
                        break
                name = getattr(fixture, "name", "") or ""
                layer_name = getattr(item, "layer", SimpleNamespace(name="")).name or ""
                key = (fixture.uuid or name or url or layer_name or "").strip()
                search_blob = " ".join(
                    str(part)
                    for part in [
                        name,
                        url or "",
                        getattr(fixture, "uuid", "") or "",
                        layer_name,
                    ]
                    if part
                ).lower()
                if filter_value and filter_value not in search_blob:
                    continue
                label = f"{name}{f' {url}' if url else ''}"
                checkbox = Checkbox(label, value=key in current_selected)
                checkbox.data = key
                checkbox.add_class("mvr-fixture-option")
                self.list_container.mount(checkbox)
        self.selected_ids = current_selected

    @on(Input.Changed, "#mvr_fixture_filter")
    def on_filter_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value or ""
        self.refresh_options()

    @on(Checkbox.Changed)
    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if not event.checkbox.has_class("mvr-fixture-option"):
            return
        key = getattr(event.checkbox, "data", "")
        if event.value:
            self.selected_ids.add(key)
        else:
            self.selected_ids.discard(key)

    async def on_key(self, event: events.Key) -> None:
        if event.key not in ("up", "down"):
            return
        await self._move_focus(-1 if event.key == "up" else 1)
        event.stop()

    async def _move_focus(self, delta: int) -> None:
        if not self.list_container:
            return
        checkboxes = list(self.list_container.query("Checkbox"))
        if not checkboxes:
            return
        current_index = next((i for i, cb in enumerate(checkboxes) if cb.has_focus), -1)
        if current_index == -1:
            target = 0 if delta > 0 else len(checkboxes) - 1
        else:
            target = max(0, min(len(checkboxes) - 1, current_index + delta))
        checkboxes[target].focus()


class KumaFixtureListDisplay(Vertical):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = []
        self.filter_text = ""
        self.list_container: VerticalScroll | None = None
        self.selected_ids: set[str] = set()

    def compose(self) -> ComposeResult:
        with Horizontal(id="kuma_filter_row"):
            yield Input(
                placeholder="Filter by name or IP",
                id="kuma_fixture_filter",
            )
            yield Button(
                "Tags",
                id="apply_kuma_tags",
                classes="filter_button",
                disabled=True,
            )
        self.list_container = VerticalScroll(id="kuma_fixture_list")
        self.list_container.can_focus = False  # keep focus on checkboxes
        yield self.list_container

    def update_items(self, items: list):
        self.items = items or []
        self.refresh_options()

    def refresh_options(self):
        if not self.list_container:
            return
        current_selected = set(self.selected_ids)
        new_selected: set[str] = set()
        self.list_container.remove_children()
        filter_value = self.filter_text.lower()
        for fixture in self.items:
            name = fixture.name
            uuid = fixture.uuid
            if not uuid:
                continue
            tags = fixture.tags
            if filter_value and filter_value.lower() not in name.lower():
                continue
            label = f"{name} {tags}" if tags else name
            key = str(uuid)
            is_selected = key in current_selected
            if is_selected:
                new_selected.add(key)
            checkbox = Checkbox(label, value=is_selected)
            checkbox.data = key
            checkbox.add_class("kuma-fixture-option")
            self.list_container.mount(checkbox)
        self.selected_ids = new_selected
        self.update_filter_button_state()

    @on(Input.Changed, "#kuma_fixture_filter")
    def on_filter_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value or ""
        self.refresh_options()

    @on(Button.Pressed, "#apply_kuma_tags")
    def on_filter_button(self, event: Button.Pressed) -> None:
        self.filter_text = self.query_one("#kuma_fixture_filter", Input).value or ""
        self.refresh_options()
        self.app.open_edit_kuma_tags_modal(selected_fixture_ids=list(self.selected_ids))

    @on(Checkbox.Changed)
    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if not event.checkbox.has_class("kuma-fixture-option"):
            return
        key = getattr(event.checkbox, "data", "")
        print(f"{key=}")
        if event.value:
            self.selected_ids.add(key)
        else:
            self.selected_ids.discard(key)
        self.update_filter_button_state()

    def update_filter_button_state(self):
        try:
            btn = self.query_one("#apply_kuma_tags", Button)
            btn.disabled = len(self.selected_ids) == 0
        except Exception:
            pass

    async def on_key(self, event: events.Key) -> None:
        if event.key not in ("up", "down"):
            return
        await self._move_focus(-1 if event.key == "up" else 1)
        event.stop()

    async def _move_focus(self, delta: int) -> None:
        if not self.list_container:
            return
        checkboxes = list(self.list_container.query("Checkbox"))
        if not checkboxes:
            return
        current_index = next((i for i, cb in enumerate(checkboxes) if cb.has_focus), -1)
        if current_index == -1:
            target = 0 if delta > 0 else len(checkboxes) - 1
        else:
            target = max(0, min(len(checkboxes) - 1, current_index + delta))
        checkboxes[target].focus()


class MonitorsFetched(Message):
    """Message sent when monitors are fetched from the API."""

    def __init__(self, monitors: list | None = None) -> None:
        self.monitors = monitors
        super().__init__()


class TagsFetched(Message):
    """Message sent when monitors are fetched from the API."""

    def __init__(self, tags: list | None = None, error: str | None = None) -> None:
        self.tags = tags
        super().__init__()


class MVRtoKuma(App):
    """A Textual app to manage Uptime Kuma MVR."""

    CSS_PATH = [
        "app.css",
        "quit_screen.css",
        "config_screen.css",
        "delete_screen.css",
        "add_monitors_screen.css",
        "mvr_screen.css",
        "mvr_merge_screen.css",
        "artnet_screen.css",
        "add_mvr_tag_screen.css",
        "edit_tags_screen.css",
    ]
    BINDINGS = [
        ("left", "focus_previous", "Focus Previous"),
        ("right", "focus_next", "Focus Next"),
        ("up", "focus_previous", "Focus Previous"),
        ("down", "focus_next", "Focus Next"),
    ]
    HORIZONTAL_BREAKPOINTS = [
        (0, "-narrow"),
        (40, "-normal"),
        (80, "-wide"),
        (120, "-very-wide"),
    ]

    CONFIG_FILE = "config.json"
    url: str = ""
    username: str = ""
    password: str = ""
    timeout: str = "1"
    artnet_timeout: str = "2"
    details_toggle: bool = False
    singleline_ui_toggle: bool = True

    kuma_fixtures = []
    kuma_tags = []
    tags = []
    kuma_tag_filter: str = ""
    selected_kuma_tags: set[str] = set()
    mvr_fixtures = []
    mvr_classes = []
    mvr_positions = []
    layers_toggle = True
    classes_toggle = True
    positions_toggle = True

    def is_in_classes(self, name):
        for cl in self.mvr_classes:
            if cl.name == name:
                return cl.uuid
        return None

    def is_in_positions(self, name):
        for cl in self.mvr_positions:
            if cl.name == name:
                return cl.uuid
        return None

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        with Vertical(id="all_around"):
            with Vertical(id="json_output_container"):
                yield Static(
                    "Ready... make sure to Configure Uptime Kuma address and credentials",
                    id="json_output",
                )
                with Horizontal():
                    with Vertical(id="left"):
                        with Grid(id="mvr_header"):
                            yield Static("[b]MVR data:[/b]")
                        self.mvr_tag_display = ListDisplay()
                        yield self.mvr_tag_display
                        self.mvr_fixtures_display = DictListDisplay()
                        yield self.mvr_fixtures_display
                    with Vertical(id="right"):
                        with Grid(id="kuma_header"):
                            yield Input(
                                placeholder="Filter tags",
                                id="kuma_tag_filter",
                            )
                            yield Button(
                                "Add Tag",
                                id="add_kuma_tag",
                                classes="small_button tag_header_button",
                            )
                        self.kuma_tag_display = ListDisplay()
                        yield self.kuma_tag_display
                        self.kuma_fixtures_display = KumaFixtureListDisplay()
                        yield self.kuma_fixtures_display

            with Grid(id="action_buttons"):
                yield Button(
                    "Get Server Data",
                    id="get_button",
                    classes="small_button",
                    disabled=True,
                )
                yield Button(
                    "Add Monitors",
                    id="open_create_monitors",
                    disabled=True,
                    classes="small_button",
                )
                yield Button("MVR Files", id="mvr_screen", classes="small_button")
                yield Button(
                    "Delete", id="delete_screen", disabled=True, classes="small_button"
                )
                yield Button("Configure", id="configure_button", classes="small_button")
                yield Button("Quit", variant="error", id="quit", classes="small_button")

    def on_mount(self) -> None:
        """Load the configuration from the JSON file when the app starts."""
        if os.path.exists(self.CONFIG_FILE):
            with open(self.CONFIG_FILE, "r") as f:
                try:
                    data = json.load(f)
                    self.url = data.get("url", "")
                    self.username = data.get("username", "")
                    self.password = data.get("password", "")
                    self.timeout = data.get("timeout", "1")
                    self.artnet_timeout = data.get("artnet_timeout", "2")
                    self.layers_toggle = data.get("layers", False)
                    self.classes_toggle = data.get("classes", False)
                    self.positions_toggle = data.get("positions", False)
                    self.details_toggle = data.get("details_toggle", False)
                    self.singleline_ui_toggle = data.get("singleline_ui_toggle", True)

                    if self.singleline_ui_toggle:
                        for button in self.query("Button"):
                            button.remove_class("big_button")
                            button.add_class("small_button")
                            button.refresh(layout=True)  # Force refresh if needed
                    else:
                        for button in self.query("Button"):
                            button.remove_class("small_button")
                            button.add_class("big_button")
                            button.refresh(layout=True)  # Force refresh if needed
                    self.query_one("#json_output").update(
                        f"{f'Configuration loaded, Server: [blue]{self.url}[/blue]' if self.url else 'Ready... make sure to Configure Uptime Kuma address and credentials'}"
                    )
                    self.enable_buttons()

                except json.JSONDecodeError:
                    # Handle empty or invalid JSON file
                    pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Called when a button is pressed."""

        if event.button.id == "open_create_monitors":
            self.run_api_create_tags()
            self.disable_buttons()

            def set_config(data: dict) -> None:
                print("setting data", data)
                if data:
                    self.classes_toggle = data.get("classes", True)
                    self.layers_toggle = data.get("layers", True)
                    self.positions_toggle = data.get("positions", True)

            self.push_screen(
                AddMonitorsScreen(
                    data={
                        "layers": self.layers_toggle,
                        "classes": self.classes_toggle,
                        "positions": self.positions_toggle,
                    }
                ),
                set_config,
            )

        if event.button.id == "mvr_screen":
            self.push_screen(MVRScreen())

        if event.button.id == "delete_screen":
            self.push_screen(DeleteScreen())

        if event.button.id == "delete_tags":
            self.query_one("#json_output").update(
                "Calling API via script, adding monitors..."
            )
            self.run_api_delete_tags()
            self.disable_buttons()

        if event.button.id == "add_kuma_tag":
            self.open_add_kuma_tag_modal()

        if event.button.id == "get_button":
            self.query_one("#json_output").update("Calling API via script...")
            self.run_api_get_data()
            self.disable_buttons()

        if event.button.id == "configure_button":
            current_config = {
                "url": self.url,
                "username": self.username,
                "password": self.password,
                "timeout": self.timeout,
                "artnet_timeout": self.artnet_timeout,
                "details_toggle": self.details_toggle,
                "singleline_ui_toggle": self.singleline_ui_toggle,
            }

            def save_config(data: dict) -> None:
                """Called with the result of the configuration dialog."""
                if data:
                    self.url = data.get("url", "")
                    self.username = data.get("username", "")
                    self.password = data.get("password", "")
                    self.timeout = data.get("timeout", "1")
                    self.artnet_timeout = data.get("artnet_timeout", "2")
                    self.details_toggle = data.get("details_toggle", False)
                    self.singleline_ui_toggle = data.get("singleline_ui_toggle", True)
                    self.action_save_config()
                    self.notify("Configuration saved.", timeout=1)
                    self.query_one("#json_output").update(
                        f"{f'Configuration loaded, Server: [blue]{self.url}[/blue]' if self.url else 'Ready... make sure to Configure Uptime Kuma address and credentials'}"
                    )

                    self.update_mvr_tag_display()
                    self.mvr_fixtures_display.update_items(self.mvr_fixtures)
                    self.kuma_fixtures_display.update_items(self.kuma_fixtures)
                    self.kuma_tag_display.update_items(self.kuma_tags)

                    if not data.get("singleline_ui_toggle", False):
                        for button in self.query("Button"):
                            button.remove_class("small_button")
                            button.add_class("big_button")
                            button.refresh(layout=True)  # Force refresh if needed
                    else:
                        for button in self.query("Button"):
                            button.remove_class("big_button")
                            button.add_class("small_button")
                            button.refresh(layout=True)  # Force refresh if needed

                    self.enable_buttons()

            self.push_screen(ConfigScreen(data=current_config), save_config)

        if event.button.id == "quit":

            def check_quit(quit_confirmed: bool) -> None:
                """Called with the result of the quit dialog."""
                if quit_confirmed:
                    self.action_quit()

            self.push_screen(QuitScreen(), check_quit)

    @work(thread=True)
    async def run_import_mvr(self, filename) -> str:
        try:
            mvr_fixtures, mvr_tags = get_fixtures(filename)
            self.post_message(MvrParsed(fixtures=mvr_fixtures, tags=mvr_tags))
        except Exception as e:
            self.post_message(Errors(error=str(e)))

    def on_monitors_fetched(self, message: MonitorsFetched) -> None:
        # output_widget = self.query_one("#json_output", Static)
        # self.query_one("#get_button", Button).disabled = False

        # formatted = json.dumps(message.monitors, indent=2)
        # output_widget.update(f"[green]Monitors Fetched:[/green]\n{formatted}")
        self.kuma_fixtures = [KumaFixture(f) for f in message.monitors]
        # for fixture in self.kuma_fixtures:
        #    print(fixture)

        self.kuma_fixtures_display.update_items(self.kuma_fixtures)
        self.enable_buttons()

    def on_tags_fetched(self, message: TagsFetched) -> None:
        # output_widget = self.query_one("#json_output", Static)
        # self.query_one("#get_button", Button).disabled = False

        # formatted = json.dumps(message.tags, indent=2)
        # output_widget.update(f"[green]Tags Fetched:[/green]\n{formatted}")
        self.kuma_tags = [KumaTag(t) for t in message.tags]
        # reset filter on fresh fetch
        try:
            self.kuma_tag_filter = self.query_one("#kuma_tag_filter", Input).value
        except Exception:
            self.kuma_tag_filter = ""
        self.update_kuma_tag_display()
        self.enable_buttons()

    def on_mvr_parsed(self, message: MvrParsed) -> None:
        # output_widget = self.query_one("#json_output", Static)
        # self.query_one("#get_button", Button).disabled = False

        self.mvr_fixtures += message.fixtures
        self.mvr_classes += message.tags["classes"]
        self.mvr_positions += message.tags["positions"]

        self.update_mvr_tag_display()
        self.mvr_fixtures_display.update_items(self.mvr_fixtures)
        self.query_one("#json_output").update("[green]MVR data imported[/green]")
        self.enable_buttons()

    def on_errors(self, message: Errors) -> None:
        output_widget = self.query_one("#json_output", Static)

        if message.error:
            output_widget.update(f"[red]Error:[/red] {message.error}")

    def update_mvr_tag_display(self):
        """Refresh stored tags and update UI."""
        self.tags = (
            self.mvr_positions
            + self.mvr_classes
            + [layer.layer for layer in self.mvr_fixtures]
        )
        self.mvr_tag_display.update_items(self.tags)

    def update_kuma_tag_display(self):
        """Refresh Kuma tags with current filter."""
        if not self.kuma_tags:
            self.kuma_tag_display.update_items([])
            return
        filter_value = (self.kuma_tag_filter or "").lower()
        filtered = []
        for tag in self.kuma_tags:
            name = getattr(tag, "name", "") or ""
            uuid = getattr(tag, "uuid", "") or ""
            haystack = f"{name} {uuid}".lower()
            if filter_value and filter_value not in haystack:
                continue
            filtered.append(tag)
        self.kuma_tag_display.update_items(filtered)
        try:
            btn = self.query_one("#apply_kuma_tags", Button)
            btn.disabled = False
        except Exception:
            pass

    @on(Input.Changed, "#kuma_tag_filter")
    def on_kuma_tag_filter_changed(self, event: Input.Changed) -> None:
        self.kuma_tag_filter = event.value or ""
        self.update_kuma_tag_display()

    @work(thread=True)
    async def run_api_get_data(self) -> str:
        # Safe to call blocking code here
        api = None
        try:
            api = UptimeKumaApi(self.url, timeout=int(self.timeout))
            api.login(self.username, self.password)
        except Exception as e:
            self.post_message(Errors(error=str(e)))

        if not api:
            self.post_message(Errors(error="Not logged in"))
            return
        try:
            monitors = api.get_monitors()
            print(monitors)
            # You can now emit a message or update reactive variables
            self.post_message(MonitorsFetched(monitors=monitors))
        except Exception as e:
            self.post_message(Errors(error=str(e)))

        try:
            print("get tags")
            tags = api.get_tags()
            print("get tags", tags)
            # You can now emit a message or update reactive variables
            self.post_message(TagsFetched(tags=tags))
        except Exception as e:
            self.post_message(Errors(error=str(e)))
        finally:
            api.disconnect()

    @work(thread=True)
    async def run_api_delete_tags(self, mvr=False) -> str:
        # Safe to call blocking code here
        api = None
        try:
            api = UptimeKumaApi(self.url, timeout=int(self.timeout))
            api.login(self.username, self.password)
        except Exception as e:
            traceback.print_exception(e)
            self.post_message(Errors(error=str(e)))

        if not api:
            self.post_message(Errors(error="Not logged in"))
            return
        try:
            for tag in self.kuma_tags:
                delete = False
                if mvr:
                    for mvr_tag in (
                        self.mvr_classes
                        + self.mvr_positions
                        + [layer.layer for layer in self.mvr_fixtures]
                    ):  # class or layer
                        if tag.name == mvr_tag.name:
                            delete = True
                else:
                    delete = True
                if delete:
                    print("Delete", tag.id)
                    api.delete_tag(tag.id)

        except Exception as e:
            traceback.print_exception(e)
            print("error!!!!!", traceback.print_exception(e))
            self.post_message(Errors(error=str(e)))
        finally:
            api.disconnect()

    @work(thread=True)
    async def run_api_delete_monitors(self, mvr=False) -> str:
        # Safe to call blocking code here
        api = None
        try:
            api = UptimeKumaApi(self.url, timeout=int(self.timeout))
            api.login(self.username, self.password)
        except Exception as e:
            traceback.print_exception(e)
            print("error!!!!!", traceback.print_exception(e))
            self.post_message(Errors(error=str(e)))

        if not api:
            self.post_message(Errors(error="Not logged in"))
            return
        try:
            for monitor in self.kuma_fixtures:
                delete = False
                if mvr:
                    for layer in self.mvr_fixtures:
                        for fixture in layer.fixtures:
                            if fixture.uuid == monitor.uuid:
                                delete = True
                else:
                    delete = True
                if delete:
                    api.delete_monitor(monitor.id)

        except Exception as e:
            traceback.print_exception(e)
            print("error!!!!!", traceback.print_exception(e))
            self.post_message(Errors(error=str(e)))
        finally:
            api.disconnect()

    @work(thread=True)
    async def run_api_create_monitors(self, data) -> str:
        self.classes_toggle = data.get("classes", True)
        self.layers_toggle = data.get("layers", True)
        self.positions_toggle = data.get("positions", True)

        # Safe to call blocking code here
        api = None
        try:
            api = UptimeKumaApi(self.url, timeout=int(self.timeout))
            api.login(self.username, self.password)
        except Exception as e:
            traceback.print_exception(e)
            print("error!!!!!", traceback.print_exception(e))
            self.post_message(Errors(error=str(e)))

        if not api:
            self.post_message(Errors(error="Not logged in"))
            return
        try:
            heartbeat_interval = 60
            retry_interval = 60
            resend_interval = 0
            max_retries = 0

            for layer in self.mvr_fixtures:
                print("debug layer", layer)

                for mvr_fixture in layer.fixtures or []:
                    url = None
                    for network in mvr_fixture.addresses.networks:
                        if network.ipv4 is not None:
                            url = network.ipv4
                            break
                    if url is None:
                        continue

                    monitor_id = None
                    monitor_tags = []
                    add_monitor = True
                    add_tag = None
                    for kuma_fixture in self.kuma_fixtures:
                        # print(f"{kuma_fixture.name=} {mvr_fixture=}")
                        if mvr_fixture.uuid == kuma_fixture.uuid:
                            add_monitor = False
                            monitor_id = kuma_fixture.id
                            monitor_tags = kuma_fixture.tags
                            print("Monitor already exists", monitor_id, monitor_tags)
                            break
                    if add_monitor:
                        print("Add new monitor")
                        result = api.add_monitor(
                            type=MonitorType.PING,
                            name=mvr_fixture.name,
                            hostname=url,
                            url=f"http://{url}",
                            description=mvr_fixture.uuid,
                            interval=heartbeat_interval,
                            retryInterval=retry_interval,
                            resendInterval=resend_interval,
                            maxretries=max_retries,
                        )

                        monitor_id = result.get("monitorID", None)
                    if monitor_id is not None:
                        for kuma_tag in self.kuma_tags:
                            if self.layers_toggle:  # add layers tag
                                if kuma_tag.name == layer.layer.name:
                                    if kuma_tag.name not in monitor_tags:
                                        print(
                                            f"{monitor_id=}, {kuma_tag.id=}, {kuma_tag.name=}, {monitor_tags=}"
                                        )
                                        monitor_tags.append(kuma_tag.name)
                                        add_tag = kuma_tag.id

                                if add_tag:
                                    try:
                                        print("add layer", kuma_tag.name)
                                        api.add_monitor_tag(
                                            monitor_id=monitor_id,
                                            tag_id=kuma_tag.id,
                                        )
                                        add_tag = None
                                    except Exception as e:
                                        print(e)

                        add_tag = None
                        for kuma_tag in self.kuma_tags:
                            if self.positions_toggle:
                                uuid = self.is_in_positions(kuma_tag.name)
                                if uuid == mvr_fixture.position:
                                    if kuma_tag.name not in monitor_tags:
                                        print(
                                            f"{monitor_id=}, {kuma_tag.id=}, {kuma_tag.name=}, {monitor_tags=}"
                                        )
                                        monitor_tags.append(kuma_tag.name)
                                        add_tag = kuma_tag.id

                                if add_tag:
                                    try:
                                        print("add position", kuma_tag.name)
                                        api.add_monitor_tag(
                                            monitor_id=monitor_id,
                                            tag_id=kuma_tag.id,
                                        )
                                        add_tag = None
                                    except Exception as e:
                                        print(e)

                        add_tag = None
                        for kuma_tag in self.kuma_tags:
                            if self.classes_toggle:
                                uuid = self.is_in_classes(kuma_tag.name)
                                if uuid == mvr_fixture.classing:
                                    if kuma_tag.name not in monitor_tags:
                                        print(
                                            f"{monitor_id=}, {kuma_tag.id=}, {kuma_tag.name=}, {monitor_tags=}"
                                        )
                                        monitor_tags.append(kuma_tag.name)
                                        add_tag = kuma_tag.id

                                if add_tag:
                                    try:
                                        print("add class", kuma_tag.name)
                                        api.add_monitor_tag(
                                            monitor_id=monitor_id,
                                            tag_id=kuma_tag.id,
                                        )
                                        add_tag = None
                                    except Exception as e:
                                        print(e)

        except Exception as e:
            traceback.print_exception(e)
            print("error!!!!!", traceback.print_exception(e))
            self.post_message(Errors(error=str(e)))
        finally:
            if api:
                api.disconnect()

    @work(thread=True)
    async def run_api_add_tags_to_monitors(
        self, monitors: [KumaFixture], tags: [KumaTag]
    ):
        # Safe to call blocking code here
        api = None
        try:
            api = UptimeKumaApi(self.url, timeout=int(self.timeout))
            api.login(self.username, self.password)
        except Exception as e:
            print("error!!!!!", traceback.print_exception(e))
            self.post_message(Errors(error=str(e)))

        if not api:
            self.post_message(Errors(error="Not logged in"))
            return
        selected_tag_names = [tag.name for tag in tags]
        for monitor in monitors:
            print(f"{monitor.id=}, {monitor.tags=}")
            for tag in tags:
                print(f"{tag.id=}, {tag=}")
                if tag.name in monitor.tags:
                    print(f"skip {tag.id}")
                    continue
                try:
                    api.add_monitor_tag(
                        monitor_id=monitor.id,
                        tag_id=tag.id,
                    )
                except Exception as e:
                    print("error!!!!!", traceback.print_exception(e))
            for tag_name in monitor.tags:
                if tag_name not in selected_tag_names:
                    for t in self.kuma_tags:
                        if t.name == tag_name:
                            try:
                                api.delete_monitor_tag(
                                    monitor_id=monitor.id, tag_id=t.id
                                )
                            except exception as e:
                                print("error!!!!!", traceback.print_exception(e))

        if api:
            api.disconnect()

    @work(thread=True)
    async def run_api_create_tag(self, tag: KumaTag):
        # Safe to call blocking code here
        api = None
        try:
            api = UptimeKumaApi(self.url, timeout=int(self.timeout))
            api.login(self.username, self.password)
        except Exception as e:
            print("error!!!!!", traceback.print_exception(e))
            self.post_message(Errors(error=str(e)))

        if not api:
            self.post_message(Errors(error="Not logged in"))
            return
        try:
            api.add_tag(
                name=tag.name,
                color="#{:06x}".format(random.randint(0, 0xFFFFFF)),
            )
        except Exception as e:
            print("error!!!!!", traceback.print_exception(e))
            self.post_message(Errors(error=str(e)))
        finally:
            if api:
                api.disconnect()

    @work(thread=True)
    async def run_api_create_tags(self) -> str:
        # Safe to call blocking code here
        api = None
        try:
            api = UptimeKumaApi(self.url, timeout=int(self.timeout))
            api.login(self.username, self.password)
        except Exception as e:
            print("error!!!!!", traceback.print_exception(e))
            self.post_message(Errors(error=str(e)))

        if not api:
            self.post_message(Errors(error="Not logged in"))
            return
        try:
            for tag in (
                self.mvr_classes
                + self.mvr_positions
                + [layer.layer for layer in self.mvr_fixtures]
            ):
                add = True
                for kuma_tag in self.kuma_tags:
                    print(f"{kuma_tag.name=} {tag=}")
                    if tag.name == kuma_tag.name or tag.uuid == kuma_tag.uuid:
                        add = False
                if add:
                    api.add_tag(
                        name=tag.name,
                        color="#{:06x}".format(random.randint(0, 0xFFFFFF)),
                    )
        except Exception as e:
            print("error!!!!!", traceback.print_exception(e))
            self.post_message(Errors(error=str(e)))
        finally:
            if api:
                api.disconnect()

    def action_save_config(self) -> None:
        """Save the configuration to the JSON file."""
        data = {
            "url": self.url,
            "username": self.username,
            "password": self.password,
            "timeout": self.timeout,
            "artnet_timeout": self.artnet_timeout,
            "layers": self.layers_toggle,
            "classes": self.classes_toggle,
            "positions": self.positions_toggle,
            "details_toggle": self.details_toggle,
            "singleline_ui_toggle": self.singleline_ui_toggle,
        }
        with open(self.CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=4)

    def action_quit(self) -> None:
        """Save the configuration to the JSON file when the app closes."""
        self.action_save_config()
        self.exit()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Called when the worker state changes."""
        if event.worker.name in [
            "run_api_delete_tags",
            "run_api_add_tags_to_monitors",
            "run_api_create_tags",
            "run_api_create_tag",
            "run_api_create_monitors",
            "run_api_delete_monitors",
        ]:
            if event.worker.is_finished:
                self.run_api_get_data()

        if event.worker.name == "run_api_get_data":
            if event.worker.is_finished:
                self.query_one("#json_output").update("Server data refreshed")
                self.enable_buttons()

    def disable_buttons(self):
        self.query_one("#get_button").disabled = True
        self.query_one("#open_create_monitors").disabled = True
        self.query_one("#delete_screen").disabled = True

    def enable_buttons(self):
        if self.username and self.password:
            self.query_one("#get_button").disabled = False
            if self.mvr_fixtures:
                self.query_one("#open_create_monitors").disabled = False
            self.query_one("#delete_screen").disabled = False
        else:
            self.query_one("#get_button").disabled = True
            self.query_one("#open_create_monitors").disabled = True
            self.query_one("#delete_screen").disabled = True

    def open_add_kuma_tag_modal(self):
        def add_tag(data: dict) -> None:
            if data and data.get("name"):
                tag = KumaTag({"name": data["name"]})
                self.run_api_create_tag(tag)

        self.push_screen(AddTagScreen(), add_tag)

    def open_edit_kuma_tags_modal(self, selected_fixture_ids: list[str] | None = None):
        """Open tag editor for the currently selected fixtures."""
        selected_fixture_ids = selected_fixture_ids or []
        if not selected_fixture_ids:
            return

        gathered_tag_names = set()
        for fixture in self.kuma_fixtures:
            uuid = fixture.uuid
            if not uuid:
                continue
            if uuid in selected_fixture_ids:
                gathered_tag_names.update(fixture.tags)
        initial_selected_tags = sorted(gathered_tag_names)

        def save_selection(data: dict) -> None:
            is_exit = data.get("exit", True)
            if is_exit:
                return
            selected_tags = set(data.get("selected", [])) if data else set()
            fixtures_to_update = [
                fixture
                for fixture in self.kuma_fixtures
                if fixture.uuid in selected_fixture_ids
            ]
            tags_to_use = [tag for tag in self.kuma_tags if tag.name in selected_tags]
            self.run_api_add_tags_to_monitors(fixtures_to_update, tags_to_use)
        
        print("LLL", initial_selected_tags)
        self.push_screen(
            EditTagsScreen(
                data={
                    "tags": self.kuma_tags,
                    "selected": initial_selected_tags,
                }
            ),
            save_selection,
        )


if __name__ == "__main__":
    app = MVRtoKuma()
    app.run()
