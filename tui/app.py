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
from types import SimpleNamespace
from textual.app import App, ComposeResult
from textual import on, work
from textual.containers import Horizontal, Vertical, VerticalScroll, Grid
from textual.widgets import Header, Footer, Input, Button, Static
from textual.worker import Worker, WorkerState
from tui.screens import (
    MVRScreen,
    QuitScreen,
    ConfigScreen,
    DeleteScreen,
    AddMonitorsScreen,
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
    def update_items(self, items: list):
        self.remove_children()
        for item in items:  # layers
            for fixture in item.fixtures:
                if self.app.details_toggle:
                    self.mount(Static(f"[green]{fixture.name}[/green] {fixture.uuid}"))
                else:
                    self.mount(Static(f"[green]{fixture.name}[/green]"))


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
    details_toggle: bool = False
    singleline_ui_toggle: bool = True

    kuma_fixtures = []
    kuma_tags = []
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
                        yield Static("[b]MVR data:[/b]")
                        self.mvr_tag_display = ListDisplay()
                        yield self.mvr_tag_display
                        self.mvr_fixtures_display = DictListDisplay()
                        yield self.mvr_fixtures_display
                    with Vertical(id="right"):
                        yield Static("[b]Uptime Kuma data:[/b]")
                        self.kuma_tag_display = ListDisplay()
                        yield self.kuma_tag_display
                        self.kuma_fixtures_display = ListDisplay()
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
                    self.details_toggle = data.get("details_toggle", False)
                    self.singleline_ui_toggle = data.get("singleline_ui_toggle", True)
                    self.action_save_config()
                    self.notify("Configuration saved.", timeout=1)
                    self.query_one("#json_output").update(
                        f"{f'Configuration loaded, Server: [blue]{self.url}[/blue]' if self.url else 'Ready... make sure to Configure Uptime Kuma address and credentials'}"
                    )

                    self.mvr_tag_display.update_items(
                        self.mvr_positions
                        + self.mvr_classes
                        + [layer.layer for layer in self.mvr_fixtures]
                    )

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

    def on_tags_fetched(self, message: MonitorsFetched) -> None:
        # output_widget = self.query_one("#json_output", Static)
        # self.query_one("#get_button", Button).disabled = False

        # formatted = json.dumps(message.tags, indent=2)
        # output_widget.update(f"[green]Tags Fetched:[/green]\n{formatted}")
        self.kuma_tag_display.update_items(self.kuma_tags)
        self.enable_buttons()

    def on_mvr_parsed(self, message: MvrParsed) -> None:
        # output_widget = self.query_one("#json_output", Static)
        # self.query_one("#get_button", Button).disabled = False

        self.mvr_fixtures += message.fixtures
        self.mvr_classes += message.tags["classes"]
        self.mvr_positions += message.tags["positions"]

        self.mvr_tag_display.update_items(
            self.mvr_positions
            + self.mvr_classes
            + [layer.layer for layer in self.mvr_fixtures]
        )

        self.mvr_fixtures_display.update_items(self.mvr_fixtures)
        self.query_one("#json_output").update("[green]MVR data imported[/green]")
        self.enable_buttons()

    def on_errors(self, message: Errors) -> None:
        output_widget = self.query_one("#json_output", Static)

        if message.error:
            output_widget.update(f"[red]Error:[/red] {message.error}")

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
                            type=MonitorType.HTTP,
                            name=mvr_fixture.name,
                            url=f"http://{url}",
                            description=mvr_fixture.uuid,
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
            "run_api_create_tags",
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


if __name__ == "__main__":
    app = MVRtoKuma()
    app.run()
